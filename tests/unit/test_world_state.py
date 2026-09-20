'''WorldState contract tests.'''

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from flight_agent.contracts import (
    GlobalPosition,
    Vector3,
    WorldState,
    enu_to_ned,
    ned_to_enu,
)


def make_state(received_at: datetime, state_age_ms: int = 0) -> WorldState:
    '''构造测试用状态快照。'''

    return WorldState(
        state_id='state-100-1',
        source_timestamp_us=100,
        received_at=received_at,
        state_age_ms=state_age_ms,
        position_ned_m=Vector3(x=1, y=2, z=3),
        velocity_ned_mps=Vector3(x=0, y=0, z=0),
        armed=False,
        landed=True,
        position_valid=True,
        link_healthy=True,
    )


def test_coordinate_conversion_has_correct_signs_and_round_trips() -> None:
    '''验证 NED 与 ENU 转换的轴、符号和往返结果。'''

    ned = Vector3(x=1, y=2, z=3)

    assert ned_to_enu(ned) == Vector3(x=2, y=1, z=-3)
    assert enu_to_ned(ned_to_enu(ned)) == ned


def test_world_state_detects_stale_snapshot_from_receive_time() -> None:
    '''验证状态年龄基于消息接收时间而不是快照生成时间。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = make_state(now - timedelta(milliseconds=1100))

    assert state.is_fresh(2000, now) is True
    assert state.is_fresh(1000, now) is False


def test_world_state_age_never_drops_below_recorded_age() -> None:
    '''验证时钟回拨不会让已记录的状态年龄变小。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = make_state(now, state_age_ms=500)

    assert state.age_ms(now - timedelta(seconds=1)) == 500


def test_world_state_requires_timezone() -> None:
    '''验证无时区接收时间会被拒绝。'''

    with pytest.raises(ValidationError, match='timezone'):
        make_state(datetime(2026, 1, 1))  # noqa: DTZ001


def test_global_position_validates_wgs84_ranges_and_finite_altitude() -> None:
    '''验证 Home 参考拒绝越界经纬度和非有限 AMSL 高度。'''

    with pytest.raises(ValidationError):
        GlobalPosition(latitude_deg=91.0, longitude_deg=120.0, altitude_amsl_m=30.0)
    with pytest.raises(ValidationError):
        GlobalPosition(
            latitude_deg=30.0,
            longitude_deg=120.0,
            altitude_amsl_m=float('nan'),
        )


def test_world_state_accepts_trace_without_optional_home_reference() -> None:
    '''验证新增可选字段不会破坏旧 WorldState Trace。'''

    payload = make_state(datetime(2026, 1, 1, tzinfo=UTC)).model_dump(mode='json')
    payload.pop('home_position_wgs84')

    assert WorldState.model_validate(payload).home_position_wgs84 is None
