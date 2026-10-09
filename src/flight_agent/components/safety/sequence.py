'''Deterministic Flight Skill lifecycle sequence checks.'''

from flight_agent.contracts import SafetyReasonCode, SkillName, WorldState

_AIRBORNE_SKILLS = frozenset(
    {SkillName.GOTO, SkillName.HOLD, SkillName.RTL, SkillName.LAND}
)
_BLOCKED_TRANSITION_MODES = frozenset({'AUTO_TAKEOFF', 'AUTO_RTL', 'AUTO_LAND'})


def check_command_sequence(
    skill_name: SkillName,
    state: WorldState,
) -> tuple[SafetyReasonCode, ...]:
    '''检查Skill是否允许从当前飞行生命周期阶段启动。'''

    if state.landed is None:
        return (SafetyReasonCode.INVALID_VEHICLE_STATE,)
    if state.flight_mode in _BLOCKED_TRANSITION_MODES:
        return (SafetyReasonCode.INVALID_COMMAND_SEQUENCE,)
    if state.landed:
        allowed = skill_name is SkillName.TAKEOFF
    else:
        allowed = skill_name in _AIRBORNE_SKILLS
    if allowed:
        return ()
    return (SafetyReasonCode.INVALID_COMMAND_SEQUENCE,)
