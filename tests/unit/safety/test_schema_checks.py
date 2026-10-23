'''M4 Proposal schema check unit tests.'''

from flight_agent.components.safety import check_proposal_schema
from flight_agent.contracts import (
    AtomicActionProposal,
    MissionConstraints,
    NedGeofence,
    SafetyReasonCode,
    SkillName,
)


def make_constraints(
    allowed_skills: tuple[SkillName, ...] = tuple(SkillName),
) -> MissionConstraints:
    '''构造Schema检查使用的安全约束。'''

    return MissionConstraints(
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
        allowed_skills=allowed_skills,
    )


def make_proposal(
    *,
    skill_name: str = 'takeoff',
    arguments: dict[str, object] | None = None,
) -> AtomicActionProposal:
    '''构造Schema检查使用的Proposal。'''

    proposal_arguments = (
        {'target_altitude_m': 10.0} if arguments is None else arguments
    )
    return AtomicActionProposal(
        proposal_id='proposal-1',
        skill_name=skill_name,
        arguments=proposal_arguments,
        based_on_state_id='state-1',
    )


def test_valid_proposal_schema_has_no_rejection_reason() -> None:
    '''验证合法Proposal通过Schema检查。'''

    assert check_proposal_schema(make_proposal(), make_constraints()) == ()


def test_unknown_or_contract_disallowed_skill_is_rejected() -> None:
    '''验证未知Skill和Contract未授权Skill使用同一稳定原因码。'''

    assert check_proposal_schema(
        make_proposal(skill_name='teleport'), make_constraints()
    ) == (SafetyReasonCode.SKILL_NOT_ALLOWED,)
    assert check_proposal_schema(
        make_proposal(
            skill_name='goto',
            arguments={
                'north_m': 10.0,
                'east_m': 5.0,
                'altitude_m': 10.0,
                'acceptance_radius_m': 1.0,
            },
        ),
        make_constraints((SkillName.TAKEOFF, SkillName.LAND)),
    ) == (SafetyReasonCode.SKILL_NOT_ALLOWED,)


def test_invalid_arguments_are_invalid_schema() -> None:
    '''验证Skill参数错误被确定性拒绝。'''

    assert check_proposal_schema(
        make_proposal(arguments={'target_altitude_m': -1.0}), make_constraints()
    ) == (SafetyReasonCode.INVALID_SCHEMA,)


def test_multiple_schema_failures_follow_stable_reason_order() -> None:
    '''验证多个Schema错误按固定顺序返回且不重复。'''

    reasons = check_proposal_schema(
        make_proposal(
            skill_name='goto',
            arguments={'north_m': 1.0},
        ),
        make_constraints((SkillName.TAKEOFF,)),
    )

    assert reasons == (
        SafetyReasonCode.INVALID_SCHEMA,
        SafetyReasonCode.SKILL_NOT_ALLOWED,
    )
