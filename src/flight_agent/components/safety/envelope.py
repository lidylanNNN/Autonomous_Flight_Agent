'''Deterministic target-altitude checks for flight Skills.'''

from math import hypot, isfinite

from flight_agent.contracts import (
    GoToArgs,
    MissionConstraints,
    SafetyReasonCode,
    SkillArguments,
    SkillName,
    TakeoffArgs,
    WorldState,
)


def check_flight_envelope(
    skill_name: SkillName,
    arguments: SkillArguments,
    constraints: MissionConstraints,
) -> tuple[SafetyReasonCode, ...]:
    '''检查起飞或GoTo目标高度是否超过任务上限。'''

    if skill_name is SkillName.TAKEOFF:
        if not isinstance(arguments, TakeoffArgs):
            return (SafetyReasonCode.INVALID_SCHEMA,)
        altitude_m = arguments.target_altitude_m
    elif skill_name is SkillName.GOTO:
        if not isinstance(arguments, GoToArgs):
            return (SafetyReasonCode.INVALID_SCHEMA,)
        altitude_m = arguments.altitude_m
    else:
        return ()
    if altitude_m > constraints.max_altitude_m:
        return (SafetyReasonCode.ALTITUDE_ABOVE_ENVELOPE,)
    return ()


def check_mission_radius(
    skill_name: SkillName,
    arguments: SkillArguments,
    state: WorldState,
    constraints: MissionConstraints,
) -> tuple[SafetyReasonCode, ...]:
    '''检查GoTo目标到有效Home NED位置的水平距离。'''

    if skill_name is not SkillName.GOTO:
        return ()
    if not isinstance(arguments, GoToArgs):
        return (SafetyReasonCode.INVALID_SCHEMA,)
    home = state.home_position_ned_m
    if home is None or not all(map(isfinite, (home.x, home.y))):
        return (SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,)
    distance_m = hypot(arguments.north_m - home.x, arguments.east_m - home.y)
    if distance_m > constraints.max_mission_radius_m:
        return (SafetyReasonCode.MISSION_RADIUS_EXCEEDED,)
    return ()
