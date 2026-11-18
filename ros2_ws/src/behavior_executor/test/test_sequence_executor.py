"""Verify the ROS service dispatch path through the real sequence executor."""

from __future__ import annotations

import json
from threading import Event, Thread
from time import monotonic

import pytest
import rclpy
from behavior_executor.sequence_executor import SequenceExecutor
from rclpy.executors import MultiThreadedExecutor
from rosllm_srvs.srv import AtomicAction, ExecuteBehavior


def test_default_timeout_allows_action_longer_than_thirty_seconds() -> None:
    '''通过真实 ROS 服务等待 31 秒，验证默认预算不再提前截断动作。'''
    rclpy.init()
    sequence = SequenceExecutor()
    actions = rclpy.create_node('mx4_long_action')
    caller = rclpy.create_node('mx4_long_action_caller')
    release_action = Event()
    elapsed: list[float] = []

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''用延迟响应替身模拟合法的长动作，不调用 PX4。'''
        started = monotonic()
        release_action.wait(timeout=31.0)
        elapsed.append(monotonic() - started)
        response.success = True
        response.output = request.input
        return response

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
            ExecuteBehavior.Request(behavior='{"name":"rtl","input":"{}"}'),
            timeout_sec=40,
        )
        assert result is not None and result.success
        assert len(elapsed) == 1 and elapsed[0] >= 31.0
    finally:
        release_action.set()
        executor.shutdown()
        thread.join(timeout=5)
        for node in (caller, actions, sequence):
            node.destroy_node()
        rclpy.shutdown()


def test_sequence_executor_forwards_previous_output_and_stops_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''验证前项传递、预校验及各类终止路径的结果封装。'''
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
        with monkeypatch.context() as test_temp_patch:
            def test_temp_fail_call(*_args: object, **_kwargs: object) -> None:
                '''注入调用异常，验证已完成的首项仍被返回。'''
                raise RuntimeError('injected call failure')
            test_temp_patch.setattr(sequence._action_clients['rtl'], 'call', test_temp_fail_call)
            result = client.call(ExecuteBehavior.Request(behavior=f'{first}\n{second}'), timeout_sec=3)
            assert result is not None and not result.success
            steps = json.loads(result.message)['steps']
            assert steps[0]['output'] == 'hold-evidence'
            assert steps[1]['success'] is None and steps[1]['output'] is None
            assert 'ATOMIC_ACTION_CALL_FAILED name=rtl' in steps[1]['error']

        calls.clear()
        fail_hold = True
        failed = client.call(ExecuteBehavior.Request(behavior=f'{first}\n{second}'), timeout_sec=3)
        assert failed is not None and not failed.success
        assert 'hold-refused' in failed.message
        assert calls == [('hold', 'wait', '')]
        assert json.loads(failed.message)['steps'] == [
            {'index': 1, 'name': 'hold', 'success': False,
             'output': 'hold-refused', 'error': None},
        ]

        calls.clear()
        invalid = client.call(
            ExecuteBehavior.Request(behavior=f'{first}\n{{"name":"unknown","input":"x"}}'),
            timeout_sec=3,
        )
        assert invalid is not None and not invalid.success
        assert calls == []
        assert json.loads(invalid.message)['steps'] == []

        empty = client.call(ExecuteBehavior.Request(behavior=''), timeout_sec=3)
        assert empty is not None and not empty.success
        assert calls == []

        unavailable = client.call(
            ExecuteBehavior.Request(behavior='{"name":"takeoff","input":"1"}'),
            timeout_sec=3,
        )
        assert unavailable is not None and not unavailable.success
        assert 'ATOMIC_ACTION_UNAVAILABLE name=takeoff' == json.loads(unavailable.message)['summary']
        assert calls == []
        steps = json.loads(unavailable.message)['steps']
        assert steps[0]['success'] is None and steps[0]['output'] is None
        assert steps[0]['error'] == 'ATOMIC_ACTION_UNAVAILABLE name=takeoff'
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in (caller, actions, sequence):
            node.destroy_node()
        rclpy.shutdown()


def test_action_response_timeout_stops_before_next_action() -> None:
    '''超时不生成伪造的服务响应且不执行后项。'''
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
        assert json.loads(result.message)['summary'] == 'ATOMIC_ACTION_TIMEOUT name=hold'
        assert rtl_calls == []
        assert json.loads(result.message)['steps'] == [
            {'index': 1, 'name': 'hold', 'success': None, 'output': None,
             'error': 'ATOMIC_ACTION_TIMEOUT name=hold'},
        ]
    finally:
        release_hold.set()
        executor.shutdown()
        thread.join(timeout=5)
        for node in (caller, actions, sequence):
            node.destroy_node()
        rclpy.shutdown()
