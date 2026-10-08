'''M4 Mission Contract 与 Safety Decision 契约测试。'''

import pytest
from pydantic import ValidationError

from flight_agent.contracts import (
    MissionConstraints,
    MissionContract,
    NedGeofence,
    SafetyDecision,
    SafetyDecisionType,
    SafetyReasonCode,
    SkillName,
    SkillProposal,
)


def make_constraints() -> MissionConstraints:
    '''构造一组有效的任务硬约束。'''

    return MissionConstraints(
        max_altitude_m=50.0,
        max_horizontal_speed_mps=8.0,
        max_mission_radius_m=120.0,
        min_battery_percent=25.0,
        max_state_age_ms=1_000,
        geofence=NedGeofence(
            north_min_m=-100.0,
            north_max_m=100.0,
            east_min_m=-80.0,
            east_max_m=80.0,
            boundary_margin_m=2.0,
        ),
        allowed_skills=tuple(SkillName),
        human_approval_skills=(SkillName.GOTO,),
    )


def make_contract() -> MissionContract:
    '''构造一份有效且可复用的任务契约。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='飞到检查点并安全返航',
        constraints=make_constraints(),
        completion_criteria=('到达检查点', '返回并落地'),
        abort_criteria=('PX4 failsafe active', 'link unhealthy'),
    )


def make_decision(
    decision: SafetyDecisionType,
    reason_codes: tuple[SafetyReasonCode, ...],
) -> SafetyDecision:
    '''构造指定类型与原因码的安全仲裁结果。'''

    return SafetyDecision(
        decision_id='decision-1',
        proposal_id='proposal-1',
        decision=decision,
        reason_codes=reason_codes,
        checked_state_id='state-1',
        policy_version='m4-policy-v1',
    )


def test_mission_contract_is_strict_and_frozen() -> None:
    '''验证任务契约拒绝额外字段且创建后不可修改。'''

    contract = make_contract()

    assert contract.constraints.allowed_skills == tuple(SkillName)
    with pytest.raises(ValidationError, match='frozen'):
        contract.objective = '绕过原任务'  # type: ignore[misc]
    with pytest.raises(ValidationError, match='Extra inputs'):
        MissionContract.model_validate(contract.model_dump() | {'planner_override': True})


@pytest.mark.parametrize(
    ('field_name', 'field_value'),
    [('north_max_m', -100.0), ('east_min_m', 80.0)],
)
def test_geofence_requires_positive_area(field_name: str, field_value: float) -> None:
    '''验证反向或零面积围栏不能进入 Mission Contract。'''

    payload = {
        'north_min_m': -100.0,
        'north_max_m': 100.0,
        'east_min_m': -80.0,
        'east_max_m': 80.0,
        field_name: field_value,
    }
    with pytest.raises(ValidationError, match='must be less than'):
        NedGeofence.model_validate(payload)


def test_mission_constraints_reject_duplicate_or_unapproved_skills() -> None:
    '''验证Skill列表唯一，且审批列表必须属于允许集合。'''

    payload = make_constraints().model_dump()
    with pytest.raises(ValidationError, match='must not contain duplicates'):
        MissionConstraints.model_validate(
            payload | {'allowed_skills': ['takeoff', 'takeoff']}
        )
    with pytest.raises(ValidationError, match='must be a subset'):
        MissionConstraints.model_validate(
            payload
            | {
                'allowed_skills': ['takeoff'],
                'human_approval_skills': ['goto'],
            }
        )


def test_skill_proposal_preserves_unapproved_content_for_safety_review() -> None:
    '''验证Proposal保留未知Skill和原始参数，供Safety给出明确拒绝。'''

    proposal = SkillProposal(
        proposal_id='proposal-1',
        mission_id='mission-1',
        plan_revision=0,
        step_id='step-1',
        skill_name='teleport',
        arguments={'unsafe_target': 999},
        based_on_state_id='state-1',
    )

    assert proposal.skill_name == 'teleport'
    assert proposal.arguments == {'unsafe_target': 999}
    with pytest.raises(ValidationError, match='Extra inputs'):
        SkillProposal.model_validate(proposal.model_dump() | {'approved': True})


@pytest.mark.parametrize(
    ('decision', 'reason_codes'),
    [
        (SafetyDecisionType.APPROVE, (SafetyReasonCode.CHECKS_PASSED,)),
        (
            SafetyDecisionType.REJECT,
            (
                SafetyReasonCode.ALTITUDE_ABOVE_ENVELOPE,
                SafetyReasonCode.AUTHORITY_NOT_GRANTED,
            ),
        ),
        (
            SafetyDecisionType.HUMAN_APPROVAL,
            (SafetyReasonCode.HUMAN_APPROVAL_REQUIRED,),
        ),
    ],
)
def test_safety_decision_accepts_only_matching_reason_families(
    decision: SafetyDecisionType,
    reason_codes: tuple[SafetyReasonCode, ...],
) -> None:
    '''验证三类Safety Decision各自接受合法原因码组合。'''

    assert make_decision(decision, reason_codes).reason_codes == reason_codes


@pytest.mark.parametrize(
    ('decision', 'reason_codes'),
    [
        (SafetyDecisionType.APPROVE, (SafetyReasonCode.STALE_STATE,)),
        (SafetyDecisionType.REJECT, (SafetyReasonCode.CHECKS_PASSED,)),
        (
            SafetyDecisionType.HUMAN_APPROVAL,
            (SafetyReasonCode.HUMAN_APPROVAL_TIMEOUT,),
        ),
        (
            SafetyDecisionType.REJECT,
            (SafetyReasonCode.STALE_STATE, SafetyReasonCode.STALE_STATE),
        ),
    ],
)
def test_safety_decision_rejects_mismatched_or_duplicate_reasons(
    decision: SafetyDecisionType,
    reason_codes: tuple[SafetyReasonCode, ...],
) -> None:
    '''验证仲裁类型不能携带矛盾或重复原因码。'''

    with pytest.raises(ValidationError):
        make_decision(decision, reason_codes)


def test_reason_codes_match_existing_agent_benchmark_cases() -> None:
    '''固定现有安全边界测评集依赖的机器可读原因码。'''

    assert SafetyReasonCode.ALTITUDE_ABOVE_ENVELOPE.value == (
        'ALTITUDE_ABOVE_ENVELOPE'
    )
    assert SafetyReasonCode.WAYPOINT_OUTSIDE_GEOFENCE.value == (
        'WAYPOINT_OUTSIDE_GEOFENCE'
    )
    assert SafetyReasonCode.AUTHORITY_NOT_GRANTED.value == 'AUTHORITY_NOT_GRANTED'
