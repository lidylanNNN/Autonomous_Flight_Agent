'''Tests for Agent Flight Skill execution through PX4/ROS2.'''

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from flight_agent_ros.adapters.flight_execution_backend import (
    Px4Ros2FlightExecutionBackend,
)
from flight_agent_ros.adapters.px4_command_plan_executor import Px4CommandPlanReceipt
from flight_agent_ros.adapters.px4_command_plan_mapper import Px4CommandPlan
from flight_agent_ros.adapters.px4_offboard_setpoint_adapter import PositionSetpointNed
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    Px4CommandAck,
    Px4CommandAckStatus,
)

from flight_agent.contracts import (
    ApprovedSkillCommand,
    GlobalPosition,
    SkillExecutionStatus,
    Vector3,
    WorldState,
)


def make_state(state_id: str, **updates: object) -> WorldState:
    base = WorldState(
        state_id=state_id, source_timestamp_us=1,
        received_at=datetime(2026, 9, 20, tzinfo=UTC), state_age_ms=0,
        position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        armed=False, landed=True, position_valid=True, home_valid=True,
        home_position_wgs84=GlobalPosition(
            latitude_deg=30.0, longitude_deg=120.0, altitude_amsl_m=40.0
        ), link_healthy=True,
    )
    return base.model_copy(update=updates)


def make_command(
    skill_name: str,
    *,
    arguments: dict[str, float | None] | None = None,
    timeout_s: float = 0.02,
) -> ApprovedSkillCommand:
    '''Build one approved command with concise per-Skill defaults.'''

    if arguments is None:
        arguments = {'target_altitude_m': 10.0} if skill_name == 'takeoff' else {}
    return ApprovedSkillCommand.model_validate({
        'execution_id': f'exec-{skill_name}', 'proposal_id': 'proposal-1',
        'decision_id': 'decision-1', 'skill_name': skill_name,
        'arguments': arguments, 'timeout_s': timeout_s,
        'approved_state_id': 'state-start',
    })


class StateSequence:
    def __init__(self, states: list[WorldState]) -> None:
        self._states = states

    def __call__(self) -> WorldState:
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]


class ScriptedPlanExecutor:
    def __init__(self, status: Px4CommandAckStatus) -> None:
        self._status = status
        self.cancelled: list[str] = []
        self.plans: list[Px4CommandPlan] = []

    async def execute(
        self, plan: Px4CommandPlan, *, timeout_s: float
    ) -> Px4CommandPlanReceipt:
        del timeout_s
        self.plans.append(plan)
        execution_id = plan.execution_id
        accepted = self._status is Px4CommandAckStatus.ACCEPTED
        ack = Px4CommandAck(
            execution_id=execution_id, command_id=22, status=self._status,
            ack_name='ACCEPTED' if accepted else 'DENIED',
            failure_code=None if accepted else 'PX4_ACK_DENIED',
        )
        return Px4CommandPlanReceipt(execution_id=execution_id, acknowledgements=(ack,))

    async def cancel(self, execution_id: str) -> bool:
        self.cancelled.append(execution_id)
        return True


class RecordingOffboardSetpointAdapter:
    '''Record Offboard target lifecycle without starting a ROS executor.'''

    def __init__(self) -> None:
        self.active = False
        self.targets: list[PositionSetpointNed] = []
        self.stop_count = 0

    def start_position_stream(self, target: PositionSetpointNed) -> None:
        self.active = True
        self.targets.append(target)

    def update_position_target(self, target: PositionSetpointNed) -> None:
        self.targets.append(target)

    def stop_position_stream(self) -> bool:
        was_active = self.active
        self.active = False
        self.stop_count += 1
        return was_active


@pytest.mark.parametrize(
    ('skill_name', 'states'),
    [
        ('takeoff', [make_state('state-start'), make_state(
            'state-airborne', armed=True, landed=False,
            position_ned_m=Vector3(x=0.0, y=0.0, z=-10.0)
        )]),
        ('land', [make_state('state-start', armed=True, landed=False),
                  make_state('state-landed')]),
        ('rtl', [make_state('state-start', armed=True, landed=False),
                 make_state('state-rtl', armed=True, landed=False, flight_mode='AUTO_RTL'),
                 make_state('state-home', flight_mode='AUTO_RTL')]),
    ],
)
def test_skill_success_requires_px4_vehicle_state(
    skill_name: str, states: list[WorldState]
) -> None:
    async def scenario() -> None:
        backend = Px4Ros2FlightExecutionBackend(
            StateSequence(states), ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED),
            poll_interval_s=0.0,
        )
        result = await backend.execute(make_command(skill_name))
        assert result.status is SkillExecutionStatus.SUCCEEDED
        assert result.end_state_id == states[-1].state_id
        assert result.px4_ack == 'ACCEPTED'

    asyncio.run(scenario())


def test_rejected_ack_is_not_reported_as_skill_success() -> None:
    async def scenario() -> None:
        state = make_state('state-start')
        backend = Px4Ros2FlightExecutionBackend(
            lambda: state, ScriptedPlanExecutor(Px4CommandAckStatus.REJECTED)
        )
        result = await backend.execute(make_command('takeoff'))
        assert result.status is SkillExecutionStatus.REJECTED_BY_PX4
        assert result.failure_code == 'PX4_ACK_DENIED'

    asyncio.run(scenario())


def test_accepted_ack_without_state_progress_times_out() -> None:
    async def scenario() -> None:
        state = make_state('state-start')
        backend = Px4Ros2FlightExecutionBackend(
            lambda: state, ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED),
            poll_interval_s=0.001,
        )
        result = await backend.execute(make_command('takeoff'))
        assert result.status is SkillExecutionStatus.TIMED_OUT
        assert result.failure_code == 'SKILL_STATE_TIMEOUT'

    asyncio.run(scenario())


