'''M4 mission contract models.'''

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flight_agent.contracts.models.skill_model import SkillName


class _StrictFrozenModel(BaseModel):
    '''禁止额外字段并冻结Mission契约。'''

    model_config = ConfigDict(
        extra='forbid', frozen=True, allow_inf_nan=False, str_strip_whitespace=True
    )


class NedGeofence(_StrictFrozenModel):
    '''局部NED平面中的轴对齐矩形围栏。'''

    north_min_m: float
    north_max_m: float
    east_min_m: float
    east_max_m: float
    boundary_margin_m: float = Field(default=0.0, ge=0.0)

    @model_validator(mode='after')
    def bounds_have_positive_area(self) -> NedGeofence:
        '''拒绝反向或零面积边界。'''

        if self.north_min_m >= self.north_max_m:
            raise ValueError('north_min_m must be less than north_max_m')
        if self.east_min_m >= self.east_max_m:
            raise ValueError('east_min_m must be less than east_max_m')
        return self


class MissionConstraints(_StrictFrozenModel):
    '''由系统和用户共同冻结的任务级安全约束。'''

    max_altitude_m: float = Field(gt=0.0)
    max_horizontal_speed_mps: float = Field(gt=0.0)
    max_mission_radius_m: float = Field(gt=0.0)
    min_battery_percent: float = Field(ge=0.0, le=100.0)
    max_state_age_ms: int = Field(gt=0)
    geofence: NedGeofence
    allowed_skills: tuple[SkillName, ...] = Field(min_length=1)
    human_approval_skills: tuple[SkillName, ...] = ()
    requires_home_position: bool = True

    @field_validator('allowed_skills', 'human_approval_skills')
    @classmethod
    def skills_are_unique(cls, skills: tuple[SkillName, ...]) -> tuple[SkillName, ...]:
        '''拒绝重复Skill，保留Contract中的稳定顺序。'''

        if len(skills) != len(set(skills)):
            raise ValueError('skill lists must not contain duplicates')
        return skills

    @model_validator(mode='after')
    def approval_skills_are_allowed(self) -> MissionConstraints:
        '''要求人工审批的Skill必须同时位于允许集合。'''

        if not set(self.human_approval_skills).issubset(self.allowed_skills):
            raise ValueError('human approval skills must be a subset of allowed skills')
        return self


class MissionContract(_StrictFrozenModel):
    '''Planner不可修改的任务目标与安全约束。'''

    mission_id: str = Field(min_length=1)
    contract_version: int = Field(ge=1)
    objective: str = Field(min_length=1)
    constraints: MissionConstraints
    completion_criteria: tuple[str, ...] = Field(min_length=1)
    abort_criteria: tuple[str, ...] = Field(min_length=1)
