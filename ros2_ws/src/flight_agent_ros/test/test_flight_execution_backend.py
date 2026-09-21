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


def make_command(skill_name: str) -> ApprovedSkillCommand:
    arguments = {'target_altitude_m': 10.0} if skill_name == 'takeoff' else {}
    return ApprovedSkillCommand.model_validate({
        'execution_id': f'exec-{skill_name}', 'proposal_id': 'proposal-1',
        'decision_id': 'decision-1', 'skill_name': skill_name,
        'arguments': arguments, 'timeout_s': 0.02,
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

    async def execute(
        self, plan: Px4CommandPlan, *, timeout_s: float
    ) -> Px4CommandPlanReceipt:
        del timeout_s
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
