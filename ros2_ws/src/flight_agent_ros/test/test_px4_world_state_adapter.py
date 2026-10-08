'''Tests for the WorldState ROS-to-Agent snapshot boundary.'''

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import rclpy
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter

from flight_agent.components.vehicle.state import WorldStateAggregator


def test_node_exposes_its_latest_immutable_snapshot(tmp_path: Path) -> None:
    '''The Agent-side reader receives the snapshot produced on the ROS side.'''

    rclpy.init()
    aggregator = WorldStateAggregator()
    node = Px4WorldStateAdapter(
        aggregator=aggregator,
        trace_path=tmp_path / 'world_state.jsonl',
    )
    try:
        assert node.ready is False
        with pytest.raises(RuntimeError, match='WORLD_STATE_UNAVAILABLE'):
            node.read_world_state()

        aggregator.update_local_position(
            SimpleNamespace(
                timestamp=10,
                x=1.0,
                y=2.0,
                z=-3.0,
                vx=0.0,
                vy=0.0,
                vz=0.0,
                xy_valid=True,
                z_valid=True,
            ),
            datetime(2026, 9, 23, tzinfo=UTC),
        )
        node._record_snapshot()

        state = node.read_world_state()
        assert node.ready is True
        assert state.position_ned_m is not None
        assert state.position_ned_m.model_dump() == {'x': 1.0, 'y': 2.0, 'z': -3.0}
    finally:
        node.destroy_node()
        rclpy.shutdown()
