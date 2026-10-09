'''Deterministic local NED geofence checks for GoTo proposals.'''

from flight_agent.contracts import (
    GoToArgs,
    NedGeofence,
    SafetyReasonCode,
    SkillArguments,
    SkillName,
    WorldState,
)


def check_geofence(
    skill_name: SkillName,
    arguments: SkillArguments,
    state: WorldState,
    geofence: NedGeofence,
) -> tuple[SafetyReasonCode, ...]:
    '''检查GoTo目标和直线路径是否留在扣除边界余量后的围栏内。'''

    if skill_name is not SkillName.GOTO:
        return ()
    if not isinstance(arguments, GoToArgs):
        return (SafetyReasonCode.INVALID_SCHEMA,)
    if not _contains(geofence, arguments.north_m, arguments.east_m):
        return (SafetyReasonCode.WAYPOINT_OUTSIDE_GEOFENCE,)
    if not state.position_valid or state.position_ned_m is None:
        return (SafetyReasonCode.INVALID_VEHICLE_STATE,)
    if not _contains(geofence, state.position_ned_m.x, state.position_ned_m.y):
        return (SafetyReasonCode.ROUTE_OUTSIDE_GEOFENCE,)
    return ()


def _contains(geofence: NedGeofence, north_m: float, east_m: float) -> bool:
    '''判断位置是否处于矩形围栏扣除余量后的闭区间内。'''

    margin = geofence.boundary_margin_m
    return (
        geofence.north_min_m + margin <= north_m <= geofence.north_max_m - margin
        and geofence.east_min_m + margin <= east_m <= geofence.east_max_m - margin
    )
