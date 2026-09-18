'''M3 deterministic flight skill contracts.'''

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    model_validator,
)


class StrictFrozenModel(BaseModel):
    '''禁止额外字段并冻结实例的 M3 Contract 基类。'''

    model_config = ConfigDict(
        extra='forbid', frozen=True, allow_inf_nan=False, str_strip_whitespace=True
    )


class SkillName(StrEnum):
    '''V1 对外暴露的确定性飞行技能名称。'''

    TAKEOFF = 'takeoff'
    GOTO = 'goto'
    HOLD = 'hold'
    RTL = 'rtl'
    LAND = 'land'


class SkillExecutionStatus(StrEnum):
    '''Skill Executor 的终态。'''

    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    TIMED_OUT = 'TIMED_OUT'
    CANCELLED = 'CANCELLED'
    REJECTED_BY_PX4 = 'REJECTED_BY_PX4'


class SkillAuthority(StrEnum):
    '''Skill 在进入 Runtime 前要求的授权类型。'''

    SAFETY_APPROVAL_REQUIRED = 'safety_approval_required'


class TakeoffArgs(StrictFrozenModel):
    '''起飞到正数目标高度，单位为米。'''

    target_altitude_m: float = Field(gt=0.0)


class GoToArgs(StrictFrozenModel):
    '''在项目 NED 局部坐标约定下移动到目标点。'''

    north_m: float
    east_m: float
    altitude_m: float = Field(gt=0.0)
    acceptance_radius_m: float = Field(gt=0.0)


class HoldArgs(StrictFrozenModel):
    '''保持当前位置；None 表示持续到取消。'''

    duration_s: float | None = Field(default=None, gt=0.0)


class RTLArgs(StrictFrozenModel):
    '''返回 Home 的无参数请求。'''


class LandArgs(StrictFrozenModel):
    '''在当前位置执行降落的无参数请求。'''


type SkillArguments = TakeoffArgs | GoToArgs | HoldArgs | RTLArgs | LandArgs

_ARGUMENT_MODEL_BY_SKILL: dict[SkillName, type[StrictFrozenModel]] = {
    SkillName.TAKEOFF: TakeoffArgs,
    SkillName.GOTO: GoToArgs,
    SkillName.HOLD: HoldArgs,
    SkillName.RTL: RTLArgs,
    SkillName.LAND: LandArgs,
}


class ApprovedSkillCommand(StrictFrozenModel):
    '''经过 Safety 批准后才允许交给 FlightExecutionInterface 实现的命令。'''

    execution_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    skill_name: SkillName
    arguments: SkillArguments
    timeout_s: PositiveFloat
    approved_state_id: str = Field(min_length=1)

    @model_validator(mode='before')
    @classmethod
    def parse_arguments_for_skill(cls, value: Any) -> Any:
        '''按 skill_name 使用唯一参数模型解析 arguments。'''

        if not isinstance(value, dict):
            return value
        raw_skill_name = value.get('skill_name')
        if raw_skill_name is None or 'arguments' not in value:
            return value
        try:
            skill_name = SkillName(raw_skill_name)
        except ValueError:
            return value
        parsed = dict(value)
        parsed['arguments'] = _ARGUMENT_MODEL_BY_SKILL[skill_name].model_validate(
            value['arguments']
        )
        return parsed


class SkillResult(StrictFrozenModel):
    '''记录一次确定性 Skill 执行的协议结果。'''

    execution_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    skill_name: SkillName
    status: SkillExecutionStatus
    px4_ack: str | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime
    start_state_id: str = Field(min_length=1)
    end_state_id: str = Field(min_length=1)
    failure_code: str | None = None

    @model_validator(mode='after')
    def result_fields_match_status(self) -> SkillResult:
        '''校验时间顺序及成功、失败终态所需字段。'''

        if self.ended_at < self.started_at:
            raise ValueError('ended_at must not be earlier than started_at')
        if self.status is SkillExecutionStatus.SUCCEEDED and self.failure_code is not None:
            raise ValueError('successful skill result must not include failure_code')
        if self.status is not SkillExecutionStatus.SUCCEEDED and not self.failure_code:
            raise ValueError('non-success skill result requires failure_code')
        return self


class SkillSpec(StrictFrozenModel):
    '''描述 Registry 中可执行 Skill 的参数与生命周期元数据。'''

    name: SkillName
    description: str = Field(min_length=1)
    args_schema: dict[str, Any]
    required_state: tuple[str, ...]
    authority: SkillAuthority
    timeout_s: PositiveFloat


def skill_result_duration_s(result: SkillResult) -> float:
    '''计算 SkillResult 的实际持续时间。'''

    elapsed: timedelta = result.ended_at - result.started_at
    return elapsed.total_seconds()
