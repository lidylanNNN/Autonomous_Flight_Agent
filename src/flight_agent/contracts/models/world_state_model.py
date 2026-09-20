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


class GlobalPosition(BaseModel):
    '''WGS84 全局位置和平均海平面高度。'''

    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)

    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, le=180.0)
    altitude_amsl_m: float


class WorldState(BaseModel):
    '''Agent 使用的 NED 世界状态快照。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    state_id: str = Field(min_length=1)
    source_timestamp_us: int = Field(ge=0)
    received_at: datetime
    state_age_ms: int = Field(ge=0)
    position_ned_m: Vector3 | None = None
    velocity_ned_mps: Vector3 | None = None
    battery_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    armed: bool = False
    landed: bool | None = None
    flight_mode: str | None = None
    nav_state: str | None = None
    position_valid: bool = False
    home_valid: bool = False
    home_position_wgs84: GlobalPosition | None = None
    failsafe_active: bool = False
    link_healthy: bool = False
    last_command_ack: str | None = None
    health_flags: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode='after')
    def timestamps_are_timezone_aware(self) -> WorldState:
        '''拒绝没有时区的接收时间，避免跨机器比较产生歧义。'''

        if self.received_at.tzinfo is None:
            raise ValueError('received_at must include a timezone')
        return self

    def age_ms(self, now: datetime | None = None) -> int:
        '''计算状态当前年龄，并保留生成快照时记录的年龄下限。'''

        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            raise ValueError('now must include a timezone')
        elapsed = reference - self.received_at
        elapsed_ms = max(
            0,
            elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000,
        )
        return max(self.state_age_ms, elapsed_ms)

    def is_fresh(self, max_age_ms: int, now: datetime | None = None) -> bool:
        '''判断状态是否没有超过允许的新鲜度窗口。'''

        if max_age_ms < 0:
            raise ValueError('max_age_ms must be non-negative')
        return self.age_ms(now) <= max_age_ms


def ned_to_enu(vector: Vector3) -> Vector3:
    '''将 NED 向量转换为 ENU 向量。'''

    return Vector3(x=vector.y, y=vector.x, z=-vector.z)


def enu_to_ned(vector: Vector3) -> Vector3:
    '''将 ENU 向量转换为 NED 向量。'''

    return Vector3(x=vector.y, y=vector.x, z=-vector.z)
