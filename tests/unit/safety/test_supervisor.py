'''Safety Supervisor 首批装配测试。'''

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from flight_agent.components.safety import SafetySupervisor
from flight_agent.contracts import (
    ControlAuthority,
    GlobalPosition,
    MissionConstraints,
    MissionContract,
    NedGeofence,
    SafetyDecision,
    SafetyDecisionType,
    SafetyReasonCode,
    SkillName,
    SkillProposal,
    Vector3,
    WorldState,
)

_NOW = datetime(2026, 10, 12, tzinfo=UTC)


class FakeSpeedSource:
    '''在单测中模拟已校验的飞控限速来源。'''

    def __init__(self, value: float = 8.0, error: Exception | None = None) -> None:
        '''配置读数或注入读取错误。'''

        self.value = value
        self.error = error
        self.calls: list[float] = []

    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        '''记录读取时设置的有效期并返回配置结果。'''

        self.calls.append(max_age_s)
        if self.error is not None:
            raise self.error
        return self.value


def make_contract(*, approval_skills: tuple[SkillName, ...] = ()) -> MissionContract:
    '''构造带任务硬约束的合同。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='安全完成任务',
        constraints=MissionConstraints(
            max_altitude_m=50.0,
            max_horizontal_speed_mps=8.0,
            max_mission_radius_m=100.0,
            min_battery_percent=25.0,
            max_state_age_ms=1_000,
            geofence=NedGeofence(
                north_min_m=-100.0,
                north_max_m=100.0,
                east_min_m=-100.0,
                east_max_m=100.0,
            ),
            allowed_skills=(SkillName.TAKEOFF, SkillName.GOTO),
            human_approval_skills=approval_skills,
        ),
        completion_criteria=('完成',),
        abort_criteria=('取消',),
    )


def make_state(**changes: object) -> WorldState:
    '''构造地面待起飞的健康飞行器状态。'''

    state = WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=_NOW,
        state_age_ms=0,
        position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        home_position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        home_position_wgs84=GlobalPosition(
            latitude_deg=0.0, longitude_deg=0.0, altitude_amsl_m=0.0
        ),
        battery_percent=80.0,
        armed=False,
        landed=True,
        position_valid=True,
        home_valid=True,
        link_healthy=True,
        health_flags={
            'battery_connected': True,
            'battery_valid': True,
            'battery_fault_free': True,
            'failure_detector_clear': True,
            'preflight_checks_pass': True,
        },
    )
    return state.model_copy(update=changes)


def make_proposal(**changes: object) -> SkillProposal:
    '''构造地面起飞提案。'''

    proposal = SkillProposal(
        proposal_id='proposal-1',
        mission_id='mission-1',
        plan_revision=0,
        step_id='step-1',
        skill_name='takeoff',
        arguments={'target_altitude_m': 10.0},
        based_on_state_id='state-1',
    )
    return proposal.model_copy(update=changes)


def test_healthy_proposal_gets_stable_approval() -> None:
    '''验证全部检查通过后得到稳定的批准决定。'''

    source = FakeSpeedSource()
    supervisor = SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW)
    async def run_twice() -> tuple[SafetyDecision, SafetyDecision]:
        '''在同一输入上重复仲裁。'''

        first = await supervisor.evaluate(
            make_proposal(), make_contract(), make_state(), ControlAuthority.AGENT_ALLOWED
        )
        second = await supervisor.evaluate(
            make_proposal(), make_contract(), make_state(), ControlAuthority.AGENT_ALLOWED
        )
        return first, second

    first, second = asyncio.run(run_twice())
    assert first == second
    assert first.decision is SafetyDecisionType.APPROVE
    assert first.reason_codes == (SafetyReasonCode.CHECKS_PASSED,)
    assert source.calls == [2.0, 2.0]


def test_human_approval_is_not_execution_approval() -> None:
    '''验证需审批的Skill仅进入等待态。'''

    source = FakeSpeedSource()
    decision = asyncio.run(
        SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
            make_proposal(),
            make_contract(approval_skills=(SkillName.TAKEOFF,)),
            make_state(),
            ControlAuthority.AGENT_ALLOWED,
        )
    )
    assert decision.decision is SafetyDecisionType.HUMAN_APPROVAL
    assert decision.reason_codes == (SafetyReasonCode.HUMAN_APPROVAL_REQUIRED,)


def test_invalid_schema_and_stale_state_never_read_px4_parameter() -> None:
    '''验证无效参数及过期状态按顺序拒绝，且不访问飞控参数。'''

    source = FakeSpeedSource()
    decision = asyncio.run(
        SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
            make_proposal(arguments={'target_altitude_m': -1.0}),
            make_contract(),
            make_state(received_at=_NOW - timedelta(seconds=3)),
            ControlAuthority.AGENT_ALLOWED,
        )
    )
    assert decision.decision is SafetyDecisionType.REJECT
    assert decision.reason_codes == (
        SafetyReasonCode.INVALID_SCHEMA,
        SafetyReasonCode.STALE_STATE,
    )
    assert source.calls == []


@pytest.mark.parametrize(
    ('value', 'error', 'reason'),
    [
        (12.0, None, SafetyReasonCode.SPEED_ABOVE_ENVELOPE),
        (0.0, None, SafetyReasonCode.SYSTEM_UNHEALTHY),
        (8.0, TimeoutError('PX4 unavailable'), SafetyReasonCode.SYSTEM_UNHEALTHY),
    ],
)
def test_unusable_px4_speed_evidence_rejects(
    value: float, error: Exception | None, reason: SafetyReasonCode
) -> None:
    '''验证限速超任务约束、值无效或读取失败均拒绝。'''

    source = FakeSpeedSource(value=value, error=error)
    decision = asyncio.run(
        SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
            make_proposal(), make_contract(), make_state(), ControlAuthority.AGENT_ALLOWED
        )
    )
    assert decision.decision is SafetyDecisionType.REJECT
    assert decision.reason_codes == (reason,)


def test_state_becoming_stale_during_parameter_read_rejects() -> None:
    '''验证异步限速读取完成后重新检查原状态的时效。'''

    current = [_NOW]

    class AdvancingSource(FakeSpeedSource):
        '''模拟参数读取期间时钟向前推进。'''

        async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
            '''在返回读数前推进时钟。'''

            current[0] += timedelta(seconds=2)
            return await super().read_validated_speed_limit(max_age_s=max_age_s)

    decision = asyncio.run(
        SafetySupervisor(
            AdvancingSource(), policy_version='m4-v1', clock=lambda: current[0]
        ).evaluate(make_proposal(), make_contract(), make_state(), ControlAuthority.AGENT_ALLOWED)
    )
    assert decision.reason_codes == (SafetyReasonCode.STALE_STATE,)


def test_late_stale_state_precedes_speed_rejection() -> None:
    '''验证异步读取后出现多种错误时仍遵守检查顺序。'''

    current = [_NOW]

    class SlowSpeedSource(FakeSpeedSource):
        '''模拟超限参数在状态变旧后才返回。'''

        async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
            '''推进时钟并返回超限速度。'''

            current[0] += timedelta(seconds=2)
            return await super().read_validated_speed_limit(max_age_s=max_age_s)

    decision = asyncio.run(
        SafetySupervisor(
            SlowSpeedSource(value=12.0), policy_version='m4-v1', clock=lambda: current[0]
        ).evaluate(make_proposal(), make_contract(), make_state(), ControlAuthority.AGENT_ALLOWED)
    )
    assert decision.reason_codes == (
        SafetyReasonCode.STALE_STATE,
        SafetyReasonCode.SPEED_ABOVE_ENVELOPE,
    )
