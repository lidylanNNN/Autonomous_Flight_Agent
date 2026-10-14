'''Typed boundaries for the first mission-to-execution workflow.'''

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flight_agent.contracts.models.safety_model import SafetyDecision
from flight_agent.contracts.models.skill_model import SkillName, SkillResult


class _FrozenModel(BaseModel):
    '''Reject unknown fields and keep workflow records immutable.'''

    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class MissionRequest(_FrozenModel):
    '''保存用户原始任务语句，不代表任务已经被理解或批准。'''

    mission_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(_FrozenModel):
    '''计划中的一个待审查 Skill 步骤。'''

    step_id: str = Field(min_length=1)
    skill_name: SkillName
    arguments: dict[str, Any]
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = Field(gt=0.0)


class MissionPlan(_FrozenModel):
    '''由规划来源给出的步骤列表，尚未获得 Safety 批准。'''

    mission_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=0)
    contract_version: int = Field(ge=1)
    based_on_state_id: str = Field(min_length=1)
    steps: tuple[PlanStep, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def step_ids_are_unique(self) -> MissionPlan:
        '''拒绝无法唯一定位的计划步骤。'''

        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError('plan step IDs must be unique')
        return self


class VerificationStatus(StrEnum):
    '''独立状态验证的结论；占位实现只会返回 INCONCLUSIVE。'''

    SUCCESS = 'SUCCESS'
    FAILURE = 'FAILURE'
    INCONCLUSIVE = 'INCONCLUSIVE'


class VerificationResult(_FrozenModel):
    '''对一次 Skill 执行结果的独立验证记录。'''

    verification_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    result: VerificationStatus
    checks: dict[str, bool | float | str] = Field(default_factory=dict)
    observed_state_ids: tuple[str, ...] = ()
    failure_code: str | None = None


class MissionRunStatus(StrEnum):
    '''骨架期不提供已完成任务这一状态。'''

    UNSUPPORTED = 'UNSUPPORTED'
    REJECTED = 'REJECTED'
    FAILED = 'FAILED'
    UNVERIFIED = 'UNVERIFIED'


class MissionRunResult(_FrozenModel):
    '''返回给入口的任务运行事实，不把占位验证当作成功。'''

    mission_id: str = Field(min_length=1)
    status: MissionRunStatus
    plan: MissionPlan | None = None
    decisions: tuple[SafetyDecision, ...] = ()
    skill_results: tuple[SkillResult, ...] = ()
    verifications: tuple[VerificationResult, ...] = ()
    reason: str | None = None
