'''Tests for PX4 VehicleCommand transport acknowledgement lifecycle.'''

from __future__ import annotations

import asyncio
from threading import Thread
from types import SimpleNamespace

import pytest
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
        ack_thread = Thread(
            target=adapter._handle_command_ack,
            args=(SimpleNamespace(
                command=VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH,
                result=VehicleCommandAck.VEHICLE_CMD_RESULT_ACCEPTED,
                result_param1=0,
                result_param2=0,
            ),),
        )
        ack_thread.start()
        ack_thread.join(timeout=1.0)
        assert not ack_thread.is_alive()
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


def test_adapter_can_be_reused_by_a_new_event_loop() -> None:
    '''同一适配器在成功、超时、取消和锁竞争后，能在新循环再次接收 ACK。'''

    async def timeout_with_contention() -> None:
        '''让异步锁实际发生竞争，再通过超时释放全部等待。'''
        outcomes = await asyncio.gather(*(
            adapter.submit_and_wait(execution_id=str(i), command_id=400, timeout_s=0.01)
            for i in range(2)
        ))
        assert all(item.status is Px4CommandAckStatus.TIMED_OUT for item in outcomes)

    async def cancel_task() -> None:
        '''取消正在等待 ACK 的协程，确认归属计数和等待状态可以释放。'''
        task = asyncio.create_task(adapter.submit_and_wait(
            execution_id='cancelled', command_id=400, timeout_s=1.0,
        ))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    rclpy.init()
    adapter = Px4VehicleCommandAdapter()
    try:
        asyncio.run(_accept_command(adapter, 'first'))
        asyncio.run(_accept_command(adapter, 'second'))
        asyncio.run(timeout_with_contention())
        asyncio.run(_accept_command(adapter, 'after-timeout'))
        asyncio.run(cancel_task())
        asyncio.run(_accept_command(adapter, 'after-cancel'))
    finally:
        adapter.destroy_node()
        rclpy.shutdown()


async def _accept_command(adapter: Px4VehicleCommandAdapter, execution_id: str) -> None:
    '''从另一线程送入 ACK，验证当前循环收到对应执行结果。'''
    task = asyncio.create_task(adapter.submit_and_wait(
        execution_id=execution_id, command_id=400, timeout_s=1.0,
    ))
    await asyncio.sleep(0)
    thread = Thread(target=adapter._handle_command_ack, args=(SimpleNamespace(
        command=400, result=VehicleCommandAck.VEHICLE_CMD_RESULT_ACCEPTED,
        result_param1=0, result_param2=0,
    ),))
    thread.start()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    outcome = await task
    assert outcome.execution_id == execution_id
    assert outcome.status is Px4CommandAckStatus.ACCEPTED


def test_busy_adapter_rejects_another_event_loop() -> None:
    '''有执行中或排队中的调用时，其他循环不能抢走适配器归属。'''

    async def foreign_request() -> None:
        '''在另一个循环提交命令，必须明确拒绝。'''
        with pytest.raises(RuntimeError, match='active on another event loop'):
            await adapter.submit_and_wait(
                execution_id='foreign', command_id=400, timeout_s=0.01,
            )

    def check_foreign_loop() -> None:
        '''由另一线程运行独立循环，并把断言失败带回测试线程。'''
        errors: list[BaseException] = []

        def run() -> None:
            '''收集另一线程的异常，避免测试静默通过。'''
            try:
                asyncio.run(foreign_request())
            except BaseException as error:  # noqa: BLE001 - 在主测试线程重新抛出
                errors.append(error)

        thread = Thread(target=run)
        thread.start()
        thread.join(timeout=1.0)
        assert not thread.is_alive()
        if errors:
            raise errors[0]

    async def exercise() -> None:
        '''保留执行中与排队中的任务，检查拒绝后原任务仍正常完成。'''
        tasks = [asyncio.create_task(adapter.submit_and_wait(
            execution_id=str(i), command_id=400, timeout_s=2.0,
        )) for i in range(2)]
        await asyncio.sleep(0)
        check_foreign_loop()
        assert await adapter.cancel('0')
        assert (await tasks[0]).status is Px4CommandAckStatus.CANCELLED
        await asyncio.sleep(0)
        check_foreign_loop()
        assert await adapter.cancel('1')
        assert (await tasks[1]).status is Px4CommandAckStatus.CANCELLED

    rclpy.init()
    adapter = Px4VehicleCommandAdapter()
    try:
        asyncio.run(exercise())
        asyncio.run(_accept_command(adapter, 'after-busy'))
    finally:
        adapter.destroy_node()
        rclpy.shutdown()
