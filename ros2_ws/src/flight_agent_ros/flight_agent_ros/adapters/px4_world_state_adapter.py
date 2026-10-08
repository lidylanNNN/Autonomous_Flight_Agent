'''ROS 2 node that aggregates PX4 state topics into trace snapshots.'''

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

import rclpy
from px4_msgs.msg import (
    BatteryStatus,
    HomePosition,
    VehicleCommandAck,
    VehicleLandDetected,
    VehicleLocalPosition,
    VehicleStatus,
)
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from flight_agent.components.tracing import TraceRecorder
from flight_agent.components.vehicle.state import WorldStateAggregator
from flight_agent.contracts import WorldState


class Px4WorldStateAdapter(Node):
    '''订阅 PX4 状态并周期性写入 WorldState trace。'''

    def __init__(
        self,
        *,
        aggregator: WorldStateAggregator | None = None,
        trace_path: Path | None = None,
    ) -> None:
        '''初始化 PX4 状态订阅和 trace 定时器。'''

        super().__init__('px4_world_state_adapter')
        resolved_trace_path = trace_path or Path(
            os.environ.get('FLIGHT_AGENT_TRACE_PATH', 'artifacts/world_state.jsonl')
        )
        self._aggregator = aggregator or WorldStateAggregator()
        self._recorder = TraceRecorder(resolved_trace_path)
        self._latest_state_lock = Lock()
        self._latest_state: WorldState | None = None
        px4_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(
            VehicleLocalPosition, '/fmu/out/vehicle_local_position',
            self._aggregator.update_local_position, px4_qos
        )
        self.create_subscription(
            VehicleStatus, '/fmu/out/vehicle_status_v1', self._aggregator.update_status, px4_qos
        )
        self.create_subscription(
            VehicleLandDetected, '/fmu/out/vehicle_land_detected',
            self._aggregator.update_land_detected, px4_qos
        )
        self.create_subscription(
            BatteryStatus, '/fmu/out/battery_status', self._aggregator.update_battery, px4_qos
        )
        self.create_subscription(
            HomePosition, '/fmu/out/home_position',
            self._aggregator.update_home_position, px4_qos
        )
        self.create_subscription(
            VehicleCommandAck, '/fmu/out/vehicle_command_ack',
            self._aggregator.update_command_ack, px4_qos
        )
        self.create_timer(0.1, self._record_snapshot)

    @property
    def ready(self) -> bool:
        '''Return whether at least one WorldState snapshot is available.'''

        with self._latest_state_lock:
            return self._latest_state is not None

    def read_world_state(self) -> WorldState:
        '''Return the latest immutable snapshot across the ROS/Agent thread boundary.'''

        with self._latest_state_lock:
            state = self._latest_state
        if state is None:
            raise RuntimeError('WORLD_STATE_UNAVAILABLE')
        return state

    def _record_snapshot(self) -> None:
        '''记录当前聚合状态。'''

        if not self._aggregator.has_samples:
            return
        state = self._aggregator.snapshot()
        with self._latest_state_lock:
            self._latest_state = state
        self._recorder.record_world_state(state)


def main() -> None:
    '''启动 WorldState ROS 2 节点。'''

    rclpy.init()
    node = Px4WorldStateAdapter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
