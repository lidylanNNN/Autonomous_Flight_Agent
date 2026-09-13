'''EnvironmentManifest 的强类型 Pydantic contract。'''

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    '''禁止未声明字段的基础模型。'''

    model_config = ConfigDict(extra='forbid')


class ProjectEnvironment(StrictModel):
    '''描述项目标识与规格版本。'''

    name: str = Field(min_length=1)
    spec_version: str = Field(pattern=r'^v\d+\.\d+$')


class HostEnvironment(StrictModel):
    '''描述主机操作系统要求。'''

    os: str = Field(min_length=1)


class PythonEnvironment(StrictModel):
    '''描述 Python 与包管理器要求。'''

    version: str = Field(min_length=1)
    package_manager: Literal['uv']


class Ros2Environment(StrictModel):
    '''描述 ROS2 运行环境。'''

    distro: str = Field(min_length=1)
    python_client: str = Field(min_length=1)


class Px4Environment(StrictModel):
    '''描述 PX4 版本固定方式。'''

    version_family: str = Field(min_length=1)
    branch_or_tag: str = Field(min_length=1)
    commit: str = Field(min_length=1)


class Px4MessagesEnvironment(StrictModel):
    '''描述 px4_msgs 版本固定方式。'''

    commit: str = Field(min_length=1)


class GazeboEnvironment(StrictModel):
    '''描述 Gazebo 仿真环境。'''

    version_family: str = Field(min_length=1)
    mode: Literal['headless_for_ci_and_benchmark', 'gui_for_debug']


class VehicleEnvironment(StrictModel):
    '''描述默认仿真载具。'''

    model: str = Field(min_length=1)


class MiddlewareEnvironment(StrictModel):
    '''描述 ROS2 与 PX4 之间的中间件。'''

    name: Literal['uXRCE-DDS']


class LlmEnvironment(StrictModel):
    '''描述 LLM 集成方式。'''

    integration: Literal['provider abstraction']


class ContainerEnvironment(StrictModel):
    '''描述容器运行方式。'''

    runtime: str = Field(min_length=1)
    status: Literal['planned_m12', 'required', 'not_required']


class EnvironmentManifest(StrictModel):
    '''定义项目可复现实验环境清单。'''

    manifest_version: str = Field(min_length=1)
    schema_path: str = Field(alias='schema', min_length=1)
    status: Literal['pending_pin', 'frozen']
    project: ProjectEnvironment
    host: HostEnvironment
    python: PythonEnvironment
    ros2: Ros2Environment
    px4: Px4Environment
    px4_msgs: Px4MessagesEnvironment
    gazebo: GazeboEnvironment
    vehicle: VehicleEnvironment
    middleware: MiddlewareEnvironment
    llm: LlmEnvironment
    container: ContainerEnvironment

    @model_validator(mode='after')
    def frozen_manifest_must_pin_versions(self) -> EnvironmentManifest:
        '''冻结环境时禁止保留 pending_pin 占位。'''

        if self.status != 'frozen':
            return self

        pending_paths = sorted(
            path for path, value in flatten_manifest(self).items() if value == 'pending_pin'
        )
        if pending_paths:
            joined_paths = ', '.join(pending_paths)
            raise ValueError(f'frozen manifest still has pending pins: {joined_paths}')

        return self


def flatten_manifest(model: BaseModel, prefix: str = '') -> dict[str, Any]:
    '''将 Pydantic 模型展开为 dotted path 到值的映射。'''

    values: dict[str, Any] = {}
    for field_name in model.model_fields:
        field_value = getattr(model, field_name)
        field_path = f'{prefix}.{field_name}' if prefix else field_name
        if isinstance(field_value, BaseModel):
            values.update(flatten_manifest(field_value, field_path))
        else:
            values[field_path] = field_value

    return values
