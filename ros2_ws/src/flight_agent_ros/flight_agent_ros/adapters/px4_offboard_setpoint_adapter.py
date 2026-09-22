'''Publish position-control heartbeat and setpoints for PX4 Offboard mode.'''

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, nan
from threading import Lock

from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy


@dataclass(frozen=True)
class PositionSetpointNed:
    '''One finite position target in the PX4 local NED frame.'''

    north_m: float
    east_m: float
    down_m: float

    def __post_init__(self) -> None:
        '''Reject values that PX4 cannot use as a position target.'''

        if not all(isfinite(value) for value in self.as_tuple()):
            raise ValueError('position setpoint values must be finite')

    def as_tuple(self) -> tuple[float, float, float]:
        '''Return the target in PX4 NED field order.'''

        return self.north_m, self.east_m, self.down_m


class Px4OffboardSetpointAdapter(Node):
    '''Continuously publish one active PX4 Offboard position target.'''

    def __init__(self, *, publish_rate_hz: float = 10.0) -> None:
        '''Create fixed-topic publishers and the inactive heartbeat timer.'''

        if not isfinite(publish_rate_hz) or publish_rate_hz <= 2.0:
            raise ValueError('publish_rate_hz must be finite and greater than 2 Hz')
        super().__init__('px4_offboard_setpoint_adapter')
        px4_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._control_mode_publisher = self.create_publisher(
            OffboardControlMode, '/fmu/in/offboard_control_mode', px4_qos
        )
        self._trajectory_publisher = self.create_publisher(
            TrajectorySetpoint, '/fmu/in/trajectory_setpoint', px4_qos
        )
        self._target_lock = Lock()
        self._target: PositionSetpointNed | None = None
        self._publish_timer = self.create_timer(
            1.0 / publish_rate_hz, self._publish_active_target
        )

    @property
    def active(self) -> bool:
        '''Return whether the timer currently has a target to publish.'''

        with self._target_lock:
            return self._target is not None

    def start_position_stream(self, target: PositionSetpointNed) -> None:
        '''Activate position streaming with an initial target.'''

        with self._target_lock:
            if self._target is not None:
                raise RuntimeError('position stream is already active')
            self._target = target

    def update_position_target(self, target: PositionSetpointNed) -> None:
        '''Replace the active target without interrupting the heartbeat.'''

        with self._target_lock:
            if self._target is None:
                raise RuntimeError('position stream is not active')
            self._target = target

    def stop_position_stream(self) -> bool:
        '''Stop publication and report whether a stream was active.'''

        with self._target_lock:
            was_active = self._target is not None
            self._target = None
        return was_active

    def _publish_active_target(self) -> None:
        '''Publish one heartbeat and setpoint when a target is active.'''

        with self._target_lock:
            target = self._target
        if target is None:
            return

        timestamp_us = self.get_clock().now().nanoseconds // 1_000
        control_mode = OffboardControlMode()
        control_mode.timestamp = timestamp_us
        control_mode.position = True

        trajectory = TrajectorySetpoint()
        trajectory.timestamp = timestamp_us
        trajectory.position = list(target.as_tuple())
        trajectory.velocity = [nan, nan, nan]
        trajectory.acceleration = [nan, nan, nan]
        trajectory.jerk = [nan, nan, nan]
        trajectory.yaw = nan
        trajectory.yawspeed = nan

        self._control_mode_publisher.publish(control_mode)
        self._trajectory_publisher.publish(trajectory)
