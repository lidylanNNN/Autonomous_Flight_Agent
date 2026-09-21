'''PX4/ROS2 backend for deterministic Agent Flight Skill execution.'''

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from flight_agent.contracts import (
    ApprovedSkillCommand,
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
    build_px4_command_plan,
)
from flight_agent_ros.adapters.px4_vehicle_command_adapter import Px4CommandAckStatus


class _CommandPlanExecutor(Protocol):
    async def execute(
        self, plan: Px4CommandPlan, *, timeout_s: float
    ) -> Px4CommandPlanReceipt: ...

    async def cancel(self, execution_id: str) -> bool: ...


class Px4Ros2FlightExecutionBackend:
    '''Execute approved Agent Flight Skills through PX4 and observe completion.'''

    def __init__(
        self,
        state_reader: Callable[[], WorldState],
        plan_executor: _CommandPlanExecutor,
        *,
        poll_interval_s: float = 0.1,
        takeoff_altitude_tolerance_m: float = 0.5,
    ) -> None:
        if poll_interval_s < 0.0 or takeoff_altitude_tolerance_m <= 0.0:
            raise ValueError('poll interval must be non-negative and tolerance positive')
        self._state_reader = state_reader
        self._plan_executor: _CommandPlanExecutor = plan_executor
        self._poll_interval_s = poll_interval_s
        self._takeoff_tolerance_m = takeoff_altitude_tolerance_m
        self._active_execution_ids: set[str] = set()
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
        self._cancelled_execution_ids.add(execution_id)
        await self._plan_executor.cancel(execution_id)

    async def _wait_for_completion(
        self,
        command: ApprovedSkillCommand,
        deadline: float,
        started_at: datetime,
        start_state: WorldState,
        ack_name: str | None,
    ) -> SkillResult:
        loop = asyncio.get_running_loop()
        rtl_started = False
        state = self._state_reader()
        while True:
            if command.execution_id in self._cancelled_execution_ids:
                return self._result(
                    command, SkillExecutionStatus.CANCELLED, 'CANCELLED_BY_REQUEST',
                    ack_name, started_at, start_state, state
                )
            completed, rtl_started = self._px4_mode_completion_reached(
                command, state, rtl_started
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
