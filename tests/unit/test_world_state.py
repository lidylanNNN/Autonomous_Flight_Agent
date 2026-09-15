'''WorldState contract tests.'''

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from flight_agent.contracts.world_state import (
    CoordinateFrame,
    Vector3,
    WorldState,
    ned_to_enu,
)


def make_state(observed_at: datetime) -> WorldState:
    '''构造测试用状态快照。'''

    return WorldState(
        observed_at=observed_at,
        source_timestamp_us=100,
        frame=CoordinateFrame.ENU,
        position_m=Vector3(x=1, y=2, z=3),
        velocity_mps=Vector3(x=0, y=0, z=0),
        armed=False,
        landed=True,
        connected=True,
    )


def test_ned_to_enu_swaps_horizontal_axes_and_inverts_down() -> None:
    '''验证 NED 到 ENU 的确定性转换。'''

    assert ned_to_enu(Vector3(x=1, y=2, z=3)) == Vector3(x=2, y=1, z=-3)


def test_world_state_detects_stale_snapshot() -> None:
    '''验证状态新鲜度边界。'''

    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = make_state(now - timedelta(seconds=1.1))

    assert state.is_fresh(2, now) is True
    assert state.is_fresh(1, now) is False


def test_world_state_requires_timezone() -> None:
    '''验证无时区时间会被拒绝。'''

    with pytest.raises(ValidationError, match='timezone'):
        make_state(datetime(2026, 1, 1))  # noqa: DTZ001
