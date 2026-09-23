'''PX4/ROS2 backend for deterministic Agent Flight Skill execution.'''

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from math import hypot
from typing import Protocol

from flight_agent.contracts import (
    ApprovedSkillCommand,
    GoToArgs,
    HoldArgs,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    TakeoffArgs,
    WorldState,
)
from flight_agent_ros.adapters.px4_command_plan_executor import (
    Px4CommandPlanReceipt,
)
from flight_agent_ros.adapters.px4_command_plan_mapper import (
    Px4CommandPlan,
    Px4CommandPlanMappingError,
    build_px4_auto_loiter_handover_plan,
    build_px4_command_plan,
    build_px4_offboard_mode_plan,
)
from flight_agent_ros.adapters.px4_offboard_setpoint_adapter import PositionSetpointNed
from flight_agent_ros.adapters.px4_vehicle_command_adapter import Px4CommandAckStatus


class _CommandPlanExecutor(Protocol):
    async def execute(
        self, plan: Px4CommandPlan, *, timeout_s: float
    ) -> Px4CommandPlanReceipt: ...

    async def cancel(self, execution_id: str) -> bool: ...


class _OffboardSetpointAdapterProtocol(Protocol):
    @property
    def active(self) -> bool: ...

    def start_position_stream(self, target: PositionSetpointNed) -> None: ...

    def update_position_target(self, target: PositionSetpointNed) -> None: ...

    def stop_position_stream(self) -> bool: ...


