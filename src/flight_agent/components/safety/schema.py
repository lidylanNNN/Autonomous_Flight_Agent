'''Deterministic Skill Proposal schema checks.'''

from pydantic import ValidationError

from flight_agent.contracts import (
    AtomicActionProposal,
    MissionConstraints,
    SafetyReasonCode,
    SkillName,
    parse_skill_arguments,
)


def check_proposal_schema(
    proposal: AtomicActionProposal,
    constraints: MissionConstraints,
    expected_mission_id: str,
) -> tuple[SafetyReasonCode, ...]:
    '''检查Proposal归属、Skill授权范围和参数结构。'''

    try:
        skill_name = SkillName(proposal.skill_name)
    except ValueError:
        return (SafetyReasonCode.SKILL_NOT_ALLOWED,)

    invalid_schema = proposal.mission_id != expected_mission_id
    try:
        parse_skill_arguments(skill_name, proposal.arguments)
    except ValidationError:
        invalid_schema = True

    reasons: list[SafetyReasonCode] = []
    if invalid_schema:
        reasons.append(SafetyReasonCode.INVALID_SCHEMA)
    if skill_name not in constraints.allowed_skills:
        reasons.append(SafetyReasonCode.SKILL_NOT_ALLOWED)
    return tuple(reasons)
