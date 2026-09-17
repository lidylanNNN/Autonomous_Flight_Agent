'''WorldState aggregator tests.'''

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from flight_agent.runtime import WorldStateAggregator


def update_required_topics(aggregator: WorldStateAggregator, received_at: datetime) -> None:
    '''向聚合器写入一组完整的必需 PX4 Topic 样本。'''

    aggregator.update_local_position(
        SimpleNamespace(
            timestamp=10,
            x=1.0,
            y=2.0,
            z=-3.0,
            vx=4.0,
            vy=5.0,
            vz=-6.0,
            xy_valid=True,
            z_valid=True,
        ),
        received_at,
    )
    aggregator.update_status(
        SimpleNamespace(
            timestamp=11,
            arming_state=2,
            ARMING_STATE_ARMED=2,
            nav_state=14,
            failsafe=False,
        ),
        received_at,
    )
    aggregator.update_land_detected(
        SimpleNamespace(timestamp=12, landed=False), received_at
    )


def test_aggregator_keeps_px4_samples_in_agent_ned_contract() -> None:
    '''验证 PX4 NED 样本不会在 Agent Contract 中被误转为 ENU。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)

    state = aggregator.snapshot(now)

    assert state.position_ned_m is not None
    assert state.position_ned_m.model_dump() == {'x': 1.0, 'y': 2.0, 'z': -3.0}
    assert state.velocity_ned_mps is not None
    assert state.velocity_ned_mps.z == -6.0
    assert state.armed is True
    assert state.landed is False
    assert state.source_timestamp_us == 12
    assert state.state_id == 'state-12-1'
    assert state.link_healthy is True


def test_aggregator_is_deterministic_for_the_same_inputs() -> None:
    '''验证相同输入顺序和时间会生成相同的 WorldState。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = WorldStateAggregator()
    second = WorldStateAggregator()
    update_required_topics(first, now)
    update_required_topics(second, now)

    assert first.snapshot(now) == second.snapshot(now)


def test_aggregator_marks_state_stale_when_messages_stop() -> None:
    '''验证停止接收 PX4 消息后 WorldState 会变为不健康。'''

    received_at = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator(max_state_age_ms=1000)
    update_required_topics(aggregator, received_at)

    state = aggregator.snapshot(received_at + timedelta(milliseconds=1001))

    assert state.state_age_ms == 1001
    assert state.link_healthy is False
    assert state.is_fresh(1000, received_at + timedelta(milliseconds=1001)) is False
    assert all(fresh is False for fresh in state.health_flags.values())


def test_one_new_topic_does_not_hide_other_stale_topics() -> None:
    '''验证单个 Topic 更新不能掩盖其他必需 Topic 已过期。'''

    received_at = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator(max_state_age_ms=1000)
    update_required_topics(aggregator, received_at)
    later = received_at + timedelta(milliseconds=1001)
    aggregator.update_status(
        SimpleNamespace(
            timestamp=13,
            arming_state=2,
            ARMING_STATE_ARMED=2,
            nav_state=14,
            failsafe=False,
        ),
        later,
    )

    state = aggregator.snapshot(later)

    assert state.health_flags['status_fresh'] is True
    assert state.health_flags['local_position_fresh'] is False
    assert state.health_flags['land_detected_fresh'] is False
    assert state.link_healthy is False
