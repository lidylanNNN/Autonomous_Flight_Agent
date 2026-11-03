"""Exercise the real human entrypoint through ROS observation and LLM services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import rclpy
from agent_comm.llm_node import _FIXED_BEHAVIOR, LLMNode
from agent_comm.ros_agent_srv import _BEHAVIOR_TIMEOUT_S, RosAgent, RosEnv, main
from behavior_executor.sequence_executor import _DEFAULT_TIMEOUT_S, _MAX_ACTIONS, SequenceExecutor
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from observation_manager.observation_manager_node import ObservationManagerNode
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from rosllm_srvs.srv import LLM, AtomicAction, ExecuteBehavior, Observation

from flight_agent.components.skills.registry import list_skill_specs
from flight_agent.components.tracing import TraceRecorder, TraceReplay
from flight_agent.components.vehicle.state import WorldStateAggregator
from flight_agent.contracts import HoldArgs, RTLArgs


def test_timeout_budgets_cover_skills_and_full_sequence() -> None:
    '''防止 Skill 或执行器预算增长后，上层仍用较短的旧等待预算。'''
    longest_skill = max(spec.timeout_s for spec in list_skill_specs())
    assert _DEFAULT_TIMEOUT_S >= longest_skill + 30.0
    assert _BEHAVIOR_TIMEOUT_S > _MAX_ACTIONS * 2 * _DEFAULT_TIMEOUT_S


class RecordingObservationManager(ObservationManagerNode):
    def __init__(self, source: Px4WorldStateAdapter) -> None:
        self.calls = 0
        super().__init__(source)

    def get_observation(
        self, request: Observation.Request, response: Observation.Response
    ) -> Observation.Response:
        self.calls += 1
        return super().get_observation(request, response)


class RecordingLLM(LLMNode):
    def __init__(self) -> None:
        self.prompts: list[str] = []
        super().__init__()

    def get_response(self, request: LLM.Request, response: LLM.Response) -> LLM.Response:
        self.prompts.append(request.prompt)
        return super().get_response(request, response)


def test_main_observes_before_human_input_then_reobserves_after_llm_failure(
    tmp_path: Path,
) -> None:
    rclpy.init()
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(
        aggregator=aggregator, trace_path=tmp_path / 'world_state.jsonl'
    )
    aggregator.update_local_position(
        SimpleNamespace(
            timestamp=21, x=1.0, y=2.0, z=-3.0,
            vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
        ),
        datetime.now(UTC),
    )
    source._record_snapshot()
    observation = RecordingObservationManager(source)
    llm = RecordingLLM()
    executor = MultiThreadedExecutor(num_threads=3)
    for node in (source, observation, llm):
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    inputs = 0

    def read_input(_prompt: str) -> str:
        nonlocal inputs
        inputs += 1
        assert observation.calls == inputs
        if inputs == 1:
            return '检查当前位置'
        raise EOFError

    try:
        trace_path = tmp_path / 'agent.jsonl'
        main(input_reader=read_input, trace_path=trace_path)
        assert observation.calls == 2
        assert len(llm.prompts) == 1
        assert 'HumanInput: 检查当前位置' in llm.prompts[0]
        assert 'state_id=state-21-1' in llm.prompts[0]
        assert 'takeoff: 起飞到指定的正数目标高度。' in llm.prompts[0]
        assert 'input=MX_UNIMPLEMENTED' in llm.prompts[0]
        replay = TraceReplay(trace_path)
        observations = tuple(replay.iter_observation_records())
        assert len(observations) == 2
        assert all(record.source == 'px4_world_state_adapter:mx_placeholder' for record in observations)
        assert observations[0].observation == 'MX_UNIMPLEMENTED_TEXT_SUMMARY state_id=state-21-1'
        assert observations[0].message == 'WorldState acquired; text summary is not implemented'
        reply, = replay.iter_llm_response_records()
        assert reply.prompt == llm.prompts[0]
        assert (reply.success, reply.response, reply.info) == (False, '', 'LLM_UNAVAILABLE_MX2')
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in (llm, observation, source):
            node.destroy_node()
        rclpy.shutdown()


def test_main_executes_two_actions_then_observes_success_and_failure(tmp_path: Path, monkeypatch) -> None:
    rclpy.init()
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(aggregator=aggregator, trace_path=tmp_path / 'world_state.jsonl')
    aggregator.update_local_position(
        SimpleNamespace(
            timestamp=22, x=1.0, y=2.0, z=-3.0,
            vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
        ),
        datetime.now(UTC),
    )
    source._record_snapshot()
    observation = RecordingObservationManager(source)
    llm = RecordingLLM()
    llm.set_parameters([Parameter('mx_fixed_behavior', value=True)])
    sequence = SequenceExecutor(action_timeout_s=0.5)
    actions = rclpy.create_node('mx3_entry_test_actions')
    calls: list[tuple[str, str, str]] = []
    fail_hold = False
    steps: list[tuple[float, bool, dict[str, object]]] = []
    original_step = RosEnv.step

    def record_step(self: RosEnv, behavior: str):
        result = original_step(self, behavior)
        steps.append(result[1:])
        return result

    monkeypatch.setattr(RosEnv, 'step', record_step)

    def hold(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''验证实际收到的 hold JSON 参数，并返回脚本化动作结果。'''
        assert HoldArgs.model_validate_json(request.input).duration_s == 1.0
        calls.append(('hold', request.input, request.prev_output))
        response.success = not fail_hold
        response.output = 'hold-output' if response.success else 'hold-failed'
        return response

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''验证实际收到的 rtl 空参数对象，再返回动作替身结果。'''
        RTLArgs.model_validate_json(request.input)
        calls.append(('rtl', request.input, request.prev_output))
        response.success = True
        response.output = 'rtl-output'
        return response

    actions.create_service(AtomicAction, 'hold', hold)
    actions.create_service(AtomicAction, 'rtl', rtl)
    executor = MultiThreadedExecutor(num_threads=4)
    for node in (source, observation, llm, sequence, actions):
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    inputs = 0

    def read_input(_prompt: str) -> str:
        nonlocal inputs, fail_hold
        inputs += 1
        assert observation.calls == inputs
        if inputs == 1:
            return '执行固定两步'
        if inputs == 2:
            fail_hold = True
            return '再次执行固定两步'
        raise EOFError

    try:
        trace_path = tmp_path / 'agent.jsonl'
        main(input_reader=read_input, trace_path=trace_path)
        assert observation.calls == 3
        assert len(llm.prompts) == 2
        assert [(name, json.loads(payload), previous) for name, payload, previous in calls] == [
            ('hold', {'duration_s': 1.0}, ''),
            ('rtl', {}, 'hold-output'),
            ('hold', {'duration_s': 1.0}, ''),
        ]
        assert steps == [(-0.5, False, {}), (-1.0, False, {})]
        replay = TraceReplay(trace_path)
        assert len(tuple(replay.iter_observation_records())) == 3
        replies = tuple(replay.iter_llm_response_records())
        assert [record.prompt for record in replies] == llm.prompts
        assert all((record.success, record.response, record.info) == (
            True, _FIXED_BEHAVIOR, 'MX_FIXED_BEHAVIOR: hold,rtl',
        ) for record in replies)
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in (actions, sequence, llm, observation, source):
            node.destroy_node()
        rclpy.shutdown()


def test_agent_rejects_behavior_outside_a_single_json_block() -> None:
    valid = LLM.Response(success=True, response=(
        '```json\n{"name":"hold","input":"wait"}\n'
        '{"name":"rtl","input":"home"}\n```'
    ))
    assert RosAgent.extract_behavior(valid).count('\n') == 1
    for text in ('{"name":"hold","input":"wait"}', '```json\n```', '```json\nx\n```\nextra'):
        try:
            RosAgent.extract_behavior(LLM.Response(success=True, response=text))
        except ValueError as error:
            assert 'INVALID_BEHAVIOR_RESPONSE' in str(error)
        else:
            raise AssertionError(f'accepted invalid response: {text!r}')


@pytest.mark.parametrize('scenario', ['missing_observation', 'observation_write', 'llm_write'])
def test_main_records_observation_failure_and_stops_on_trace_write_failure(
    tmp_path: Path, monkeypatch, scenario: str,
) -> None:
    trace_path = tmp_path / 'configured_agent.jsonl'
    rclpy.init(args=['--ros-args', '-p', f'agent_trace_path:={trace_path}'])
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(aggregator=aggregator, trace_path=tmp_path / 'state.jsonl')
    if scenario != 'missing_observation':
        aggregator.update_local_position(SimpleNamespace(
            timestamp=23, x=1.0, y=2.0, z=-3.0,
            vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
        ), datetime.now(UTC))
        source._record_snapshot()
    observation = RecordingObservationManager(source)
    llm = RecordingLLM()
    llm.set_parameters([Parameter('mx_fixed_behavior', value=True)])
    behavior = rclpy.create_node('mx5_behavior_dispatch_probe')
    dispatched = []

    def execute(request, response):
        dispatched.append(request.behavior)
        response.success = True
        return response

    behavior.create_service(ExecuteBehavior, 'execute_behavior', execute)
    executor = MultiThreadedExecutor(num_threads=4)
    nodes = (source, observation, llm, behavior)
    for node in nodes:
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    inputs = iter(['执行固定两步'])

    def read_input(_prompt):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None

    def fail_write(*_args, **_kwargs):
        raise PermissionError('injected trace write denial')

    try:
        if scenario == 'missing_observation':
            main(input_reader=read_input)
            records = tuple(TraceReplay(trace_path).iter_observation_records())
            assert len(records) == 2
            assert all(not record.success and record.observation == '' for record in records)
            assert all(record.source == 'px4_world_state_adapter' and record.message for record in records)
            assert not llm.prompts
            assert not tuple(TraceReplay(trace_path).iter_llm_response_records())
        else:
            method = 'record_observation' if scenario == 'observation_write' else 'record_llm_response'
            monkeypatch.setattr(TraceRecorder, method, fail_write)
            expected = 'OBSERVATION' if scenario == 'observation_write' else 'LLM'
            with pytest.raises(OSError, match=f'{expected}_TRACE_WRITE_FAILED'):
                main(input_reader=read_input)
            assert len(llm.prompts) == (1 if scenario == 'llm_write' else 0)
        assert dispatched == []
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in reversed(nodes):
            node.destroy_node()
        rclpy.shutdown()
