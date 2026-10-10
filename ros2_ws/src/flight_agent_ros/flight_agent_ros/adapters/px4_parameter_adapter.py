'''通过MAVLink参数协议读取PX4控制器参数。'''

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from time import monotonic

from pymavlink import mavutil  # type: ignore[import-untyped]

_SPEED_PARAM_NAME = 'MPC_XY_VEL_MAX'


class Px4ParameterReadError(RuntimeError):
    '''无法从目标PX4读取参数。'''


@dataclass(frozen=True)
class Px4ParameterReading:
    '''包含来源与接收时间的单次MAVLink参数读数。'''

    name: str
    value: float
    received_at: datetime
    system_id: int
    component_id: int


class Px4ParameterAdapter:
    '''只读当前PX4水平限速参数，不修改飞控配置。'''

    def __init__(
        self,
        *,
        endpoint: str = 'udpin:0.0.0.0:14540',
        target_system: int = 1,
        target_component: int = 1,
    ) -> None:
        '''配置MAVLink端点及目标PX4身份。'''

        if not endpoint:
            raise ValueError('endpoint must not be empty')
        if not 1 <= target_system <= 255 or not 1 <= target_component <= 255:
            raise ValueError('target system and component must be MAVLink IDs')
        self._endpoint = endpoint
        self._target_system = target_system
        self._target_component = target_component

    async def read_mpc_xy_vel_max(self, *, timeout_s: float = 3.0) -> Px4ParameterReading:
        '''在线程中读取PX4水平速度指令上限，避免阻塞事件循环。'''

        if not isfinite(timeout_s) or timeout_s <= 0.0:
            raise ValueError('timeout_s must be finite and positive')
        return await asyncio.to_thread(self._read_mpc_xy_vel_max_blocking, timeout_s)

    def _read_mpc_xy_vel_max_blocking(self, timeout_s: float) -> Px4ParameterReading:
        '''与PX4交换一次PARAM_REQUEST_READ和PARAM_VALUE。'''

        deadline = monotonic() + timeout_s
        try:
            with closing(mavutil.mavlink_connection(self._endpoint)) as connection:
                self._wait_for_px4_heartbeat(connection, deadline)
                connection.mav.param_request_read_send(
                    self._target_system,
                    self._target_component,
                    _SPEED_PARAM_NAME.encode('ascii'),
                    -1,
                )
                while (remaining_s := deadline - monotonic()) > 0.0:
                    message = connection.recv_match(
                        type='PARAM_VALUE', blocking=True, timeout=remaining_s
                    )
                    if message is None:
                        break
                    if not self._is_expected_source(message):
                        continue
                    if str(message.param_id).rstrip('\x00') != _SPEED_PARAM_NAME:
                        continue
                    if message.param_type != mavutil.mavlink.MAV_PARAM_TYPE_REAL32:
                        raise Px4ParameterReadError('PX4 speed parameter has unexpected type')
                    return Px4ParameterReading(
                        name=_SPEED_PARAM_NAME,
                        value=float(message.param_value),
                        received_at=datetime.now(UTC),
                        system_id=self._target_system,
                        component_id=self._target_component,
                    )
        except OSError as error:
            raise Px4ParameterReadError('PX4 MAVLink connection failed') from error
        raise Px4ParameterReadError('PX4 speed parameter read timed out')

    def _wait_for_px4_heartbeat(self, connection: mavutil.mavfile, deadline: float) -> None:
        '''请求参数前确认连接来自目标PX4。'''

        while (remaining_s := deadline - monotonic()) > 0.0:
            message = connection.recv_match(
                type='HEARTBEAT', blocking=True, timeout=remaining_s
            )
            if message is None:
                break
            if self._is_expected_source(message):
                return
        raise Px4ParameterReadError('expected PX4 heartbeat timed out')

    def _is_expected_source(self, message: mavutil.mavlink.MAVLink_message) -> bool:
        '''同时核对MAVLink系统ID和组件ID。'''

        return bool(
            message.get_srcSystem() == self._target_system
            and message.get_srcComponent() == self._target_component
        )
