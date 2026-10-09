'''Deterministic Skill-specific vehicle state checks.'''

from flight_agent.contracts import SafetyReasonCode, SkillName, WorldState

_POSITION_REQUIRED_SKILLS = frozenset(
    {SkillName.GOTO, SkillName.HOLD, SkillName.LAND}
)


def check_vehicle_state(
    skill_name: SkillName,
    state: WorldState,
) -> tuple[SafetyReasonCode, ...]:
    '''检查当前飞行器状态是否明确且足以判断指定Skill。'''

    lifecycle_invalid = state.landed is None or (
        state.landed is False and not state.armed
    )
    position_invalid = (
        skill_name in _POSITION_REQUIRED_SKILLS and not state.position_valid
    )
    if lifecycle_invalid or position_invalid:
        return (SafetyReasonCode.INVALID_VEHICLE_STATE,)
    return ()
