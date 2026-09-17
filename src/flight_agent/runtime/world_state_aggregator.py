'''Aggregate PX4 topic samples into a deterministic NED WorldState.'''

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flight_agent.contracts import Vector3, WorldState

_REQUIRED_TOPICS = ('local_position', 'status', 'land_detected')


class WorldStateAggregator:
    '''收集 PX4 Topic 样本并生成统一状态。'''

    def __init__(self, max_state_age_ms: int = 1000) -> None:
        '''初始化空状态聚合器和 Topic 新鲜度上限。'''

        if max_state_age_ms < 0:
            raise ValueError('max_state_age_ms must be non-negative')
        self._max_state_age_ms = max_state_age_ms
        self._position_ned: Vector3 | None = None
        self._velocity_ned: Vector3 | None = None
        self._source_timestamp_us = 0
        self._armed = False
        self._landed: bool | None = None
        self._nav_state: str | None = None
        self._position_valid = False
        self._failsafe_active = False
        self._last_received_at: dict[str, datetime] = {}
        self._snapshot_sequence = 0

    @property
    def has_samples(self) -> bool:
        '''说明聚合器是否至少接收过一条 PX4 消息。'''

        return bool(self._last_received_at)

    def update_local_position(
        self, message: Any, received_at: datetime | None = None
    ) -> None:
        '''接收 PX4 VehicleLocalPosition 的 NED 位置和速度字段。'''

        self._position_ned = Vector3(x=message.x, y=message.y, z=message.z)
        self._velocity_ned = Vector3(x=message.vx, y=message.vy, z=message.vz)
        self._position_valid = bool(
            getattr(message, 'xy_valid', True) and getattr(message, 'z_valid', True)
        )
        self._update_source_timestamp(message.timestamp)
        self._mark_received('local_position', received_at)

    def update_status(self, message: Any, received_at: datetime | None = None) -> None:
        '''接收 PX4 VehicleStatus 的解锁、导航和失效保护状态。'''

        self._armed = message.arming_state == message.ARMING_STATE_ARMED
        nav_state = getattr(message, 'nav_state', None)
        self._nav_state = str(nav_state) if nav_state is not None else None
        self._failsafe_active = bool(getattr(message, 'failsafe', False))
        self._update_source_timestamp(message.timestamp)
        self._mark_received('status', received_at)

    def update_land_detected(
        self, message: Any, received_at: datetime | None = None
    ) -> None:
        '''接收 PX4 VehicleLandDetected 的着陆状态。'''

        self._landed = bool(message.landed)
        self._update_source_timestamp(message.timestamp)
        self._mark_received('land_detected', received_at)

    def snapshot(self, snapshot_at: datetime | None = None) -> WorldState:
        '''生成当前 NED WorldState，并按最旧必需 Topic 计算年龄。'''

        now = self._aware_time(snapshot_at)
        topic_freshness = {
            topic: self._topic_is_fresh(topic, now) for topic in _REQUIRED_TOPICS
        }
        link_healthy = all(topic_freshness.values())
        received_at = min(self._last_received_at.values(), default=now)
        state_age_ms = self._elapsed_ms(received_at, now)
        self._snapshot_sequence += 1

        return WorldState(
            state_id=f'state-{self._source_timestamp_us}-{self._snapshot_sequence}',
            source_timestamp_us=self._source_timestamp_us,
            received_at=received_at,
            state_age_ms=state_age_ms,
            position_ned_m=self._position_ned,
            velocity_ned_mps=self._velocity_ned,
            armed=self._armed,
            landed=self._landed,
            nav_state=self._nav_state,
            position_valid=self._position_valid,
            failsafe_active=self._failsafe_active,
            link_healthy=link_healthy,
            health_flags={f'{topic}_fresh': fresh for topic, fresh in topic_freshness.items()},
        )

    def _update_source_timestamp(self, timestamp_us: int) -> None:
        '''保留本轮聚合状态中最新的 PX4 源时间戳。'''

        self._source_timestamp_us = max(self._source_timestamp_us, timestamp_us)

    def _mark_received(self, topic: str, received_at: datetime | None) -> None:
        '''记录 Topic 在 Adapter 中的实际接收时间。'''

        self._last_received_at[topic] = self._aware_time(received_at)

    def _topic_is_fresh(self, topic: str, now: datetime) -> bool:
        '''判断指定必需 Topic 是否存在且未超过年龄上限。'''

        received_at = self._last_received_at.get(topic)
        if received_at is None:
            return False
        return self._elapsed_ms(received_at, now) <= self._max_state_age_ms

    @staticmethod
    def _aware_time(value: datetime | None) -> datetime:
        '''返回有时区时间，并拒绝含义不明确的 naive datetime。'''

        result = value or datetime.now(UTC)
        if result.tzinfo is None:
            raise ValueError('received_at must include a timezone')
        return result

    @staticmethod
    def _elapsed_ms(start: datetime, end: datetime) -> int:
        '''计算非负毫秒时间差。'''

        elapsed = end - start
        if elapsed.total_seconds() <= 0:
            return 0
        return elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
