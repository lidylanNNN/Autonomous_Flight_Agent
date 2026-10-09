'''M4 local geofence and target-altitude boundary tests.'''

from datetime import UTC, datetime
from math import inf, nan

import pytest

from flight_agent.components.safety import (
    check_controller_speed_limit,
    check_flight_envelope,
    check_geofence,
    check_mission_radius,
)
from flight_agent.contracts import (
    GoToArgs,
    MissionConstraints,
    NedGeofence,
    SafetyReasonCode,
    SkillName,
    TakeoffArgs,
    Vector3,
    WorldState,
)


def make_constraints() -> MissionConstraints:
    '''构造高度上限和带余量的矩形围栏。'''

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
            boundary_margin_m=5.0,
        ),
        allowed_skills=(SkillName.TAKEOFF, SkillName.GOTO),
    )


def make_state(
    north_m: float = 0.0,
    east_m: float = 0.0,
    *,
    position_valid: bool = True,
) -> WorldState:
    '''构造含本地NED位置的状态。'''

    return WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=datetime(2026, 10, 9, tzinfo=UTC),
        state_age_ms=0,
        position_ned_m=Vector3(x=north_m, y=east_m, z=-10.0),
        position_valid=position_valid,
    )


@pytest.mark.parametrize(
    ('north_m', 'east_m', 'expected'),
    [
        (95.0, -95.0, ()),
        (95.01, 0.0, (SafetyReasonCode.WAYPOINT_OUTSIDE_GEOFENCE,)),
        (0.0, -95.01, (SafetyReasonCode.WAYPOINT_OUTSIDE_GEOFENCE,)),
    ],
)
def test_goto_target_respects_boundary_margin(
    north_m: float,
    east_m: float,
    expected: tuple[SafetyReasonCode, ...],
) -> None:
    '''验证目标落在有效边界上可通过，越过余量即拒绝。'''

    constraints = make_constraints()
    arguments = GoToArgs(
        north_m=north_m, east_m=east_m, altitude_m=10.0, acceptance_radius_m=1.0
    )
    assert check_geofence(
        SkillName.GOTO, arguments, make_state(), constraints.geofence
    ) == expected


def test_goto_route_requires_current_position_inside_fence() -> None:
    '''验证目标合法但起点越界时路径不能通过。'''

    arguments = GoToArgs(
        north_m=0.0, east_m=0.0, altitude_m=10.0, acceptance_radius_m=1.0
    )
    assert check_geofence(
        SkillName.GOTO, arguments, make_state(96.0), make_constraints().geofence
    ) == (SafetyReasonCode.ROUTE_OUTSIDE_GEOFENCE,)


def test_goto_route_requires_valid_position() -> None:
    '''验证没有可用起点时不把路径误判为安全。'''

    arguments = GoToArgs(
        north_m=0.0, east_m=0.0, altitude_m=10.0, acceptance_radius_m=1.0
    )
    assert check_geofence(
        SkillName.GOTO,
        arguments,
        make_state(position_valid=False),
        make_constraints().geofence,
    ) == (SafetyReasonCode.INVALID_VEHICLE_STATE,)


@pytest.mark.parametrize(
    ('skill_name', 'altitude_m', 'expected'),
    [
        (SkillName.TAKEOFF, 50.0, ()),
        (SkillName.TAKEOFF, 50.01, (SafetyReasonCode.ALTITUDE_ABOVE_ENVELOPE,)),
        (SkillName.GOTO, 50.0, ()),
        (SkillName.GOTO, 50.01, (SafetyReasonCode.ALTITUDE_ABOVE_ENVELOPE,)),
    ],
)
def test_target_altitude_limit(
    skill_name: SkillName,
    altitude_m: float,
    expected: tuple[SafetyReasonCode, ...],
) -> None:
    '''验证起飞和GoTo目标高度的闭区间上限。'''

    arguments = (
        TakeoffArgs(target_altitude_m=altitude_m)
        if skill_name is SkillName.TAKEOFF
        else GoToArgs(
            north_m=0.0,
            east_m=0.0,
            altitude_m=altitude_m,
            acceptance_radius_m=1.0,
        )
    )
    assert check_flight_envelope(skill_name, arguments, make_constraints()) == expected


@pytest.mark.parametrize(
    ('px4_limit_mps', 'expected'),
    [
        (8.0, ()),
        (7.9, ()),
        (8.01, (SafetyReasonCode.SPEED_ABOVE_ENVELOPE,)),
        (None, (SafetyReasonCode.SYSTEM_UNHEALTHY,)),
        (0.0, (SafetyReasonCode.SYSTEM_UNHEALTHY,)),
        (-1.0, (SafetyReasonCode.SYSTEM_UNHEALTHY,)),
        (nan, (SafetyReasonCode.SYSTEM_UNHEALTHY,)),
        (inf, (SafetyReasonCode.SYSTEM_UNHEALTHY,)),
        (True, (SafetyReasonCode.SYSTEM_UNHEALTHY,)),
    ],
)
def test_controller_speed_limit_requires_usable_px4_value(
    px4_limit_mps: float | None,
    expected: tuple[SafetyReasonCode, ...],
) -> None:
    '''验证PX4限速的边界及缺失、非法值均不能误放行。'''

    assert check_controller_speed_limit(px4_limit_mps, make_constraints()) == expected


def test_unrelated_skill_is_not_checked_as_goto() -> None:
    '''验证起飞不会被GoTo围栏检查误拦截。'''

    assert check_geofence(
        SkillName.TAKEOFF,
        TakeoffArgs(target_altitude_m=10.0),
        make_state(101.0),
        make_constraints().geofence,
    ) == ()


@pytest.mark.parametrize(
    ('north_m', 'east_m', 'expected'),
    [
        (10.0, 120.0, ()),
        (10.0, 120.01, (SafetyReasonCode.MISSION_RADIUS_EXCEEDED,)),
    ],
)
def test_mission_radius_uses_home_not_local_origin(
    north_m: float,
    east_m: float,
    expected: tuple[SafetyReasonCode, ...],
) -> None:
    '''验证半径以偏离原点的Home为中心，边界距离允许通过。'''

    state = make_state().model_copy(
        update={'home_position_ned_m': Vector3(x=10.0, y=20.0, z=0.0)}
    )
    arguments = GoToArgs(
        north_m=north_m, east_m=east_m, altitude_m=10.0, acceptance_radius_m=1.0
    )
    assert check_mission_radius(
        SkillName.GOTO, arguments, state, make_constraints()
    ) == expected


def test_mission_radius_rejects_missing_local_home() -> None:
    '''验证缺少Home局部参考时不能误将原点当成Home。'''

    arguments = GoToArgs(
        north_m=0.0, east_m=0.0, altitude_m=10.0, acceptance_radius_m=1.0
    )
    assert check_mission_radius(
        SkillName.GOTO, arguments, make_state(), make_constraints()
    ) == (SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,)
