'''WorldState aggregator tests.'''

from datetime import UTC, datetime
from types import SimpleNamespace

from flight_agent.runtime import WorldStateAggregator


def test_aggregator_normalizes_px4_samples() -> None:
    '''验证 PX4 NED 样本会生成 ENU 状态。'''

    aggregator = WorldStateAggregator()
    aggregator.update_local_position(
        SimpleNamespace(timestamp=10, x=1.0, y=2.0, z=-3.0, vx=4.0, vy=5.0, vz=-6.0)
    )
    aggregator.update_status(SimpleNamespace(timestamp=11, arming_state=2, ARMING_STATE_ARMED=2))
    aggregator.update_land_detected(SimpleNamespace(timestamp=12, landed=False))

    state = aggregator.snapshot(datetime(2026, 1, 1, tzinfo=UTC))

    assert state.position_m.x == 2.0
    assert state.position_m.y == 1.0
    assert state.position_m.z == 3.0
    assert state.velocity_mps.z == 6.0
    assert state.armed is True
    assert state.landed is False
    assert state.source_timestamp_us == 12
