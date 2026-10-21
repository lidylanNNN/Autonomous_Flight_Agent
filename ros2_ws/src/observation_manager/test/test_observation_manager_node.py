"""The observation service must read PX4-derived state before responding."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import rclpy
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from observation_manager.observation_manager_node import ObservationManagerNode
from rosllm_srvs.srv import Observation

from flight_agent.components.vehicle.state import WorldStateAggregator


def test_observation_service_requires_state_and_marks_placeholder(tmp_path: Path) -> None:
    rclpy.init()
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(
        aggregator=aggregator, trace_path=tmp_path / 'world_state.jsonl'
    )
    node = ObservationManagerNode(source)
    try:
        absent = node.get_observation(Observation.Request(), Observation.Response())
        assert absent.success is False
        assert absent.observation == ''
        assert absent.message == 'WORLD_STATE_UNAVAILABLE'

        aggregator.update_local_position(
            SimpleNamespace(
                timestamp=10, x=1.0, y=2.0, z=-3.0,
                vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
            ),
            datetime.now(UTC),
        )
        source._record_snapshot()

        present = node.get_observation(Observation.Request(), Observation.Response())
        assert present.success is True
        assert present.source == 'px4_world_state_adapter:mx_placeholder'
        assert present.observation == 'MX_UNIMPLEMENTED_TEXT_SUMMARY state_id=state-10-1'
    finally:
        node.destroy_node()
        source.destroy_node()
        rclpy.shutdown()
