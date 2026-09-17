'''ROS 2 node that aggregates PX4 state topics into trace snapshots.'''

from __future__ import annotations

import os
from pathlib import Path

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

from flight_agent.runtime import TraceRecorder, WorldStateAggregator


class WorldStateNode(Node):
    '''订阅 PX4 状态并周期性写入 WorldState trace。'''

    def __init__(self) -> None:
        '''初始化 PX4 状态订阅和 trace 定时器。'''

        super().__init__('world_state_node')
        trace_path = Path(os.environ.get('FLIGHT_AGENT_TRACE_PATH', 'artifacts/world_state.jsonl'))
        self._aggregator = WorldStateAggregator()
        self._recorder = TraceRecorder(trace_path)
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

    def _record_snapshot(self) -> None:
        '''记录当前聚合状态。'''

        if not self._aggregator.has_samples:
            return
        state = self._aggregator.snapshot()
        self._recorder.record_world_state(state)


def main() -> None:
    '''启动 WorldState ROS 2 节点。'''

    rclpy.init()
    node = WorldStateNode()
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
