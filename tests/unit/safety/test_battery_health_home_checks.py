'''M4 resource, system health and Home reference boundary tests.'''

from datetime import UTC, datetime

import pytest

from flight_agent.components.safety import (
    check_battery,
    check_home_reference,
    check_system_health,
)
from flight_agent.contracts import (
    GlobalPosition,
    MissionConstraints,
    NedGeofence,
    SafetyReasonCode,
    SkillName,
    WorldState,
)


def make_constraints(*, requires_home_position: bool = True) -> MissionConstraints:
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
        allowed_skills=(SkillName.TAKEOFF, SkillName.GOTO, SkillName.RTL),
        requires_home_position=requires_home_position,
    )


def make_state() -> WorldState:
    return WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=datetime(2026, 10, 10, tzinfo=UTC),
        state_age_ms=0,
        battery_percent=25.0,
        link_healthy=True,
        home_valid=True,
        home_position_wgs84=GlobalPosition(
            latitude_deg=30.0, longitude_deg=120.0, altitude_amsl_m=20.0
        ),
        health_flags={
            'battery_connected': True,
            'battery_valid': True,
            'battery_fault_free': True,
            'failure_detector_clear': True,
            'preflight_checks_pass': True,
        },
    )


@pytest.mark.parametrize(
    ('percent', 'expected'),
    [
        (25.0, ()),
        (24.9, (SafetyReasonCode.INSUFFICIENT_BATTERY,)),
        (80.0, ()),
    ],
)
def test_battery_respects_inclusive_mission_minimum(
    percent: float,
    expected: tuple[SafetyReasonCode, ...],
) -> None:
    state = make_state().model_copy(update={'battery_percent': percent})

    assert check_battery(state, make_constraints()) == expected


@pytest.mark.parametrize(
    ('changes', 'flag'),
    [
        ({'battery_percent': None}, None),
        ({}, 'battery_connected'),
        ({}, 'battery_valid'),
        ({}, 'battery_fault_free'),
    ],
)
def test_battery_rejects_missing_reading_or_bad_health_flags(
    changes: dict[str, object], flag: str | None
) -> None:
    state = make_state().model_copy(update=changes)
    if flag is not None:
        flags = dict(state.health_flags)
        flags[flag] = False
        state = state.model_copy(update={'health_flags': flags})

    assert check_battery(state, make_constraints()) == (SafetyReasonCode.SYSTEM_UNHEALTHY,)


def test_battery_rejects_missing_health_evidence() -> None:
    state = make_state().model_copy(update={'health_flags': {}})

    assert check_battery(state, make_constraints()) == (SafetyReasonCode.SYSTEM_UNHEALTHY,)


@pytest.mark.parametrize(
    'changes',
    [
        {'link_healthy': False},
        {'failsafe_active': True},
        {'health_flags': {'failure_detector_clear': False}},
        {'health_flags': {}},
    ],
)
def test_system_health_rejects_link_failsafe_and_missing_failure_evidence(
    changes: dict[str, object],
) -> None:
    state = make_state().model_copy(update=changes)

    assert check_system_health(SkillName.GOTO, state) == (SafetyReasonCode.SYSTEM_UNHEALTHY,)


def test_takeoff_requires_preflight_checks_but_goto_does_not() -> None:
    flags = dict(make_state().health_flags)
    flags['preflight_checks_pass'] = False
    state = make_state().model_copy(update={'health_flags': flags})

    assert check_system_health(SkillName.TAKEOFF, state) == (SafetyReasonCode.SYSTEM_UNHEALTHY,)
    assert check_system_health(SkillName.GOTO, state) == ()


def test_gcs_connection_is_not_required_for_onboard_agent() -> None:
    flags = dict(make_state().health_flags)
    flags['gcs_connection_healthy'] = False
    state = make_state().model_copy(update={'health_flags': flags})

    assert check_system_health(SkillName.GOTO, state) == ()


@pytest.mark.parametrize(
    ('skill_name', 'requires_home', 'expected'),
    [
        (SkillName.RTL, True, (SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,)),
        (SkillName.RTL, False, (SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,)),
        (SkillName.TAKEOFF, True, (SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,)),
        (SkillName.TAKEOFF, False, ()),
    ],
)
def test_home_requirement_depends_on_skill_and_contract(
    skill_name: SkillName,
    requires_home: bool,
    expected: tuple[SafetyReasonCode, ...],
) -> None:
    state = make_state().model_copy(update={'home_valid': False})

    assert check_home_reference(
        skill_name, state, make_constraints(requires_home_position=requires_home)
    ) == expected


def test_rtl_requires_global_home_not_just_valid_flag() -> None:
    state = make_state().model_copy(update={'home_position_wgs84': None})

    assert check_home_reference(SkillName.RTL, state, make_constraints()) == (
        SafetyReasonCode.HOME_REFERENCE_UNAVAILABLE,
    )


def test_valid_home_is_accepted() -> None:
    assert check_home_reference(SkillName.RTL, make_state(), make_constraints()) == ()
