'''Aggregate PX4 topic samples into a deterministic NED WorldState.'''

from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any

from flight_agent.contracts import Vector3, WorldState

_REQUIRED_TOPICS = ('local_position', 'status', 'land_detected', 'battery')

_NAV_STATE_NAMES = {
    0: 'MANUAL',
    1: 'ALTCTL',
    2: 'POSCTL',
    3: 'AUTO_MISSION',
    4: 'AUTO_LOITER',
    5: 'AUTO_RTL',
    6: 'POSITION_SLOW',
    7: 'FREE5',
    8: 'FREE4',
    9: 'FREE3',
    10: 'ACRO',
    11: 'FREE2',
    12: 'DESCEND',
    13: 'TERMINATION',
    14: 'OFFBOARD',
    15: 'STABILIZED',
    16: 'FREE1',
    17: 'AUTO_TAKEOFF',
    18: 'AUTO_LAND',
    19: 'AUTO_FOLLOW_TARGET',
    20: 'AUTO_PRECLAND',
    21: 'ORBIT',
    22: 'AUTO_VTOL_TAKEOFF',
    23: 'EXTERNAL1',
    24: 'EXTERNAL2',
    25: 'EXTERNAL3',
    26: 'EXTERNAL4',
    27: 'EXTERNAL5',
    28: 'EXTERNAL6',
    29: 'EXTERNAL7',
    30: 'EXTERNAL8',
}

_ACK_RESULT_NAMES = {
    0: 'ACCEPTED',
    1: 'TEMPORARILY_REJECTED',
    2: 'DENIED',
    3: 'UNSUPPORTED',
    4: 'FAILED',
    5: 'IN_PROGRESS',
    6: 'CANCELLED',
}


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
        self._battery_percent: float | None = None
        self._battery_connected = False
        self._battery_valid = False
        self._battery_fault_free = False
        self._flight_mode: str | None = None
        self._nav_state: str | None = None
        self._position_valid = False
        self._home_valid = False
        self._failsafe_active = False
        self._gcs_connection_healthy = True
        self._preflight_checks_pass = False
        self._failure_detector_clear = True
        self._last_command_ack: str | None = None
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
        self._flight_mode = (
            _NAV_STATE_NAMES.get(nav_state, f'UNKNOWN_{nav_state}')
            if nav_state is not None
            else None
        )
        self._failsafe_active = bool(getattr(message, 'failsafe', False))
        self._gcs_connection_healthy = not bool(
            getattr(message, 'gcs_connection_lost', False)
            or getattr(message, 'high_latency_data_link_lost', False)
        )
        self._preflight_checks_pass = bool(
            getattr(message, 'pre_flight_checks_pass', False)
        )
        self._failure_detector_clear = getattr(message, 'failure_detector_status', 0) == 0
        self._update_source_timestamp(message.timestamp)
        self._mark_received('status', received_at)

    def update_land_detected(
        self, message: Any, received_at: datetime | None = None
    ) -> None:
        '''接收 PX4 VehicleLandDetected 的着陆状态。'''

        self._landed = bool(message.landed)
        self._update_source_timestamp(message.timestamp)
        self._mark_received('land_detected', received_at)

    def update_battery(self, message: Any, received_at: datetime | None = None) -> None:
        '''接收 PX4 BatteryStatus 并规范化剩余电量百分比。'''

        remaining = float(message.remaining)
        self._battery_connected = bool(message.connected)
        self._battery_valid = (
            self._battery_connected and isfinite(remaining) and 0.0 <= remaining <= 1.0
        )
        warning = int(getattr(message, 'warning', 0))
        self._battery_fault_free = int(getattr(message, 'faults', 0)) == 0 and warning not in {
            4,
            6,
        }
        self._battery_percent = remaining * 100.0 if self._battery_valid else None
        self._update_source_timestamp(message.timestamp)
        self._mark_received('battery', received_at)

    def update_home_position(
        self, message: Any, received_at: datetime | None = None
    ) -> None:
        '''接收 PX4 HomePosition 并记录 RTL 所需的全局 Home 有效性。'''

        self._home_valid = bool(message.valid_hpos and message.valid_alt)
        self._update_source_timestamp(message.timestamp)
        self._mark_received('home_position', received_at)

    def update_command_ack(
        self, message: Any, received_at: datetime | None = None
    ) -> None:
        '''接收 PX4 VehicleCommandAck 并保存稳定的命令结果名称。'''

        result = int(message.result)
        result_name = _ACK_RESULT_NAMES.get(result, f'UNKNOWN_{result}')
        self._last_command_ack = f'{message.command}:{result_name}'
        self._update_source_timestamp(message.timestamp)
        self._mark_received('command_ack', received_at)

    def snapshot(self, snapshot_at: datetime | None = None) -> WorldState:
        '''生成当前 NED WorldState，并按最旧必需 Topic 计算年龄。'''

        now = self._aware_time(snapshot_at)
        topic_freshness = {
            topic: self._topic_is_fresh(topic, now) for topic in _REQUIRED_TOPICS
        }
        link_healthy = all(topic_freshness.values())
        required_receive_times = [
            self._last_received_at[topic]
            for topic in _REQUIRED_TOPICS
            if topic in self._last_received_at
        ]
        received_at = min(required_receive_times, default=now)
        state_age_ms = self._elapsed_ms(received_at, now)
        health_flags = {
            **{f'{topic}_fresh': fresh for topic, fresh in topic_freshness.items()},
            'position_valid': self._position_valid,
            'battery_connected': self._battery_connected,
            'battery_valid': self._battery_valid,
            'battery_fault_free': self._battery_fault_free,
            'home_valid': self._home_valid,
            'failsafe_clear': not self._failsafe_active,
            'gcs_connection_healthy': self._gcs_connection_healthy,
            'preflight_checks_pass': self._preflight_checks_pass,
            'failure_detector_clear': self._failure_detector_clear,
            'command_ack_received': self._last_command_ack is not None,
        }
        health_flags['runtime_healthy'] = bool(
            link_healthy
            and self._position_valid
            and self._battery_valid
            and self._battery_fault_free
            and not self._failsafe_active
            and self._failure_detector_clear
        )
        self._snapshot_sequence += 1

        return WorldState(
            state_id=f'state-{self._source_timestamp_us}-{self._snapshot_sequence}',
            source_timestamp_us=self._source_timestamp_us,
            received_at=received_at,
            state_age_ms=state_age_ms,
            position_ned_m=self._position_ned,
            velocity_ned_mps=self._velocity_ned,
            battery_percent=self._battery_percent,
            armed=self._armed,
            landed=self._landed,
            flight_mode=self._flight_mode,
            nav_state=self._nav_state,
            position_valid=self._position_valid,
            home_valid=self._home_valid,
            failsafe_active=self._failsafe_active,
            link_healthy=link_healthy,
            last_command_ack=self._last_command_ack,
            health_flags=health_flags,
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
