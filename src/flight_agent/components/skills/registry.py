'''飞行动作执行规格及 Agent 使用的结构化动作描述。'''

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

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


@dataclass(frozen=True)
class AtomicActionSpec:
    '''供 Agent 生成工具说明，不作为飞行权限或执行凭证。'''

    name: str
    description: str
    args_schema: dict[str, Any]
    output_description: str = ''


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
        description='保持当前位置指定的有限时长。',
        # 公开服务要求有限时长；内部 HoldArgs 仍保留 None/缺省的历史执行语义。
        args_schema={
            **HoldArgs.model_json_schema(),
            'description': '保持当前位置指定的有限时长。',
            'properties': {
                'duration_s': {
                    'type': 'number', 'exclusiveMinimum': 0,
                    'description': '必须为有限正数秒，不允许省略、null 或布尔值。',
                },
            },
            'required': ['duration_s'],
        },
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

_ATOMIC_ACTION_SPECS = (
    *(AtomicActionSpec(
        name=spec.name.value,
        description=spec.description,
        args_schema=spec.args_schema,
    ) for spec in _SKILL_SPECS),
    AtomicActionSpec(
        name='capture_photo',
        description=(
            '保存请求后的一张新照片及相机、时间和实测位置。 '
            '不移动飞机，不做目标检测，不解析 prev_output。 '
            '只能输入空对象，不能指定路径、图片 ID、坐标或等待时长。'
        ),
        args_schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
        output_description=(
            'source (capture_photo_server), '
            'photo_id, image_path, metadata_path, sha256, error_code, message. '
            'success=true means a new PNG and metadata JSON were saved and read back; '
            'paths are absolute and sha256 identifies the PNG. '
            'On failure success=false, error_code reports the cause and file references are null. '
            'Photo success does not mean detection or flight task success.'
        ),
    ),
)

ATOMIC_ACTION_REGISTRY: Mapping[str, AtomicActionSpec] = MappingProxyType(
    {spec.name: spec for spec in _ATOMIC_ACTION_SPECS}
)


def get_atomic_action_spec(name: str) -> AtomicActionSpec:
    '''返回模型可用的动作描述；未知名称抛出 KeyError。'''

    return ATOMIC_ACTION_REGISTRY[name]


def get_skill_spec(name: SkillName) -> SkillSpec:
    '''返回指定 Skill 的 Registry 定义。'''

    return SKILL_REGISTRY[name]


def list_skill_specs() -> tuple[SkillSpec, ...]:
    '''按稳定的 V1 顺序返回全部 Skill 定义。'''

    return _SKILL_SPECS
