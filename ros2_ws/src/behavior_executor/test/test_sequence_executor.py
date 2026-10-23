"""Verify the ROS service dispatch path through the real sequence executor."""

from __future__ import annotations

from threading import Event, Thread

import rclpy
from behavior_executor.sequence_executor import SequenceExecutor
from rclpy.executors import MultiThreadedExecutor
from rosllm_srvs.srv import AtomicAction, ExecuteBehavior


def test_sequence_executor_forwards_previous_output_and_stops_on_failure() -> None:
    rclpy.init()
    sequence = SequenceExecutor(action_timeout_s=0.5)
    actions = rclpy.create_node('mx3_test_actions')
    caller = rclpy.create_node('mx3_test_caller')
    calls: list[tuple[str, str, str]] = []
    fail_hold = False

    def hold(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        calls.append(('hold', request.input, request.prev_output))
        response.success = not fail_hold
        response.output = 'hold-evidence' if response.success else 'hold-refused'
        return response

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        calls.append(('rtl', request.input, request.prev_output))
        response.success = True
        response.output = 'rtl-evidence'
        return response

    actions.create_service(AtomicAction, 'hold', hold)
    actions.create_service(AtomicAction, 'rtl', rtl)
    executor = MultiThreadedExecutor(num_threads=2)
    for node in (sequence, actions, caller):
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    client = caller.create_client(ExecuteBehavior, 'execute_behavior')
    first = '{"name":"hold","input":"wait"}'
    second = '{"name":"rtl","input":"home"}'

    try:
        assert client.wait_for_service(timeout_sec=2)
        success = client.call(ExecuteBehavior.Request(behavior=f'{first}\n{second}'), timeout_sec=3)
        assert success is not None and success.success
        assert calls == [('hold', 'wait', ''), ('rtl', 'home', 'hold-evidence')]

        calls.clear()
        fail_hold = True
        failed = client.call(ExecuteBehavior.Request(behavior=f'{first}\n{second}'), timeout_sec=3)
        assert failed is not None and not failed.success
        assert 'hold-refused' in failed.message
        assert calls == [('hold', 'wait', '')]

        calls.clear()
        invalid = client.call(
            ExecuteBehavior.Request(behavior=f'{first}\n{{"name":"unknown","input":"x"}}'),
            timeout_sec=3,
        )
        assert invalid is not None and not invalid.success
        assert calls == []

        empty = client.call(ExecuteBehavior.Request(behavior=''), timeout_sec=3)
        assert empty is not None and not empty.success
        assert calls == []

        unavailable = client.call(
            ExecuteBehavior.Request(behavior='{"name":"takeoff","input":"1"}'),
            timeout_sec=3,
        )
        assert unavailable is not None and not unavailable.success
        assert 'ATOMIC_ACTION_UNAVAILABLE name=takeoff' == unavailable.message
        assert calls == []
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in (caller, actions, sequence):
            node.destroy_node()
        rclpy.shutdown()


def test_action_response_timeout_stops_before_next_action() -> None:
    rclpy.init()
    sequence = SequenceExecutor(action_timeout_s=1.0)
    actions = rclpy.create_node('mx3_slow_action')
    caller = rclpy.create_node('mx3_timeout_caller')
    hold_started = Event()
    release_hold = Event()
    rtl_calls: list[str] = []

    def hold(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        hold_started.set()
        release_hold.wait(timeout=3)
        response.success = True
        response.output = request.input
        return response

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        rtl_calls.append(request.input)
        response.success = True
        return response

    actions.create_service(AtomicAction, 'hold', hold)
    actions.create_service(AtomicAction, 'rtl', rtl)
    executor = MultiThreadedExecutor(num_threads=2)
    for node in (sequence, actions, caller):
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    client = caller.create_client(ExecuteBehavior, 'execute_behavior')

    try:
        assert client.wait_for_service(timeout_sec=2)
        result = client.call(
            ExecuteBehavior.Request(behavior=(
                '{"name":"hold","input":"wait"}\n'
                '{"name":"rtl","input":"home"}'
            )),
            timeout_sec=3,
        )
        assert hold_started.is_set()
        assert result is not None and not result.success
        assert result.message == 'ATOMIC_ACTION_TIMEOUT name=hold'
        assert rtl_calls == []
    finally:
        release_hold.set()
        executor.shutdown()
        thread.join(timeout=5)
        for node in (caller, actions, sequence):
            node.destroy_node()
        rclpy.shutdown()
