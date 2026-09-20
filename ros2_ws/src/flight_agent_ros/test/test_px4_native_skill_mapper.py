'''Tests for deterministic native PX4 Skill command mapping.'''

from __future__ import annotations

from datetime import UTC, datetime
from math import isnan

import pytest
from flight_agent_ros.adapters.px4_native_skill_mapper import (
    Px4NativeSkillMappingError,
    build_native_skill_plan,
)
from px4_msgs.msg import VehicleCommand

from flight_agent.contracts import (
    ApprovedSkillCommand,
    GlobalPosition,
    SkillName,
    Vector3,
    WorldState,
)


def make_state(*, with_home: bool = True) -> WorldState:
    '''构造带可选 Home 经纬高的飞行器状态。'''

    return WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        state_age_ms=0,
        position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        velocity_ned_mps=Vector3(x=0.0, y=0.0, z=0.0),
        armed=False,
        landed=True,
        position_valid=True,
        home_valid=with_home,
        home_position_wgs84=(
            GlobalPosition(
                latitude_deg=30.0,
                longitude_deg=120.0,
                altitude_amsl_m=42.5,
            )
            if with_home
            else None
        ),
        link_healthy=True,
    )


def make_command(skill_name: SkillName) -> ApprovedSkillCommand:
    '''构造指定 Skill 的已批准命令。'''

    if skill_name is SkillName.TAKEOFF:
        arguments: dict[str, float] = {'target_altitude_m': 10.0}
    elif skill_name is SkillName.GOTO:
        arguments = {
            'north_m': 10.0,
            'east_m': 5.0,
            'altitude_m': 20.0,
            'acceptance_radius_m': 1.0,
        }
    else:
        arguments = {}
    return ApprovedSkillCommand.model_validate(
        {
            'execution_id': f'exec-{skill_name}',
            'proposal_id': 'proposal-1',
            'decision_id': 'decision-1',
            'skill_name': skill_name,
            'arguments': arguments,
            'timeout_s': 30.0,
            'approved_state_id': 'state-1',
        }
    )


def test_takeoff_maps_relative_home_height_to_mode_then_arm_sequence() -> None:
    '''验证起飞高度转换为 AMSL，并固定先切起飞模式再解锁。'''

    plan = build_native_skill_plan(make_command(SkillName.TAKEOFF), make_state())

    assert [item.command_id for item in plan.commands] == [
        VehicleCommand.VEHICLE_CMD_NAV_TAKEOFF,
        VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,
    ]
    assert plan.commands[0].parameters.param7 == 52.5
    assert isnan(plan.commands[0].parameters.param5)
    assert isnan(plan.commands[0].parameters.param6)
    assert plan.commands[1].parameters.param1 == VehicleCommand.ARMING_ACTION_ARM


@pytest.mark.parametrize(
    ('skill_name', 'command_id'),
    [
        (SkillName.RTL, VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH),
        (SkillName.LAND, VehicleCommand.VEHICLE_CMD_NAV_LAND),
    ],
)
def test_single_native_command_skills_use_px4_mode_commands(
    skill_name: SkillName, command_id: int
) -> None:
    '''验证 RTL 与 Land 映射为单条 PX4 原生模式命令。'''

    plan = build_native_skill_plan(make_command(skill_name), make_state())

    assert len(plan.commands) == 1
    assert plan.commands[0].command_id == command_id
    assert isnan(plan.commands[0].parameters.param1)


@pytest.mark.parametrize('skill_name', [SkillName.TAKEOFF, SkillName.RTL])
def test_home_dependent_skills_reject_missing_home_reference(
    skill_name: SkillName,
) -> None:
    '''验证缺少 Home 经纬高时不会构造 Takeoff 或 RTL 命令。'''

    with pytest.raises(
        Px4NativeSkillMappingError, match='HOME_REFERENCE_UNAVAILABLE'
    ):
        build_native_skill_plan(make_command(skill_name), make_state(with_home=False))


@pytest.mark.parametrize('skill_name', [SkillName.GOTO, SkillName.HOLD])
def test_offboard_skills_are_not_mapped_to_native_vehicle_commands(
    skill_name: SkillName,
) -> None:
    '''验证 GoTo 与 Hold 不会误用 PX4 原生导航命令。'''

    with pytest.raises(Px4NativeSkillMappingError, match='SKILL_REQUIRES_OFFBOARD'):
        build_native_skill_plan(make_command(skill_name), make_state())
