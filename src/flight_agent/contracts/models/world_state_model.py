'''M2 WorldState contract and deterministic coordinate helpers.'''

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class FreshnessLevel(StrEnum):
    '''周期报文的新鲜度等级，阈值只在此处定义。'''

    FAST = 'fast'
    NORMAL = 'normal'
    SLOW = 'slow'

    @property
    def max_age_ms(self) -> int:
        '''按等级匹配允许的最大接收年龄，未知等级不回退。'''
        match self:
            case FreshnessLevel.FAST:
                return 500
            case FreshnessLevel.NORMAL:
                return 1000
            case FreshnessLevel.SLOW:
                return 1500
            case _:
                raise ValueError('unknown freshness level')


class WorldStateFreshness(BaseModel):
    '''保存快照及四类必需报文的接收时间，并显式分配等级。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    snapshot_at: datetime
    local_position_received_at: datetime | None
    status_received_at: datetime | None
    battery_received_at: datetime | None
    land_detected_received_at: datetime | None

    @field_validator('snapshot_at', 'local_position_received_at', 'status_received_at',
                     'battery_received_at', 'land_detected_received_at')
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        '''拒绝无时区时间；空值明确表示尚未收到报文。'''
        if value is not None and value.tzinfo is None:
            raise ValueError('timestamp must include a timezone')
        return value

    def local_position_is_fresh(self, now: datetime) -> bool:
        '''位置和速度使用快速档。'''
        return self._message_is_fresh(self.local_position_received_at, FreshnessLevel.FAST, now)

    def status_is_fresh(self, now: datetime) -> bool:
        '''飞控模式、权限状态和故障标志使用普通档。'''
        return self._message_is_fresh(self.status_received_at, FreshnessLevel.NORMAL, now)

    def battery_is_fresh(self, now: datetime) -> bool:
        '''电量、连接与故障标志使用普通档。'''
        return self._message_is_fresh(self.battery_received_at, FreshnessLevel.NORMAL, now)

    def land_detected_is_fresh(self, now: datetime) -> bool:
        '''着陆状态使用慢速档。'''
        return self._message_is_fresh(self.land_detected_received_at, FreshnessLevel.SLOW, now)

    def is_fresh(self, now: datetime) -> bool:
        '''逐项检查必需报文，任一缺失或过期均无效。'''
        return (self.local_position_is_fresh(now) and self.status_is_fresh(now)
                and self.battery_is_fresh(now) and self.land_detected_is_fresh(now))

    @staticmethod
    def _message_is_fresh(received_at: datetime | None, level: FreshnessLevel,
                          now: datetime) -> bool:
        '''按本地接收时间比较等级阈值，拒绝未来时间。'''
        if received_at is None:
            return False
        elapsed_ms = (now - received_at).total_seconds() * 1000
        return 0 <= elapsed_ms <= level.max_age_ms


class WorldState(BaseModel):
    '''Agent 使用的 NED 世界状态快照。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    state_id: str = Field(min_length=1)
    source_timestamp_us: int = Field(ge=0)
    received_at: datetime
    state_age_ms: int = Field(ge=0)
    freshness: WorldStateFreshness | None = None
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
    home_position_ned_m: Vector3 | None = None
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
        '''新格式逐报文检查并限制快照缓存年龄；旧格式保留统一年龄规则。'''

        if max_age_ms < 0:
            raise ValueError('max_age_ms must be non-negative')
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            raise ValueError('now must include a timezone')
        if self.freshness is None:
            return self.age_ms(reference) <= max_age_ms
        snapshot_age_ms = (reference - self.freshness.snapshot_at).total_seconds() * 1000
        return (0 <= snapshot_age_ms <= max_age_ms and self.freshness.is_fresh(reference))


def ned_to_enu(vector: Vector3) -> Vector3:
    '''将 NED 向量转换为 ENU 向量。'''

    return Vector3(x=vector.y, y=vector.x, z=-vector.z)


def enu_to_ned(vector: Vector3) -> Vector3:
    '''将 ENU 向量转换为 NED 向量。'''

    return Vector3(x=vector.y, y=vector.x, z=-vector.z)
