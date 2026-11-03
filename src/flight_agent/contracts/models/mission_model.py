'''系统安全限制与 NED 围栏模型。'''

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flight_agent.contracts.models.skill_model import SkillName


class _StrictFrozenModel(BaseModel):
    '''禁止额外字段并冻结安全配置。'''

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
    '''从系统配置加载、不可由动作请求放宽的安全限制。'''

    max_altitude_m: float = Field(gt=0.0)
    max_horizontal_speed_mps: float = Field(gt=0.0)
    max_mission_radius_m: float = Field(gt=0.0)
    min_battery_percent: float = Field(ge=0.0, le=100.0)
    max_state_age_ms: int = Field(gt=0)
    geofence: NedGeofence
    allowed_skills: tuple[SkillName, ...] = Field(min_length=1)
    requires_home_position: bool = True

    @field_validator('allowed_skills')
    @classmethod
    def skills_are_unique(cls, skills: tuple[SkillName, ...]) -> tuple[SkillName, ...]:
        '''拒绝重复的可用 Skill，保留配置中的稳定顺序。'''

        if len(skills) != len(set(skills)):
            raise ValueError('skill lists must not contain duplicates')
        return skills
