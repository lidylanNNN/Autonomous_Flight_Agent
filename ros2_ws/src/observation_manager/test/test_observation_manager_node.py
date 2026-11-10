"""The observation service must read PX4-derived state before responding."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import rclpy
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from observation_manager.observation_manager_node import ObservationManagerNode
from rosllm_srvs.srv import Observation

from flight_agent.components.vehicle.state import WorldStateAggregator


def test_observation_service_requires_state_and_summarizes_snapshot(tmp_path: Path) -> None:
    '''检查缺失状态失败，并通过真实聚合器核对摘要的坐标、未知值及年龄。'''

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
            datetime.now(UTC) - timedelta(seconds=2),
        )
        source._record_snapshot()

        present = node.get_observation(Observation.Request(), Observation.Response())
        assert present.success is True
        assert present.source == 'px4_world_state_adapter'
        assert present.message == 'WorldState snapshot acquired'
        assert 'frame=NED: x=north, y=east, z=down' in present.observation
        assert 'position in m, velocity in m/s; battery in percent' in present.observation
        assert 'null=unknown' in present.observation
        assert 'not safety approval or task verification' in present.observation
        summary = json.loads(present.observation.splitlines()[-1])
        assert summary['state_id'] == 'state-10-1'
        assert summary['position_ned_m'] == {'x': 1.0, 'y': 2.0, 'z': -3.0}
        assert summary['velocity_ned_mps'] == {'x': 0.0, 'y': 0.0, 'z': 0.0}
        assert summary['battery_percent'] is None
        assert summary['landed'] is None
        assert summary['flight_mode'] is None
        assert summary['home_position_ned_m'] is None
        assert summary['position_valid'] is True
        assert summary['home_valid'] is False
        assert summary['link_healthy'] is False
        assert summary['failsafe_active'] is False
        assert summary['state_age_ms'] >= 2000
        state = source.read_world_state()
        expected_output_summary = state.model_dump(mode='json')
        expected_output_summary['state_age_ms'] = summary['state_age_ms']
        assert summary == expected_output_summary
        with patch('flight_agent.contracts.models.world_state_model.datetime') as test_temp_clock:
            test_temp_clock.now.return_value = state.received_at + timedelta(seconds=5)
            present = node.get_observation(Observation.Request(), Observation.Response())
        summary = json.loads(present.observation.splitlines()[-1])
        assert summary['state_age_ms'] == 5000
        assert state.state_age_ms < summary['state_age_ms']

        aggregator.update_status(SimpleNamespace(
            timestamp=11, arming_state=2, ARMING_STATE_ARMED=2,
            nav_state=4, failsafe=True, pre_flight_checks_pass=True,
        ))
        aggregator.update_land_detected(SimpleNamespace(timestamp=12, landed=False))
        aggregator.update_battery(SimpleNamespace(
            timestamp=13, remaining=0.75, connected=True, faults=0, warning=0,
        ))
        aggregator.update_home_position(SimpleNamespace(
            timestamp=14, valid_hpos=True, valid_alt=True, valid_lpos=True,
            lat=47.0, lon=8.0, alt=500.0, x=0.0, y=1.0, z=2.0,
        ))
        source._record_snapshot()
        present = node.get_observation(Observation.Request(), Observation.Response())
        summary = json.loads(present.observation.splitlines()[-1])
        assert summary['armed'] is True
        assert summary['landed'] is False
        assert summary['battery_percent'] == 75.0
        assert summary['nav_state'] == '4'
        assert summary['flight_mode'] == 'AUTO_LOITER'
        assert summary['failsafe_active'] is True
        assert summary['home_valid'] is True
        assert summary['home_position_ned_m'] == {'x': 0.0, 'y': 1.0, 'z': 2.0}
        assert summary['home_position_wgs84']['altitude_amsl_m'] == 500.0
        assert summary['health_flags']['battery_valid'] is True
    finally:
        node.destroy_node()
        source.destroy_node()
        rclpy.shutdown()
