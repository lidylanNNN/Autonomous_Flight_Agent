'''WorldState aggregator tests.'''

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from flight_agent.components.vehicle.state import WorldStateAggregator
from flight_agent.contracts import FreshnessLevel, Vector3, WorldState, WorldStateFreshness


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
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, received_at)

    state = aggregator.snapshot(received_at + timedelta(milliseconds=1501))

    assert state.state_age_ms == 1501
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
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, received_at)
    later = received_at + timedelta(milliseconds=1501)
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


@pytest.mark.parametrize(
    ('warning', 'faults', 'expected_healthy'),
    [
        (0, 0, True),   # NONE
        (1, 0, True),   # LOW: task threshold is checked separately
        (2, 0, False),  # CRITICAL
        (3, 0, False),  # EMERGENCY
        (4, 0, False),  # FAILED
        (5, 0, False),  # Unknown: fail closed
        (6, 0, False),  # UNHEALTHY
        (7, 0, False),  # CHARGING
        (0, 1, False),  # Fault bit even without a warning
    ],
)
def test_battery_warning_and_faults_affect_runtime_health(
    warning: int, faults: int, expected_healthy: bool
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    aggregator.update_battery(
        SimpleNamespace(
            timestamp=14,
            connected=True,
            remaining=0.8,
            warning=warning,
            faults=faults,
        ),
        now,
    )

    state = aggregator.snapshot(now)

    assert state.battery_percent == 80.0
    assert state.health_flags['battery_fault_free'] is expected_healthy
    assert state.health_flags['runtime_healthy'] is expected_healthy


def test_home_ack_and_failsafe_are_mapped_to_runtime_health() -> None:
    '''验证 Home、ACK 与 Failsafe 字段映射到统一 WorldState。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    aggregator.update_home_position(
        SimpleNamespace(
            timestamp=14,
            lat=30.123,
            lon=120.456,
            alt=42.5,
            valid_hpos=True,
            valid_alt=True,
            valid_lpos=True,
            x=12.0,
            y=-7.0,
            z=1.0,
        ),
        now,
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
    assert state.home_position_wgs84 is not None
    assert state.home_position_wgs84.latitude_deg == 30.123
    assert state.home_position_wgs84.longitude_deg == 120.456
    assert state.home_position_wgs84.altitude_amsl_m == 42.5
    assert state.home_position_ned_m == Vector3(x=12.0, y=-7.0, z=1.0)
    assert state.last_command_ack == '400:ACCEPTED'
    assert state.flight_mode == 'AUTO_RTL'
    assert state.failsafe_active is True
    assert state.health_flags['gcs_connection_healthy'] is False
    assert state.health_flags['failure_detector_clear'] is False
    assert state.health_flags['runtime_healthy'] is False


def test_invalid_home_sample_clears_previous_reference() -> None:
    '''验证无效 Home 样本不会保留先前可能过期的全局参考。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    aggregator.update_home_position(
        SimpleNamespace(
            timestamp=14,
            lat=30.0,
            lon=120.0,
            alt=50.0,
            valid_hpos=True,
            valid_alt=True,
            valid_lpos=True,
            x=12.0,
            y=-7.0,
            z=1.0,
        ),
        now,
    )
    aggregator.update_home_position(
        SimpleNamespace(
            timestamp=15,
            lat=float('nan'),
            lon=120.0,
            alt=50.0,
            valid_hpos=False,
            valid_alt=True,
            valid_lpos=False,
            x=float('nan'),
            y=0.0,
            z=0.0,
        ),
        now,
    )

    state = aggregator.snapshot(now)

    assert state.home_valid is False
    assert state.home_position_wgs84 is None
    assert state.home_position_ned_m is None


def test_invalid_local_home_does_not_invalidate_global_home() -> None:
    '''验证局部坐标异常不会误清除有效的全球Home参考。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, now)
    aggregator.update_home_position(
        SimpleNamespace(
            timestamp=14,
            lat=30.0,
            lon=120.0,
            alt=50.0,
            valid_hpos=True,
            valid_alt=True,
            valid_lpos=True,
            x=float('nan'),
            y=0.0,
            z=0.0,
        ),
        now,
    )

    state = aggregator.snapshot(now)

    assert state.home_valid is True
    assert state.home_position_wgs84 is not None
    assert state.home_position_ned_m is None


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


@pytest.mark.parametrize(('test_input_field', 'test_input_level', 'expected_output_limit'), [
    ('local_position_received_at', FreshnessLevel.FAST, 500),
    ('status_received_at', FreshnessLevel.NORMAL, 1000),
    ('battery_received_at', FreshnessLevel.NORMAL, 1000),
    ('land_detected_received_at', FreshnessLevel.SLOW, 1500),
])
@pytest.mark.parametrize('test_input_offset_ms', [0, 0.001, None])
def test_temp_grade_boundaries(
    test_input_field: str, test_input_level: FreshnessLevel, expected_output_limit: int,
    test_input_offset_ms: float | None,
) -> None:
    '''每类报文在自身等级上限允许，超过一微秒或缺失时拒绝。'''
    test_temp_now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, test_temp_now)
    state = aggregator.snapshot(test_temp_now)
    assert state.freshness is not None
    assert test_input_level.max_age_ms == expected_output_limit
    state = state.model_copy(update={'freshness': state.freshness.model_copy(update={
        test_input_field: (None if test_input_offset_ms is None else
            test_temp_now - timedelta(milliseconds=expected_output_limit + test_input_offset_ms)),
    })})
    assert state.is_fresh(1000, test_temp_now) is (test_input_offset_ms == 0)


def test_temp_slow_land_message_does_not_hide_stale_position() -> None:
    '''生产聚合按慢速档接纳着陆消息，同时不放过快速档过期的位置。'''
    test_temp_now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, test_temp_now)
    aggregator.update_land_detected(SimpleNamespace(timestamp=12, landed=False),
                                    test_temp_now - timedelta(milliseconds=1200))
    state = aggregator.snapshot(test_temp_now)
    assert state.state_age_ms == 1200 and state.link_healthy
    assert state.is_fresh(1000, test_temp_now)
    assert state.is_fresh(1000, test_temp_now + timedelta(milliseconds=301)) is False
    assert state.freshness is not None
    state = state.model_copy(update={'freshness': state.freshness.model_copy(update={
        'local_position_received_at': test_temp_now - timedelta(milliseconds=501),
    })})
    assert state.is_fresh(1000, test_temp_now) is False


def test_temp_snapshot_cache_has_its_own_limit() -> None:
    '''所有报文仍在等级内时，过期的快照缓存仍被拒绝。'''
    test_temp_now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, test_temp_now)
    state = aggregator.snapshot(test_temp_now)
    assert state.is_fresh(100, test_temp_now + timedelta(milliseconds=100))
    assert state.is_fresh(100, test_temp_now + timedelta(milliseconds=101)) is False


def test_temp_new_trace_preserves_grade_timestamps(tmp_path: Path) -> None:
    '''生产聚合状态经正式 Trace 写入回放后仍保持分级判定。'''
    from flight_agent.components.tracing import TraceRecorder, TraceReplay

    test_temp_now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, test_temp_now)
    state = aggregator.snapshot(test_temp_now)
    test_temp_path = tmp_path / 'world.jsonl'
    TraceRecorder(test_temp_path).record_world_state(state)
    test_temp_record, = TraceReplay(test_temp_path).iter_world_state_records()
    assert test_temp_record.state == state
    assert test_temp_record.state.is_fresh(1000, test_temp_now)
    assert test_temp_record.state.is_fresh(1000, test_temp_now + timedelta(milliseconds=501)) is False


def test_temp_partial_or_naive_new_metadata_is_rejected() -> None:
    '''部分新字段及无时区时间不被当作旧格式或有效新格式。'''
    from pydantic import ValidationError

    test_temp_now = datetime(2026, 1, 1, tzinfo=UTC)
    aggregator = WorldStateAggregator()
    update_required_topics(aggregator, test_temp_now)
    test_temp_payload = aggregator.snapshot(test_temp_now).model_dump(mode='json')
    del test_temp_payload['freshness']['status_received_at']
    with pytest.raises(ValidationError):
        WorldState.model_validate(test_temp_payload)
    with pytest.raises(ValidationError, match='timezone'):
        WorldStateFreshness(snapshot_at=test_temp_now.replace(tzinfo=None),
            local_position_received_at=None, status_received_at=None,
            battery_received_at=None, land_detected_received_at=None)
    with pytest.raises(ValueError):
        FreshnessLevel('unknown')