class Px4Ros2FlightExecutionBackend:
    '''Execute approved Agent Flight Skills through PX4 and observe completion.'''

    def __init__(
        self,
        state_reader: Callable[[], WorldState],
        plan_executor: _CommandPlanExecutor,
        offboard_adapter: _OffboardSetpointAdapterProtocol | None = None,
        *,
        poll_interval_s: float = 0.1,
        takeoff_altitude_tolerance_m: float = 0.5,
        goto_altitude_tolerance_m: float = 0.5,
        hold_position_tolerance_m: float = 0.5,
        offboard_warmup_s: float = 1.1,
        cancel_handover_timeout_s: float = 2.0,
    ) -> None:
        if poll_interval_s < 0.0 or offboard_warmup_s < 0.0:
            raise ValueError('poll interval and Offboard warmup must be non-negative')
        if min(
            takeoff_altitude_tolerance_m,
            goto_altitude_tolerance_m,
            hold_position_tolerance_m,
            cancel_handover_timeout_s,
        ) <= 0.0:
            raise ValueError('tolerances and cancellation timeout must be positive')
        self._state_reader = state_reader
        self._plan_executor: _CommandPlanExecutor = plan_executor
        self._offboard_adapter = offboard_adapter
        self._poll_interval_s = poll_interval_s
        self._takeoff_tolerance_m = takeoff_altitude_tolerance_m
        self._goto_altitude_tolerance_m = goto_altitude_tolerance_m
        self._hold_position_tolerance_m = hold_position_tolerance_m
        self._offboard_warmup_s = offboard_warmup_s
        self._cancel_handover_timeout_s = cancel_handover_timeout_s
        self._active_execution_ids: set[str] = set()
        self._offboard_execution_skills: dict[str, SkillName] = {}
        self._cancelled_execution_ids: set[str] = set()

    async def get_world_state(self) -> WorldState:
        '''Return the latest strongly typed vehicle-state snapshot.'''

        return self._state_reader()

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''Submit one Agent Flight Skill and wait for its basic terminal state.'''

        loop = asyncio.get_running_loop()
        deadline = loop.time() + command.timeout_s
        started_at = datetime.now(UTC)
        start_state = self._state_reader()
        self._active_execution_ids.add(command.execution_id)
        try:
            if command.skill_name in {SkillName.GOTO, SkillName.HOLD}:
                return await self._execute_offboard(
                    command, deadline, started_at, start_state
                )
            try:
                plan = build_px4_command_plan(command, start_state)
            except Px4CommandPlanMappingError as error:
                return self._result(
                    command, SkillExecutionStatus.FAILED, error.failure_code,
                    None, started_at, start_state, start_state
                )

            receipt = await self._plan_executor.execute(
                plan, timeout_s=max(deadline - loop.time(), 1e-9)
            )
            ack = receipt.terminal_ack
            if not receipt.all_commands_accepted:
                status = {
                    Px4CommandAckStatus.REJECTED: SkillExecutionStatus.REJECTED_BY_PX4,
                    Px4CommandAckStatus.TIMED_OUT: SkillExecutionStatus.TIMED_OUT,
                    Px4CommandAckStatus.CANCELLED: SkillExecutionStatus.CANCELLED,
                }[ack.status]
                return self._result(
                    command, status, ack.failure_code or 'PX4_COMMAND_FAILED',
                    ack.ack_name, started_at, start_state, self._state_reader()
                )

            if (
                command.skill_name in {SkillName.RTL, SkillName.LAND}
                and self._offboard_adapter is not None
                and self._offboard_adapter.active
            ):
                self._offboard_adapter.stop_position_stream()

            return await self._wait_for_completion(
                command, deadline, started_at, start_state, ack.ack_name
            )
        finally:
            self._active_execution_ids.discard(command.execution_id)
            self._cancelled_execution_ids.discard(command.execution_id)

    async def cancel(self, execution_id: str) -> None:
        '''Cancel an active transport wait or state-completion wait.'''

        if execution_id not in self._active_execution_ids:
            return
        offboard_skill_name = self._offboard_execution_skills.get(execution_id)
        self._cancelled_execution_ids.add(execution_id)
        await self._plan_executor.cancel(execution_id)
        if offboard_skill_name is not None:
            await self._handover_from_offboard(
                execution_id, offboard_skill_name, reason='cancel'
            )

    async def _execute_offboard(
        self,
        command: ApprovedSkillCommand,
        deadline: float,
        started_at: datetime,
        start_state: WorldState,
    ) -> SkillResult:
        '''Enter or update Offboard position control and observe its basic terminal state.'''

        offboard_adapter = self._offboard_adapter
        target = self._offboard_target(command, start_state)
        if offboard_adapter is None:
            return self._result(
                command, SkillExecutionStatus.FAILED, 'OFFBOARD_STREAM_UNAVAILABLE',
                None, started_at, start_state, start_state
            )
        if target is None:
            return self._result(
                command, SkillExecutionStatus.FAILED, 'LOCAL_POSITION_UNAVAILABLE',
                None, started_at, start_state, start_state
            )

        self._offboard_execution_skills[command.execution_id] = command.skill_name
        try:
            if offboard_adapter.active:
                offboard_adapter.update_position_target(target)
            else:
                offboard_adapter.start_position_stream(target)
                if self._offboard_warmup_s > 0.0:
                    await asyncio.sleep(self._offboard_warmup_s)

            if command.execution_id in self._cancelled_execution_ids:
                return self._result(
                    command, SkillExecutionStatus.CANCELLED, 'CANCELLED_BY_REQUEST',
                    None, started_at, start_state, self._state_reader()
                )

            ack_name: str | None = None
            if self._state_reader().flight_mode != 'OFFBOARD':
                remaining_s = deadline - asyncio.get_running_loop().time()
                if remaining_s <= 0.0:
                    offboard_adapter.stop_position_stream()
                    return self._result(
                        command, SkillExecutionStatus.TIMED_OUT,
                        'SKILL_TIMEOUT_BUDGET_EXHAUSTED', None,
                        started_at, start_state, self._state_reader()
                    )
                receipt = await self._plan_executor.execute(
                    build_px4_offboard_mode_plan(command),
                    timeout_s=remaining_s,
                )
                ack = receipt.terminal_ack
                ack_name = ack.ack_name
                if not receipt.all_commands_accepted:
                    offboard_adapter.stop_position_stream()
                    return self._ack_failure_result(
                        command, ack.status, ack.failure_code,
                        ack_name, started_at, start_state
                    )

            result = await self._wait_for_completion(
                command, deadline, started_at, start_state, ack_name,
                offboard_target=target,
            )
            if result.status is SkillExecutionStatus.TIMED_OUT:
                await self._handover_from_offboard(
                    command.execution_id, command.skill_name, reason='timeout'
                )
            return result
        finally:
            self._offboard_execution_skills.pop(command.execution_id, None)

    async def _handover_from_offboard(
        self,
        execution_id: str,
        skill_name: SkillName,
        *,
        reason: str,
    ) -> bool:
        '''Enter Auto Loiter before stopping an active Offboard target stream.'''

        receipt = await self._plan_executor.execute(
            build_px4_auto_loiter_handover_plan(
                execution_id, skill_name, reason=reason
            ),
            timeout_s=self._cancel_handover_timeout_s,
        )
        if not receipt.all_commands_accepted or self._offboard_adapter is None:
            return False
        self._offboard_adapter.stop_position_stream()
        return True

    def _offboard_target(
        self, command: ApprovedSkillCommand, start_state: WorldState
    ) -> PositionSetpointNed | None:
        '''Translate GoTo or Hold arguments into one PX4 local-NED target.'''

        if command.skill_name is SkillName.GOTO:
            arguments = command.arguments
            assert isinstance(arguments, GoToArgs)
            return PositionSetpointNed(
                north_m=arguments.north_m,
                east_m=arguments.east_m,
                down_m=-arguments.altitude_m,
            )
        arguments = command.arguments
        assert isinstance(arguments, HoldArgs)
        position = start_state.position_ned_m
        if not start_state.position_valid or position is None:
            return None
        return PositionSetpointNed(
            north_m=position.x,
            east_m=position.y,
            down_m=position.z,
        )

    async def _wait_for_completion(
        self,
        command: ApprovedSkillCommand,
        deadline: float,
        started_at: datetime,
        start_state: WorldState,
        ack_name: str | None,
        offboard_target: PositionSetpointNed | None = None,
    ) -> SkillResult:
        loop = asyncio.get_running_loop()
        rtl_started = False
        hold_reached_at: float | None = None
        state = self._state_reader()
        while True:
            if command.execution_id in self._cancelled_execution_ids:
                return self._result(
                    command, SkillExecutionStatus.CANCELLED, 'CANCELLED_BY_REQUEST',
                    ack_name, started_at, start_state, state
                )
            if offboard_target is None:
                completed, rtl_started = self._px4_mode_completion_reached(
                    command, state, rtl_started
                )
            else:
                completed, hold_reached_at = self._offboard_completion_reached(
                    command, state, offboard_target, loop.time(), hold_reached_at
                )
            if completed:
                return self._result(
                    command, SkillExecutionStatus.SUCCEEDED, None,
                    ack_name, started_at, start_state, state
                )
            if loop.time() >= deadline:
                return self._result(
                    command, SkillExecutionStatus.TIMED_OUT, 'SKILL_STATE_TIMEOUT',
                    ack_name, started_at, start_state, state
                )
            await asyncio.sleep(self._poll_interval_s)
            state = self._state_reader()

    def _px4_mode_completion_reached(
        self, command: ApprovedSkillCommand, state: WorldState, rtl_started: bool
    ) -> tuple[bool, bool]:
        if command.skill_name is SkillName.TAKEOFF:
            arguments = command.arguments
            assert isinstance(arguments, TakeoffArgs)
            altitude_m = -state.position_ned_m.z if state.position_ned_m else None
            completed = bool(
                state.armed and state.landed is False and altitude_m is not None
                and abs(altitude_m - arguments.target_altitude_m)
                <= self._takeoff_tolerance_m
            )
            return completed, rtl_started
        if command.skill_name is SkillName.LAND:
            return state.landed is True and not state.armed, rtl_started
        if command.skill_name is SkillName.RTL:
            rtl_started = rtl_started or state.flight_mode == 'AUTO_RTL'
            return rtl_started and state.landed is True and not state.armed, rtl_started
        raise RuntimeError(
            f'PX4-mode completion is not defined for Skill {command.skill_name}'
        )

    def _offboard_completion_reached(
        self,
        command: ApprovedSkillCommand,
        state: WorldState,
        target: PositionSetpointNed,
        now: float,
        hold_reached_at: float | None,
    ) -> tuple[bool, float | None]:
        '''Check basic GoTo convergence or continuous Hold duration in Offboard mode.'''

        position = state.position_ned_m
        if (
            state.flight_mode != 'OFFBOARD'
            or not state.position_valid
            or position is None
        ):
            return False, None
        horizontal_error_m = hypot(position.x - target.north_m, position.y - target.east_m)
        altitude_error_m = abs(position.z - target.down_m)
        if command.skill_name is SkillName.GOTO:
            arguments = command.arguments
            assert isinstance(arguments, GoToArgs)
            return (
                horizontal_error_m <= arguments.acceptance_radius_m
                and altitude_error_m <= self._goto_altitude_tolerance_m,
                hold_reached_at,
            )

        arguments = command.arguments
        assert isinstance(arguments, HoldArgs)
        inside_tolerance = (
            horizontal_error_m <= self._hold_position_tolerance_m
            and altitude_error_m <= self._hold_position_tolerance_m
        )
        if not inside_tolerance:
            return False, None
        reached_at = hold_reached_at if hold_reached_at is not None else now
        if arguments.duration_s is None:
            return False, reached_at
        return now - reached_at >= arguments.duration_s, reached_at

    def _ack_failure_result(
        self,
        command: ApprovedSkillCommand,
        ack_status: Px4CommandAckStatus,
        failure_code: str | None,
        ack_name: str | None,
        started_at: datetime,
        start_state: WorldState,
    ) -> SkillResult:
        '''Convert a failed PX4 mode ACK into the stable Skill result vocabulary.'''

        status = {
            Px4CommandAckStatus.REJECTED: SkillExecutionStatus.REJECTED_BY_PX4,
            Px4CommandAckStatus.TIMED_OUT: SkillExecutionStatus.TIMED_OUT,
            Px4CommandAckStatus.CANCELLED: SkillExecutionStatus.CANCELLED,
        }[ack_status]
        return self._result(
            command, status, failure_code or 'PX4_COMMAND_FAILED', ack_name,
            started_at, start_state, self._state_reader()
        )

    @staticmethod
    def _result(
        command: ApprovedSkillCommand,
        status: SkillExecutionStatus,
        failure_code: str | None,
        ack_name: str | None,
        started_at: datetime,
        start_state: WorldState,
        end_state: WorldState,
    ) -> SkillResult:
        return SkillResult(
            execution_id=command.execution_id, proposal_id=command.proposal_id,
            skill_name=command.skill_name, status=status, px4_ack=ack_name,
            started_at=started_at, ended_at=datetime.now(UTC),
            start_state_id=start_state.state_id, end_state_id=end_state.state_id,
            failure_code=failure_code,
        )
