'''Tests for the explicit M3 PX4 scripted-flight entrypoint workflow.'''

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from flight_agent_ros.entrypoints.scripted_px4_flight import (
    ScriptedFlightParameters,
    run_scripted_px4_flight,
)

from flight_agent.contracts import (
    ApprovedSkillCommand,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
    WorldState,
)


class RecordingFlightBackend:
    '''Record scripted commands and return configured terminal statuses.'''

    def __init__(self, statuses: list[SkillExecutionStatus]) -> None:
        '''Initialize the fake backend with one status per expected execution.'''

        self._statuses = statuses
        self.commands: list[ApprovedSkillCommand] = []

    async def get_world_state(self) -> WorldState:
        '''Return a new state identity for each script step.'''

        return WorldState(
            state_id=f'state-{len(self.commands)}',
            source_timestamp_us=1,
            received_at=datetime.now(UTC),
            state_age_ms=0,
            position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        )

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''Record a command and return its configured result.'''

        self.commands.append(command)
        status = self._statuses[len(self.commands) - 1]
        now = datetime.now(UTC)
        return SkillResult(
            execution_id=command.execution_id,
            proposal_id=command.proposal_id,
            skill_name=command.skill_name,
            status=status,
            started_at=now,
            ended_at=now,
            start_state_id=command.approved_state_id,
            end_state_id=f'end-{len(self.commands)}',
            failure_code=None if status is SkillExecutionStatus.SUCCEEDED else 'FAILED',
        )

    async def cancel(self, execution_id: str) -> None:
        '''Satisfy the backend protocol; cancellation is not used by this script.'''

        del execution_id


def test_script_runs_all_four_skills_in_order() -> None:
    '''A healthy scripted run emits exactly Takeoff, GoTo, Hold and RTL.'''

    backend = RecordingFlightBackend([SkillExecutionStatus.SUCCEEDED] * 4)

    results = asyncio.run(
        run_scripted_px4_flight(backend, ScriptedFlightParameters())
    )

    assert [command.skill_name for command in backend.commands] == [
        SkillName.TAKEOFF,
        SkillName.GOTO,
        SkillName.HOLD,
        SkillName.RTL,
    ]
    assert [command.approved_state_id for command in backend.commands] == [
        'state-0',
        'state-1',
        'state-2',
        'state-3',
    ]
    assert len(results) == 4


def test_script_stops_after_first_failed_skill() -> None:
    '''A failed Skill prevents later scripted commands from being submitted.'''

    backend = RecordingFlightBackend(
        [SkillExecutionStatus.SUCCEEDED, SkillExecutionStatus.TIMED_OUT]
    )

    results = asyncio.run(
        run_scripted_px4_flight(backend, ScriptedFlightParameters())
    )

    assert [command.skill_name for command in backend.commands] == [
        SkillName.TAKEOFF,
        SkillName.GOTO,
    ]
    assert results[-1].status is SkillExecutionStatus.TIMED_OUT