def test_cancel_stops_state_completion_wait() -> None:
    async def scenario() -> None:
        state = make_state('state-start')
        executor = ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED)
        backend = Px4Ros2FlightExecutionBackend(
            lambda: state, executor, poll_interval_s=0.0
        )
        command = make_command('takeoff')

        task = asyncio.create_task(backend.execute(command))
        await asyncio.sleep(0)
        await backend.cancel(command.execution_id)
        result = await task

        assert result.status is SkillExecutionStatus.CANCELLED
        assert result.failure_code == 'CANCELLED_BY_REQUEST'
        assert executor.cancelled == [command.execution_id]

    asyncio.run(scenario())


def test_goto_enters_offboard_and_waits_for_target_convergence() -> None:
    '''GoTo succeeds only after PX4 reports Offboard at the requested NED point.'''

    async def scenario() -> None:
        start = make_state(
            'state-start', armed=True, landed=False, flight_mode='AUTO_LOITER'
        )
        target = make_state(
            'state-target', armed=True, landed=False, flight_mode='OFFBOARD',
            position_ned_m=Vector3(x=10.0, y=5.0, z=-20.0),
        )
        executor = ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED)
        offboard_adapter = RecordingOffboardSetpointAdapter()
        backend = Px4Ros2FlightExecutionBackend(
            StateSequence([start, start, target]), executor, offboard_adapter,
            poll_interval_s=0.0, offboard_warmup_s=0.0,
        )
        command = make_command(
            'goto',
            arguments={
                'north_m': 10.0, 'east_m': 5.0, 'altitude_m': 20.0,
                'acceptance_radius_m': 1.0,
            },
        )

        result = await backend.execute(command)

        assert result.status is SkillExecutionStatus.SUCCEEDED
        assert offboard_adapter.targets == [PositionSetpointNed(10.0, 5.0, -20.0)]
        assert executor.plans[0].commands[0].parameters.param2 == 6.0

    asyncio.run(scenario())


def test_hold_uses_current_position_and_continuous_dwell() -> None:
    '''Finite Hold keeps the starting point until its dwell duration elapses.'''

    async def scenario() -> None:
        state = make_state(
            'state-hold', armed=True, landed=False, flight_mode='OFFBOARD',
            position_ned_m=Vector3(x=3.0, y=4.0, z=-8.0),
        )
        executor = ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED)
        offboard_adapter = RecordingOffboardSetpointAdapter()
        backend = Px4Ros2FlightExecutionBackend(
            lambda: state, executor, offboard_adapter,
            poll_interval_s=0.001, offboard_warmup_s=0.0,
        )

        result = await backend.execute(make_command(
            'hold', arguments={'duration_s': 0.001}, timeout_s=0.05
        ))

        assert result.status is SkillExecutionStatus.SUCCEEDED
        assert offboard_adapter.targets == [PositionSetpointNed(3.0, 4.0, -8.0)]
        assert executor.plans == []

    asyncio.run(scenario())


def test_cancelling_indefinite_hold_hands_over_to_auto_loiter() -> None:
    '''Cancellation requests PX4 Auto Loiter before stopping the target stream.'''

    async def scenario() -> None:
        state = make_state(
            'state-hold', armed=True, landed=False, flight_mode='OFFBOARD'
        )
        executor = ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED)
        offboard_adapter = RecordingOffboardSetpointAdapter()
        backend = Px4Ros2FlightExecutionBackend(
            lambda: state, executor, offboard_adapter,
            poll_interval_s=0.0, offboard_warmup_s=0.0,
        )
        command = make_command(
            'hold', arguments={'duration_s': None}, timeout_s=0.05
        )

        task = asyncio.create_task(backend.execute(command))
        await asyncio.sleep(0)
        await backend.cancel(command.execution_id)
        result = await task

        assert result.status is SkillExecutionStatus.CANCELLED
        assert executor.plans[-1].execution_id == f'{command.execution_id}:cancel'
        assert executor.plans[-1].commands[0].parameters.param2 == 4.0
        assert executor.plans[-1].commands[0].parameters.param3 == 3.0
        assert offboard_adapter.active is False

    asyncio.run(scenario())


def test_timed_out_goto_hands_over_before_stopping_target_stream() -> None:
    '''A timed-out GoTo must not keep publishing its stale Offboard target.'''

    async def scenario() -> None:
        state = make_state(
            'state-away', armed=True, landed=False, flight_mode='OFFBOARD'
        )
        executor = ScriptedPlanExecutor(Px4CommandAckStatus.ACCEPTED)
        offboard_adapter = RecordingOffboardSetpointAdapter()
        backend = Px4Ros2FlightExecutionBackend(
            lambda: state, executor, offboard_adapter,
            poll_interval_s=0.001, offboard_warmup_s=0.0,
        )
        command = make_command(
            'goto',
            arguments={
                'north_m': 10.0, 'east_m': 5.0, 'altitude_m': 20.0,
                'acceptance_radius_m': 1.0,
            },
            timeout_s=0.002,
        )

        result = await backend.execute(command)

        assert result.status is SkillExecutionStatus.TIMED_OUT
        assert executor.plans[-1].execution_id == f'{command.execution_id}:timeout'
        assert executor.plans[-1].commands[0].parameters.param2 == 4.0
        assert executor.plans[-1].commands[0].parameters.param3 == 3.0
        assert offboard_adapter.active is False

    asyncio.run(scenario())
