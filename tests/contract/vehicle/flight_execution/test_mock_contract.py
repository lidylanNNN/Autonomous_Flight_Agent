'''MockFlightExecutionBackend contract tests.'''

import asyncio
from datetime import UTC, datetime

import pytest

from flight_agent.components.vehicle.flight_execution import (
    MockExecutionOutcome,
    MockFlightExecutionBackend,
)
from flight_agent.contracts import (
    ApprovedSkillCommand,
    FlightExecutionInterface,
    SkillExecutionStatus,
    WorldState,
)


def make_state() -> WorldState:
    return WorldState(
        state_id='mock-initial', source_timestamp_us=100,
        received_at=datetime(2026, 9, 18, tzinfo=UTC), state_age_ms=0,
        armed=False, landed=True, position_valid=True, home_valid=True, link_healthy=True,
    )


def make_takeoff_command(execution_id: str = 'exec-takeoff') -> ApprovedSkillCommand:
    return ApprovedSkillCommand.model_validate({
        'execution_id': execution_id, 'proposal_id': 'proposal-1',
        'decision_id': 'decision-1', 'skill_name': 'takeoff',
        'arguments': {'target_altitude_m': 12.0}, 'timeout_s': 30.0,
        'approved_state_id': 'mock-initial',
    })


def test_success_is_deterministic_and_transitions_state() -> None:
    asyncio.run(_assert_success_is_deterministic())


async def _assert_success_is_deterministic() -> None:
    command = make_takeoff_command()
    first = MockFlightExecutionBackend(make_state())
    second = MockFlightExecutionBackend(make_state())
    first_result = await first.execute(command)
    second_result = await second.execute(command)
    state = await first.get_world_state()

    assert isinstance(first, FlightExecutionInterface)
    assert first_result == second_result
    assert first_result.status is SkillExecutionStatus.SUCCEEDED
    assert first_result.end_state_id == 'mock-state-1'
    assert state.state_id == 'mock-state-1'
    assert state.position_ned_m is not None
    assert state.position_ned_m.z == -12.0
    assert state.armed is True
    assert state.landed is False


@pytest.mark.parametrize(
    ('outcome', 'status', 'failure_code'),
    [
        (MockExecutionOutcome.REJECTED, SkillExecutionStatus.REJECTED_BY_PX4, 'PX4_REJECTED'),
        (MockExecutionOutcome.TIMED_OUT, SkillExecutionStatus.TIMED_OUT, 'COMMAND_TIMEOUT'),
        (MockExecutionOutcome.NO_PROGRESS, SkillExecutionStatus.FAILED, 'NO_PROGRESS'),
    ],
)
def test_injected_failure_keeps_world_state_unchanged(
    outcome: MockExecutionOutcome, status: SkillExecutionStatus, failure_code: str,
) -> None:
    asyncio.run(_assert_injected_failure(outcome, status, failure_code))


async def _assert_injected_failure(
    outcome: MockExecutionOutcome, status: SkillExecutionStatus, failure_code: str,
) -> None:
    command = make_takeoff_command()
    backend = MockFlightExecutionBackend(make_state(), {command.execution_id: outcome})
    result = await backend.execute(command)

    assert result.status is status
    assert result.failure_code == failure_code
    assert result.end_state_id == 'mock-initial'
    assert await backend.get_world_state() == make_state()
    if outcome is MockExecutionOutcome.TIMED_OUT:
        assert (result.ended_at - result.started_at).total_seconds() == command.timeout_s


def test_cancelled_in_flight_command_is_cancelled() -> None:
    asyncio.run(_assert_cancelled_in_flight_command())


async def _assert_cancelled_in_flight_command() -> None:
    command = make_takeoff_command()
    backend = MockFlightExecutionBackend(make_state())
    task = asyncio.create_task(backend.execute(command))
    await asyncio.sleep(0)
    await backend.cancel(command.execution_id)
    result = await task

    assert result.status is SkillExecutionStatus.CANCELLED
    assert result.failure_code == 'CANCELLED_BY_REQUEST'
    assert await backend.get_world_state() == make_state()
