'''M4 planner proposal contract models.'''

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SkillProposal(BaseModel):
    '''等待Safety检查的请求外壳，Skill参数语义尚未获批。'''

    model_config = ConfigDict(
        extra='forbid', frozen=True, allow_inf_nan=False, str_strip_whitespace=True
    )

    proposal_id: str = Field(min_length=1)
    mission_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=0)
    step_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    arguments: dict[str, Any]
    based_on_state_id: str = Field(min_length=1)
