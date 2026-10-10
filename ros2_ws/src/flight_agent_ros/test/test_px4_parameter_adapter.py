'''测试通过MAVLink读取PX4水平限速参数。'''

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from flight_agent_ros.adapters.px4_parameter_adapter import (
    Px4ParameterAdapter,
    Px4ParameterReadError,
    Px4ParameterReading,
)
from pymavlink import mavutil


class _FakeConnection:
    '''模拟一次短生命周期MAVLink连接。'''

    def __init__(self, messages: list[SimpleNamespace]) -> None:
        '''保存待接收消息及参数请求。'''

        self.messages = messages
        self.requests: list[tuple[int, int, bytes, int]] = []
        self.mav = SimpleNamespace(param_request_read_send=self._record_request)
        self.closed = False

    def recv_match(self, *, type: str, blocking: bool, timeout: float) -> SimpleNamespace | None:
        '''返回指定类型的下一条待接收消息。'''

        assert blocking is True
        assert timeout > 0.0
        for index, message in enumerate(self.messages):
            if message.get_type() == type:
                return self.messages.pop(index)
        return None

    def close(self) -> None:
        '''记录连接关闭动作。'''

        self.closed = True

    def _record_request(
        self, system: int, component: int, name: bytes, index: int
    ) -> None:
        '''记录发送给PX4的完整参数请求。'''

        self.requests.append((system, component, name, index))


def _message(
    message_type: str,
    *,
    system: int = 1,
    component: int = 1,
    param_id: str = 'MPC_XY_VEL_MAX',
    value: float = 7.5,
    param_type: int = mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
) -> SimpleNamespace:
    '''构造模拟的MAVLink心跳或参数响应。'''

    return SimpleNamespace(
        get_type=lambda: message_type,
        get_srcSystem=lambda: system,
        get_srcComponent=lambda: component,
        param_id=param_id,
        param_value=value,
        param_type=param_type,
    )


def test_reads_only_matching_px4_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    '''忽略其他飞行器及参数，返回带来源的读数。'''

    connection = _FakeConnection([
        _message('HEARTBEAT', system=2),
        _message('HEARTBEAT'),
        _message('PARAM_VALUE', system=2),
        _message('PARAM_VALUE', param_id='MPC_XY_CRUISE'),
        _message('PARAM_VALUE'),
    ])
    monkeypatch.setattr(mavutil, 'mavlink_connection', lambda endpoint: connection)

    reading = asyncio.run(Px4ParameterAdapter().read_mpc_xy_vel_max())

    assert reading.name == 'MPC_XY_VEL_MAX'
    assert reading.value == 7.5
    assert reading.system_id == 1
    assert reading.component_id == 1
    assert reading.received_at.tzinfo is UTC
    assert connection.requests == [(1, 1, b'MPC_XY_VEL_MAX', -1)]
    assert connection.closed is True


def test_missing_matching_heartbeat_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    '''未确认目标PX4身份前不请求参数。'''

    connection = _FakeConnection([_message('HEARTBEAT', system=2)])
    monkeypatch.setattr(mavutil, 'mavlink_connection', lambda endpoint: connection)

    with pytest.raises(Px4ParameterReadError, match='heartbeat'):
        asyncio.run(Px4ParameterAdapter().read_mpc_xy_vel_max())

    assert connection.requests == []
    assert connection.closed is True


def test_missing_matching_parameter_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    '''不接受其他飞行器或其他参数的响应。'''

    connection = _FakeConnection([
        _message('HEARTBEAT'),
        _message('PARAM_VALUE', system=2),
        _message('PARAM_VALUE', param_id='MPC_XY_CRUISE'),
    ])
    monkeypatch.setattr(mavutil, 'mavlink_connection', lambda endpoint: connection)

    with pytest.raises(Px4ParameterReadError, match='timed out'):
        asyncio.run(Px4ParameterAdapter().read_mpc_xy_vel_max())

    assert connection.closed is True


def test_unexpected_parameter_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    '''拒绝同名但MAVLink类型错误的响应。'''

    connection = _FakeConnection([
        _message('HEARTBEAT'),
        _message('PARAM_VALUE', param_type=mavutil.mavlink.MAV_PARAM_TYPE_INT32),
    ])
    monkeypatch.setattr(mavutil, 'mavlink_connection', lambda endpoint: connection)

    with pytest.raises(Px4ParameterReadError, match='unexpected type'):
        asyncio.run(Px4ParameterAdapter().read_mpc_xy_vel_max())

    assert connection.closed is True


@pytest.mark.parametrize('timeout_s', [0.0, -1.0, float('nan')])
def test_invalid_timeout_is_rejected(timeout_s: float) -> None:
    '''拒绝非正或非有限的读取超时值。'''

    with pytest.raises(ValueError, match='timeout_s'):
        asyncio.run(Px4ParameterAdapter().read_mpc_xy_vel_max(timeout_s=timeout_s))


def test_validates_fresh_speed_limit_reading() -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    reading = Px4ParameterReading(
        name='MPC_XY_VEL_MAX', value=7.5, received_at=now - timedelta(seconds=1),
        system_id=1, component_id=1,
    )

    assert Px4ParameterAdapter().validate_speed_limit_reading(
        reading, max_age_s=1.0, now=now
    ) == 7.5


@pytest.mark.parametrize(
    ('changes', 'error'),
    [
        ({'name': 'MPC_XY_CRUISE'}, 'source'),
        ({'system_id': 2}, 'source'),
        ({'component_id': 2}, 'source'),
        ({'value': float('nan')}, 'value'),
        ({'value': 0.0}, 'value'),
        ({'value': True}, 'value'),
        ({'received_at': datetime(2026, 10, 9, tzinfo=UTC)}, 'stale'),
        ({'received_at': datetime(2026, 10, 11, tzinfo=UTC)}, 'future'),
        ({'received_at': datetime(2026, 10, 10, tzinfo=UTC).replace(tzinfo=None)}, 'timezone-aware'),
    ],
)
def test_rejects_untrusted_speed_limit_reading(changes: dict[str, object], error: str) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    fields: dict[str, object] = {
        'name': 'MPC_XY_VEL_MAX', 'value': 7.5, 'received_at': now,
        'system_id': 1, 'component_id': 1,
    }
    fields.update(changes)
    reading = Px4ParameterReading(**fields)

    with pytest.raises(Px4ParameterReadError, match=error):
        Px4ParameterAdapter().validate_speed_limit_reading(reading, max_age_s=1.0, now=now)


@pytest.mark.parametrize('max_age_s', [0.0, -1.0, float('nan'), True])
def test_invalid_reading_age_limit_is_rejected(max_age_s: float) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    reading = Px4ParameterReading('MPC_XY_VEL_MAX', 7.5, now, 1, 1)

    with pytest.raises(ValueError, match='max_age_s'):
        Px4ParameterAdapter().validate_speed_limit_reading(reading, max_age_s=max_age_s, now=now)
