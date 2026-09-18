'''WorldState aggregator tests.'''

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from flight_agent.components.vehicle.state import WorldStateAggregator


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
            gcs_connection_lost=False,
            high_latency_data_link_lost=False,
            pre_flight_checks_pass=True,
            failure_detector_status=0,
        ),
        received_at,
    )
    aggregator.update_land_detected(
        SimpleNamespace(timestamp=12, landed=False), received_at
    )
    aggregator.update_battery(
        SimpleNamespace(
            timestamp=13,
            connected=True,
            remaining=0.8,
            warning=0,
            faults=0,
        ),
        received_at,
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
    assert state.battery_percent == 80.0
    assert state.flight_mode == 'OFFBOARD'
    assert state.nav_state == '14'
    assert state.source_timestamp_us == 13
    assert state.state_id == 'state-13-1'
    assert state.link_healthy is True
    assert state.health_flags['runtime_healthy'] is True


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
    assert all(
        fresh is False
        for name, fresh in state.health_flags.items()
        if name.endswith('_fresh')
    )


def test_one_new_topic_does_not_hide_other_stale_topics() -> None:
    '''验证单个 Topic 更新不能掩盖其他必需 Topic 已过期。'''

    received_at = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator(max_state_age_ms=1000)
    update_required_topics(aggregator, received_at)
    later = received_at + timedelta(milliseconds=1001)
    aggregator.update_status(
        SimpleNamespace(
            timestamp=14,
            arming_state=2,
            ARMING_STATE_ARMED=2,
            nav_state=14,
            failsafe=False,
            gcs_connection_lost=False,
            high_latency_data_link_lost=False,
            pre_flight_checks_pass=True,
            failure_detector_status=0,
        ),
        later,
    )

    state = aggregator.snapshot(later)

    assert state.health_flags['status_fresh'] is True
    assert state.health_flags['local_position_fresh'] is False
    assert state.health_flags['land_detected_fresh'] is False
    assert state.health_flags['battery_fresh'] is False
    assert state.link_healthy is False


def test_invalid_battery_data_keeps_link_but_fails_runtime_health() -> None:
    '''验证无效电量不会伪装成链路故障，但会使运行健康失败。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    aggregator.update_battery(
        SimpleNamespace(
            timestamp=14,
            connected=False,
            remaining=-1.0,
            warning=4,
            faults=1,
        ),
        now,
    )

    state = aggregator.snapshot(now)

    assert state.battery_percent is None
    assert state.link_healthy is True
    assert state.health_flags['battery_connected'] is False
    assert state.health_flags['battery_valid'] is False
    assert state.health_flags['battery_fault_free'] is False
    assert state.health_flags['runtime_healthy'] is False


def test_home_ack_and_failsafe_are_mapped_to_runtime_health() -> None:
    '''验证 Home、ACK 与 Failsafe 字段映射到统一 WorldState。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    aggregator.update_home_position(
        SimpleNamespace(timestamp=14, valid_hpos=True, valid_alt=True), now
    )
    aggregator.update_command_ack(
        SimpleNamespace(timestamp=15, command=400, result=0), now
    )
    aggregator.update_status(
        SimpleNamespace(
            timestamp=16,
            arming_state=2,
            ARMING_STATE_ARMED=2,
            nav_state=5,
            failsafe=True,
            gcs_connection_lost=True,
            high_latency_data_link_lost=False,
            pre_flight_checks_pass=False,
            failure_detector_status=32,
        ),
        now,
    )

    state = aggregator.snapshot(now)

    assert state.home_valid is True
    assert state.last_command_ack == '400:ACCEPTED'
    assert state.flight_mode == 'AUTO_RTL'
    assert state.failsafe_active is True
    assert state.health_flags['gcs_connection_healthy'] is False
    assert state.health_flags['failure_detector_clear'] is False
    assert state.health_flags['runtime_healthy'] is False


def test_optional_event_age_does_not_make_required_state_stale() -> None:
    '''验证旧 Home 或 ACK 事件不会拉高持续状态的年龄。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    old_event_time = now - timedelta(seconds=10)
    aggregator.update_command_ack(
        SimpleNamespace(timestamp=14, command=400, result=0), old_event_time
    )

    state = aggregator.snapshot(now)

    assert state.state_age_ms == 0
    assert state.link_healthy is True
