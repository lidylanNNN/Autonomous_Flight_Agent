'''Deterministic global Home reference checks.'''

from flight_agent.contracts import MissionConstraints, SafetyReasonCode, SkillName, WorldState


def check_home_reference(
    skill_name: SkillName,
    state: WorldState,
    constraints: MissionConstraints,
) -> tuple[SafetyReasonCode, ...]:
    '''RTL总要求全局Home，其他Skill遵守任务约束。'''

    if skill_name is not SkillName.RTL and not constraints.requires_home_position:
        return ()
    if not state.home_valid or state.home_position_wgs84 is None:
        return (SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,)
    return ()
