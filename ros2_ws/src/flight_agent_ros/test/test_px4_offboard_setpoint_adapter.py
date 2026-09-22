'''Tests for continuous PX4 Offboard position-setpoint publication.'''

from __future__ import annotations

from math import inf, isnan, nan
from types import SimpleNamespace

import pytest
import rclpy
from flight_agent_ros.adapters.px4_offboard_setpoint_adapter import (
    PositionSetpointNed,
    Px4OffboardSetpointAdapter,
)


@pytest.mark.parametrize('invalid_value', [nan, inf, -inf])
def test_position_setpoint_rejects_non_finite_values(invalid_value: float) -> None:
    '''NED 目标拒绝 NaN 和无穷数。'''

    with pytest.raises(ValueError, match='must be finite'):
        PositionSetpointNed(north_m=invalid_value, east_m=0.0, down_m=-10.0)


@pytest.mark.parametrize('publish_rate_hz', [2.0, 0.0, inf])
def test_adapter_rejects_invalid_publish_rate(publish_rate_hz: float) -> None:
    '''发布频率必须满足 PX4 Offboard 心跳要求。'''

    rclpy.init()
    try:
        with pytest.raises(ValueError, match='greater than 2 Hz'):
            Px4OffboardSetpointAdapter(publish_rate_hz=publish_rate_hz)
    finally:
        rclpy.shutdown()


def test_active_target_publishes_position_only_messages() -> None:
    '''激活后使用同一时间戳发布位置控制心跳和 NED 目标。'''

    rclpy.init()
    adapter = Px4OffboardSetpointAdapter()
    control_modes: list[object] = []
    trajectories: list[object] = []
    try:
        adapter._control_mode_publisher = SimpleNamespace(publish=control_modes.append)
        adapter._trajectory_publisher = SimpleNamespace(publish=trajectories.append)
        adapter.start_position_stream(
            PositionSetpointNed(north_m=12.0, east_m=-4.5, down_m=-8.0)
        )

        adapter._publish_active_target()

        assert adapter.active is True
        assert len(control_modes) == 1
        assert len(trajectories) == 1
        control_mode = control_modes[0]
        trajectory = trajectories[0]
        assert control_mode.timestamp == trajectory.timestamp
        assert control_mode.position is True
        assert control_mode.velocity is False
        assert list(trajectory.position) == [12.0, -4.5, -8.0]
        assert all(isnan(value) for value in trajectory.velocity)
        assert all(isnan(value) for value in trajectory.acceleration)
        assert isnan(trajectory.yaw)
    finally:
        adapter.destroy_node()
        rclpy.shutdown()


def test_update_and_stop_have_explicit_lifecycle() -> None:
    '''目标只能在激活时更新，停止后不再发布消息。'''

    rclpy.init()
    adapter = Px4OffboardSetpointAdapter()
    trajectories: list[object] = []
    try:
        adapter._control_mode_publisher = SimpleNamespace(publish=lambda message: None)
        adapter._trajectory_publisher = SimpleNamespace(publish=trajectories.append)
        target = PositionSetpointNed(north_m=1.0, east_m=2.0, down_m=-3.0)

        with pytest.raises(RuntimeError, match='not active'):
            adapter.update_position_target(target)
        adapter.start_position_stream(target)
        with pytest.raises(RuntimeError, match='already active'):
            adapter.start_position_stream(target)
        adapter.update_position_target(
            PositionSetpointNed(north_m=4.0, east_m=5.0, down_m=-6.0)
        )
        adapter._publish_active_target()
        assert list(trajectories[-1].position) == [4.0, 5.0, -6.0]

        assert adapter.stop_position_stream() is True
        assert adapter.stop_position_stream() is False
        adapter._publish_active_target()
        assert len(trajectories) == 1
        assert adapter.active is False
    finally:
        adapter.destroy_node()
        rclpy.shutdown()
