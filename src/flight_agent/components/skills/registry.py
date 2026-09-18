'''M3 deterministic Flight Skill registry.'''

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from flight_agent.contracts import (
    GoToArgs,
    HoldArgs,
    LandArgs,
    RTLArgs,
    SkillAuthority,
    SkillName,
    SkillSpec,
    TakeoffArgs,
)

_SKILL_SPECS = (
    SkillSpec(
        name=SkillName.TAKEOFF,
        description='起飞到指定的正数目标高度。',
        args_schema=TakeoffArgs.model_json_schema(),
        required_state=('fresh_world_state', 'runtime_healthy', 'landed'),
        authority=SkillAuthority.SAFETY_APPROVAL_REQUIRED,
        timeout_s=30.0,
    ),
    SkillSpec(
        name=SkillName.GOTO,
        description='在 NED 局部坐标系中飞往指定目标。',
        args_schema=GoToArgs.model_json_schema(),
        required_state=('fresh_world_state', 'runtime_healthy', 'airborne', 'position_valid'),
        authority=SkillAuthority.SAFETY_APPROVAL_REQUIRED,
        timeout_s=60.0,
    ),
    SkillSpec(
        name=SkillName.HOLD,
        description='保持当前位置直到持续时间结束或收到取消。',
        args_schema=HoldArgs.model_json_schema(),
        required_state=('fresh_world_state', 'runtime_healthy', 'airborne', 'position_valid'),
        authority=SkillAuthority.SAFETY_APPROVAL_REQUIRED,
        timeout_s=30.0,
    ),
    SkillSpec(
        name=SkillName.RTL,
        description='调用 PX4 的确定性 Return-to-Launch 行为。',
        args_schema=RTLArgs.model_json_schema(),
        required_state=('fresh_world_state', 'runtime_healthy', 'home_valid'),
        authority=SkillAuthority.SAFETY_APPROVAL_REQUIRED,
        timeout_s=120.0,
    ),
    SkillSpec(
        name=SkillName.LAND,
        description='调用 PX4 的确定性降落行为。',
        args_schema=LandArgs.model_json_schema(),
        required_state=('fresh_world_state', 'runtime_healthy', 'position_valid'),
        authority=SkillAuthority.SAFETY_APPROVAL_REQUIRED,
        timeout_s=90.0,
    ),
)

SKILL_REGISTRY: Mapping[SkillName, SkillSpec] = MappingProxyType(
    {spec.name: spec for spec in _SKILL_SPECS}
)


def get_skill_spec(name: SkillName) -> SkillSpec:
    '''返回指定 Skill 的 Registry 定义。'''

    return SKILL_REGISTRY[name]


def list_skill_specs() -> tuple[SkillSpec, ...]:
    '''按稳定的 V1 顺序返回全部 Skill 定义。'''

    return _SKILL_SPECS
