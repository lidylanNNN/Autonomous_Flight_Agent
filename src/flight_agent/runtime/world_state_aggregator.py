'''Aggregate PX4-like topic samples into a deterministic WorldState.'''

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flight_agent.contracts import CoordinateFrame, Vector3, WorldState, ned_to_enu


class WorldStateAggregator:
    '''收集 PX4 topic 快照并生成统一状态。'''

    def __init__(self) -> None:
        '''初始化空状态聚合器。'''

        self._position_ned = Vector3(x=0.0, y=0.0, z=0.0)
        self._velocity_ned = Vector3(x=0.0, y=0.0, z=0.0)
        self._source_timestamp_us = 0
        self._armed = False
        self._landed = True
        self._connected = False

    def update_local_position(self, message: Any) -> None:
        '''接收 PX4 VehicleLocalPosition 的位置和速度字段。'''

        self._position_ned = Vector3(x=message.x, y=message.y, z=message.z)
        self._velocity_ned = Vector3(x=message.vx, y=message.vy, z=message.vz)
        self._source_timestamp_us = max(self._source_timestamp_us, message.timestamp)
        self._connected = True

    def update_status(self, message: Any) -> None:
        '''接收 PX4 VehicleStatus 的解锁状态。'''

        self._armed = message.arming_state == message.ARMING_STATE_ARMED
        self._source_timestamp_us = max(self._source_timestamp_us, message.timestamp)
        self._connected = True

    def update_land_detected(self, message: Any) -> None:
        '''接收 PX4 VehicleLandDetected 的着陆状态。'''

        self._landed = bool(message.landed)
        self._source_timestamp_us = max(self._source_timestamp_us, message.timestamp)
        self._connected = True

    def snapshot(self, observed_at: datetime | None = None) -> WorldState:
        '''生成当前规范化 ENU WorldState 快照。'''

        return WorldState(
            observed_at=observed_at or datetime.now(UTC),
            source_timestamp_us=self._source_timestamp_us,
            frame=CoordinateFrame.ENU,
            position_m=ned_to_enu(self._position_ned),
            velocity_mps=ned_to_enu(self._velocity_ned),
            armed=self._armed,
            landed=self._landed,
            connected=self._connected,
        )
