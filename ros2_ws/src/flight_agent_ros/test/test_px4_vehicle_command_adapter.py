'''Tests for PX4 VehicleCommand transport acknowledgement lifecycle.'''

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import rclpy
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    Px4CommandAckStatus,
    Px4VehicleCommandAdapter,
    VehicleCommandParameters,
)
from px4_msgs.msg import VehicleCommand, VehicleCommandAck


def test_vehicle_command_is_published_and_accepted_ack_is_correlated() -> None:
    '''同一 command 的 ACCEPTED ACK 返回给提交时的 execution_id。'''

    asyncio.run(_assert_accepted_ack_is_correlated())


async def _assert_accepted_ack_is_correlated() -> None:
    rclpy.init()
    adapter = Px4VehicleCommandAdapter()
    try:
        task = asyncio.create_task(
            adapter.submit_and_wait(
                execution_id='exec-1',
                command_id=VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH,
                timeout_s=1.0,
                parameters=VehicleCommandParameters(param1=7.0),
            )
        )
        await asyncio.sleep(0)
        adapter._handle_command_ack(
            SimpleNamespace(
                command=VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH,
                result=VehicleCommandAck.VEHICLE_CMD_RESULT_ACCEPTED,
                result_param1=0,
                result_param2=0,
            )
        )
        outcome = await task
        assert outcome.execution_id == 'exec-1'
        assert outcome.status is Px4CommandAckStatus.ACCEPTED
        assert outcome.ack_name == 'ACCEPTED'
        assert outcome.failure_code is None
    finally:
        adapter.destroy_node()
        rclpy.shutdown()


def test_rejected_ack_uses_a_stable_failure_code() -> None:
    '''PX4 拒绝与任务失败分开记录，并带稳定错误码。'''

    asyncio.run(_assert_rejected_ack_has_failure_code())


async def _assert_rejected_ack_has_failure_code() -> None:
    rclpy.init()
    adapter = Px4VehicleCommandAdapter()
    try:
        task = asyncio.create_task(
            adapter.submit_and_wait(execution_id='exec-2', command_id=400, timeout_s=1.0)
        )
        await asyncio.sleep(0)
        adapter._handle_command_ack(
            SimpleNamespace(
                command=400,
                result=VehicleCommandAck.VEHICLE_CMD_RESULT_DENIED,
                result_param1=3,
                result_param2=9,
            )
        )
        outcome = await task
        assert outcome.status is Px4CommandAckStatus.REJECTED
        assert outcome.ack_name == 'DENIED'
        assert outcome.failure_code == 'PX4_ACK_DENIED'
        assert outcome.result_param1 == 3
        assert outcome.result_param2 == 9
    finally:
        adapter.destroy_node()
        rclpy.shutdown()


def test_timeout_and_repeated_cancel_have_deterministic_outcomes() -> None:
    '''未收到 ACK 会超时；第二次取消没有额外副作用。'''

    asyncio.run(_assert_timeout_and_cancellation())


async def _assert_timeout_and_cancellation() -> None:
    rclpy.init()
    adapter = Px4VehicleCommandAdapter()
    try:
        timeout = await adapter.submit_and_wait(
            execution_id='exec-timeout', command_id=400, timeout_s=0.001
        )
        assert timeout.status is Px4CommandAckStatus.TIMED_OUT
        assert timeout.failure_code == 'PX4_ACK_TIMEOUT'

        task = asyncio.create_task(
            adapter.submit_and_wait(execution_id='exec-cancel', command_id=400, timeout_s=1.0)
        )
        await asyncio.sleep(0)
        assert await adapter.cancel('exec-cancel') is True
        assert await adapter.cancel('exec-cancel') is False
        cancelled = await task
        assert cancelled.status is Px4CommandAckStatus.CANCELLED
        assert cancelled.failure_code == 'CANCELLED_BY_REQUEST'
        assert await adapter.cancel('unknown') is False
    finally:
        adapter.destroy_node()
        rclpy.shutdown()
