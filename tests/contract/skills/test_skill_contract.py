'''M3 Flight Skill contract tests.'''

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from flight_agent.components.skills import SKILL_REGISTRY, list_skill_specs
from flight_agent.components.vehicle.flight_execution import FlightExecutionInterface
from flight_agent.contracts import (
    ApprovedSkillCommand,
    GoToArgs,
    LandArgs,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    TakeoffArgs,
    WorldState,
    skill_result_duration_s,
)


def make_success_result() -> SkillResult:
    '''构造稳定的成功结果。'''

    started_at = datetime(2026, 9, 18, tzinfo=UTC)
    return SkillResult(
        execution_id='exec-1',
        proposal_id='proposal-1',
        skill_name=SkillName.TAKEOFF,
        status=SkillExecutionStatus.SUCCEEDED,
        px4_ack='ACCEPTED',
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=3),
        start_state_id='state-1',
        end_state_id='state-2',
        failure_code=None,
    )


def test_approved_command_parses_arguments_from_skill_name() -> None:
    '''验证 JSON 输入会恢复为对应 Skill 的强类型参数。'''

    command = ApprovedSkillCommand.model_validate(
        {
            'execution_id': 'exec-1',
            'proposal_id': 'proposal-1',
            'decision_id': 'decision-1',
            'skill_name': 'goto',
            'arguments': {
                'north_m': 10.0,
                'east_m': -2.0,
                'altitude_m': 15.0,
                'acceptance_radius_m': 1.5,
            },
            'timeout_s': 60.0,
            'approved_state_id': 'state-1',
        }
    )

    assert command.skill_name is SkillName.GOTO
    assert isinstance(command.arguments, GoToArgs)
    assert command.arguments.north_m == 10.0


def test_parameterless_skills_keep_distinct_argument_types() -> None:
    '''验证空参数的 Land 不会被误解析成 RTL。'''

    command = ApprovedSkillCommand.model_validate(
        {
            'execution_id': 'exec-2',
            'proposal_id': 'proposal-2',
            'decision_id': 'decision-2',
            'skill_name': 'land',
            'arguments': {},
            'timeout_s': 90.0,
            'approved_state_id': 'state-2',
        }
    )

    assert isinstance(command.arguments, LandArgs)


def test_skill_arguments_reject_wrong_shape_and_unsafe_numbers() -> None:
    '''验证参数模型拒绝错字段、非正高度和无穷数。'''

    with pytest.raises(ValidationError):
        TakeoffArgs(target_altitude_m=0.0)
    with pytest.raises(ValidationError):
        TakeoffArgs(target_altitude_m=float('inf'))
    with pytest.raises(ValidationError):
        ApprovedSkillCommand.model_validate(
            {
                'execution_id': 'exec-1',
                'proposal_id': 'proposal-1',
                'decision_id': 'decision-1',
                'skill_name': 'takeoff',
                'arguments': {'north_m': 10.0},
                'timeout_s': 30.0,
                'approved_state_id': 'state-1',
            }
        )


def test_skill_result_enforces_terminal_status_contract() -> None:
    '''验证成功结果不带失败码，非成功结果必须带失败码。'''

    successful = make_success_result()
    assert skill_result_duration_s(successful) == 3.0

    with pytest.raises(ValidationError, match='must not include failure_code'):
        SkillResult.model_validate(
            make_success_result().model_dump() | {'failure_code': 'UNEXPECTED'}
        )
    with pytest.raises(ValidationError, match='requires failure_code'):
        SkillResult.model_validate(
            make_success_result().model_dump()
            | {'status': SkillExecutionStatus.TIMED_OUT, 'failure_code': None}
        )


def test_skill_result_rejects_reversed_or_naive_timestamps() -> None:
    '''验证结果拒绝倒序时间和无时区时间。'''

    successful = make_success_result()
    with pytest.raises(ValidationError, match='earlier'):
        SkillResult.model_validate(
            successful.model_dump()
            | {'ended_at': successful.started_at - timedelta(milliseconds=1)}
        )
    with pytest.raises(ValidationError, match='timezone'):
        SkillResult.model_validate(
            successful.model_dump()
            | {
                'started_at': datetime(2026, 9, 18),  # noqa: DTZ001
                'ended_at': datetime(2026, 9, 18),  # noqa: DTZ001
            }
        )


def test_registry_contains_exact_v1_skills_with_approval_boundary() -> None:
    '''验证 Registry 稳定暴露五个 V1 Skill 和参数 Schema。'''

    assert tuple(SKILL_REGISTRY) == tuple(SkillName)
    assert tuple(spec.name for spec in list_skill_specs()) == tuple(SkillName)
    assert SKILL_REGISTRY[SkillName.TAKEOFF].args_schema['additionalProperties'] is False
    assert all(
        spec.authority.value == 'safety_approval_required'
        for spec in SKILL_REGISTRY.values()
    )


def test_flight_runtime_protocol_is_structural() -> None:
    '''验证 Mock 与 PX4 Adapter 可通过同一结构化接口接入。'''

    class RuntimeDouble:
        '''仅用于接口结构检查的测试替身。'''

        async def get_world_state(self) -> WorldState:
            '''实现状态读取签名。'''

            raise NotImplementedError

        async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
            '''实现执行签名。'''

            raise NotImplementedError

        async def cancel(self, execution_id: str) -> None:
            '''实现取消签名。'''

            raise NotImplementedError

    assert isinstance(RuntimeDouble(), FlightExecutionInterface)
