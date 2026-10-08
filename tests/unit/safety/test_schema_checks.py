'''M4 Proposal schema check unit tests.'''

from flight_agent.components.safety import check_proposal_schema
from flight_agent.contracts import (
    MissionConstraints,
    MissionContract,
    NedGeofence,
    SafetyReasonCode,
    SkillName,
    SkillProposal,
)


def make_contract(
    allowed_skills: tuple[SkillName, ...] = tuple(SkillName),
) -> MissionContract:
    '''构造Schema检查使用的任务契约。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='执行一次安全飞行',
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
            allowed_skills=allowed_skills,
        ),
        completion_criteria=('动作完成',),
        abort_criteria=('安全检查失败',),
    )


def make_proposal(
    *,
    mission_id: str = 'mission-1',
    skill_name: str = 'takeoff',
    arguments: dict[str, object] | None = None,
) -> SkillProposal:
    '''构造Schema检查使用的Proposal。'''

    proposal_arguments = (
        {'target_altitude_m': 10.0} if arguments is None else arguments
    )
    return SkillProposal(
        proposal_id='proposal-1',
        mission_id=mission_id,
        plan_revision=0,
        step_id='step-1',
        skill_name=skill_name,
        arguments=proposal_arguments,
        based_on_state_id='state-1',
    )


def test_valid_proposal_schema_has_no_rejection_reason() -> None:
    '''验证合法Proposal通过Schema检查。'''

    assert check_proposal_schema(make_proposal(), make_contract()) == ()


def test_unknown_or_contract_disallowed_skill_is_rejected() -> None:
    '''验证未知Skill和Contract未授权Skill使用同一稳定原因码。'''

    assert check_proposal_schema(
        make_proposal(skill_name='teleport'), make_contract()
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
        make_contract((SkillName.TAKEOFF, SkillName.LAND)),
    ) == (SafetyReasonCode.SKILL_NOT_ALLOWED,)


def test_wrong_mission_or_arguments_are_invalid_schema() -> None:
    '''验证任务归属错误和Skill参数错误被确定性拒绝。'''

    assert check_proposal_schema(
        make_proposal(mission_id='mission-2'), make_contract()
    ) == (SafetyReasonCode.INVALID_SCHEMA,)
    assert check_proposal_schema(
        make_proposal(arguments={'target_altitude_m': -1.0}), make_contract()
    ) == (SafetyReasonCode.INVALID_SCHEMA,)


def test_multiple_schema_failures_follow_stable_reason_order() -> None:
    '''验证多个Schema错误按固定顺序返回且不重复。'''

    reasons = check_proposal_schema(
        make_proposal(
            mission_id='mission-2',
            skill_name='goto',
            arguments={'north_m': 1.0},
        ),
        make_contract((SkillName.TAKEOFF,)),
    )

    assert reasons == (
        SafetyReasonCode.INVALID_SCHEMA,
        SafetyReasonCode.SKILL_NOT_ALLOWED,
    )
