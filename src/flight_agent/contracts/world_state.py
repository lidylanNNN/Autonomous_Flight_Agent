'''M2 WorldState contract and deterministic coordinate helpers.'''

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoordinateFrame(StrEnum):
    '''支持的局部坐标系。'''

    NED = 'ned'
    ENU = 'enu'


class Vector3(BaseModel):
    '''三维向量，单位由所属字段约定为米或米每秒。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    x: float
    y: float
    z: float


class WorldState(BaseModel):
    '''Agent 使用的规范化世界状态快照。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    observed_at: datetime
    source_timestamp_us: int = Field(ge=0)
    frame: CoordinateFrame
    position_m: Vector3
    velocity_mps: Vector3
    armed: bool
    landed: bool
    connected: bool

    @model_validator(mode='after')
    def timestamps_are_timezone_aware(self) -> WorldState:
        '''拒绝没有时区的观测时间，避免跨机器比较产生歧义。'''

        if self.observed_at.tzinfo is None:
            raise ValueError('observed_at must include a timezone')
        return self

    def age_seconds(self, now: datetime | None = None) -> float:
        '''计算快照相对于当前时间的秒数年龄。'''

        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            raise ValueError('now must include a timezone')
        return max(0.0, (reference - self.observed_at).total_seconds())

    def is_fresh(self, max_age_s: float, now: datetime | None = None) -> bool:
        '''判断快照是否没有超过允许的新鲜度窗口。'''

        if max_age_s < 0:
            raise ValueError('max_age_s must be non-negative')
        return self.age_seconds(now) <= max_age_s


def ned_to_enu(vector: Vector3) -> Vector3:
    '''将 PX4 常用 NED 向量转换为规范化 ENU 向量。'''

    return Vector3(x=vector.y, y=vector.x, z=-vector.z)
