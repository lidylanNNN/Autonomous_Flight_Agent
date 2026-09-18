'''Deterministic in-memory flight execution runtime for Agent-level tests.'''

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from flight_agent.contracts import (
    ApprovedSkillCommand,
    GoToArgs,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    TakeoffArgs,
    Vector3,
    WorldState,
)


class MockExecutionOutcome(StrEnum):
    '''Mock runtime 可以按 execution_id 注入的终态。'''

    SUCCEEDED = 'succeeded'
    REJECTED = 'rejected'
    TIMED_OUT = 'timed_out'
    NO_PROGRESS = 'no_progress'
    CANCELLED = 'cancelled'


class MockFlightExecutionRuntime:
    '''用虚拟时钟执行批准命令的确定性飞行器替身。'''

    def __init__(
        self,
        initial_state: WorldState,
        outcomes: Mapping[str, MockExecutionOutcome] | None = None,
        execution_duration_s: float = 1.0,
    ) -> None:
        '''以初始状态、结果注入表和固定成功耗时建立运行时。'''

        if execution_duration_s <= 0.0:
            raise ValueError('execution_duration_s must be positive')
        self._state = initial_state
        self._outcomes = dict(outcomes or {})
        self._execution_duration_s = execution_duration_s
        self._virtual_time = initial_state.received_at
        self._state_sequence = 0
        self._in_flight_execution_ids: set[str] = set()
        self._cancelled_execution_ids: set[str] = set()

    async def get_world_state(self) -> WorldState:
        '''返回虚拟时钟对应的当前飞行器状态。'''

        return self._state

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''执行命令或返回预先注入的失败终态。'''

        started_at = self._virtual_time
        start_state_id = self._state.state_id
        self._in_flight_execution_ids.add(command.execution_id)
        try:
            await asyncio.sleep(0)
            outcome = self._outcome_for(command.execution_id)
            ended_at = self._advance_clock(command, outcome)
            if outcome is MockExecutionOutcome.SUCCEEDED:
                self._state = self._successful_next_state(command)
            return self._result_for(
                command, outcome, started_at, ended_at, start_state_id, self._state.state_id
            )
        finally:
            self._in_flight_execution_ids.discard(command.execution_id)

    async def cancel(self, execution_id: str) -> None:
        '''标记一个在途命令在其下一个协作点取消。'''

        if execution_id in self._in_flight_execution_ids:
            self._cancelled_execution_ids.add(execution_id)

    def _outcome_for(self, execution_id: str) -> MockExecutionOutcome:
        if execution_id in self._cancelled_execution_ids:
            return MockExecutionOutcome.CANCELLED
        return self._outcomes.get(execution_id, MockExecutionOutcome.SUCCEEDED)

    def _advance_clock(
        self, command: ApprovedSkillCommand, outcome: MockExecutionOutcome
    ) -> datetime:
        duration_s = (
            command.timeout_s
            if outcome is MockExecutionOutcome.TIMED_OUT
            else self._execution_duration_s
        )
        self._virtual_time += timedelta(seconds=duration_s)
        return self._virtual_time

    def _successful_next_state(self, command: ApprovedSkillCommand) -> WorldState:
        position = self._state.position_ned_m or Vector3(x=0.0, y=0.0, z=0.0)
        update: dict[str, object] = {
            'state_id': self._next_state_id(),
            'source_timestamp_us': self._state.source_timestamp_us + 1,
            'received_at': self._virtual_time,
            'state_age_ms': 0,
            'last_command_ack': 'ACCEPTED',
        }
        if command.skill_name is SkillName.TAKEOFF:
            arguments = command.arguments
            assert isinstance(arguments, TakeoffArgs)
            update |= {
                'position_ned_m': position.model_copy(update={'z': -arguments.target_altitude_m}),
                'armed': True,
                'landed': False,
                'flight_mode': 'AUTO_TAKEOFF',
            }
        elif command.skill_name is SkillName.GOTO:
            arguments = command.arguments
            assert isinstance(arguments, GoToArgs)
            update |= {
                'position_ned_m': Vector3(
                    x=arguments.north_m, y=arguments.east_m, z=-arguments.altitude_m
                ),
                'flight_mode': 'AUTO_MISSION',
            }
        elif command.skill_name is SkillName.RTL:
            update |= {
                'position_ned_m': position.model_copy(update={'x': 0.0, 'y': 0.0}),
                'flight_mode': 'AUTO_RTL',
            }
        elif command.skill_name is SkillName.LAND:
            update |= {
                'position_ned_m': position.model_copy(update={'z': 0.0}),
                'armed': False,
                'landed': True,
                'flight_mode': 'AUTO_LAND',
            }
        return self._state.model_copy(update=update)

    def _next_state_id(self) -> str:
        self._state_sequence += 1
        return f'mock-state-{self._state_sequence}'

    @staticmethod
    def _result_for(
        command: ApprovedSkillCommand,
        outcome: MockExecutionOutcome,
        started_at: datetime,
        ended_at: datetime,
        start_state_id: str,
        end_state_id: str,
    ) -> SkillResult:
        status, px4_ack, failure_code = {
            MockExecutionOutcome.SUCCEEDED: (SkillExecutionStatus.SUCCEEDED, 'ACCEPTED', None),
            MockExecutionOutcome.REJECTED: (
                SkillExecutionStatus.REJECTED_BY_PX4,
                'DENIED',
                'PX4_REJECTED',
            ),
            MockExecutionOutcome.TIMED_OUT: (SkillExecutionStatus.TIMED_OUT, None, 'COMMAND_TIMEOUT'),
            MockExecutionOutcome.NO_PROGRESS: (SkillExecutionStatus.FAILED, None, 'NO_PROGRESS'),
            MockExecutionOutcome.CANCELLED: (
                SkillExecutionStatus.CANCELLED,
                'CANCELLED',
                'CANCELLED_BY_REQUEST',
            ),
        }[outcome]
        return SkillResult(
            execution_id=command.execution_id,
            proposal_id=command.proposal_id,
            skill_name=command.skill_name,
            status=status,
            px4_ack=px4_ack,
            started_at=started_at,
            ended_at=ended_at,
            start_state_id=start_state_id,
            end_state_id=end_state_id,
            failure_code=failure_code,
        )
