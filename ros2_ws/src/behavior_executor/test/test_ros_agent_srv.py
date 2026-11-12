"""Exercise the real human entrypoint through ROS observation and LLM services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import rclpy
from agent_comm import llm_node, ros_agent_srv
from agent_comm.llm_node import _FIXED_BEHAVIOR, LLMNode
from agent_comm.ros_agent_srv import (
    _BEHAVIOR_TIMEOUT_S,
    _LLM_RESPONSE_TIMEOUT_S,
    RosAgent,
    RosEnv,
    main,
)
from behavior_executor.sequence_executor import _DEFAULT_TIMEOUT_S, _MAX_ACTIONS, SequenceExecutor
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from observation_manager.observation_manager_node import ObservationManagerNode
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from rosllm_srvs.srv import LLM, AtomicAction, ExecuteBehavior, Observation

from flight_agent.components.skills.registry import list_skill_specs
from flight_agent.components.tracing import TraceRecorder, TraceReplay
from flight_agent.components.vehicle.state import WorldStateAggregator
from flight_agent.contracts import HoldArgs, RTLArgs, SkillName, parse_skill_arguments


def test_timeout_budgets_cover_skills_and_full_sequence() -> None:
    '''防止 Skill 或执行器预算增长后，上层仍用较短的旧等待预算。'''
    longest_skill = max(spec.timeout_s for spec in list_skill_specs())
    assert _LLM_RESPONSE_TIMEOUT_S > llm_node._HTTP_TIMEOUT_S
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


@pytest.mark.parametrize('test_input_failure', ['config', 'timeout'])
def test_main_observes_before_human_input_then_reobserves_after_llm_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_input_failure: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    '''验证配置/网络失败零派发、重新观察，并显示失败原因和实际观察。'''
    monkeypatch.delenv('FLIGHT_LLM_API_KEY', raising=False)
    monkeypatch.delenv('FLIGHT_LLM_BASE_URL', raising=False)
    monkeypatch.delenv('FLIGHT_LLM_MODEL', raising=False)
    test_temp_opener = Mock()
    test_temp_opener.open.side_effect = TimeoutError('test-only-secret')
    monkeypatch.setattr(llm_node.urllib.request, 'build_opener', lambda *_: test_temp_opener)
    if test_input_failure == 'timeout':
        monkeypatch.setenv('FLIGHT_LLM_API_KEY', 'test-only-secret')
    test_temp_call = Mock(wraps=ros_agent_srv._call_service)
    monkeypatch.setattr(ros_agent_srv, '_call_service', test_temp_call)
    test_temp_step = Mock(side_effect=AssertionError('unexpected behavior dispatch'))
    monkeypatch.setattr(RosEnv, 'step', test_temp_step)

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
        test_temp_format = llm.prompts[0].split('AtomicActionLibrary:', 1)[0]
        assert test_temp_format.startswith('ResponseFormat:')
        assert 'only one ```json code block containing JSON Lines' in test_temp_format
        assert f'1 to {_MAX_ACTIONS} actions, one JSON object per line' in test_temp_format
        assert 'exactly "name" and "input"' in test_temp_format
        assert 'action name from AtomicActionLibrary' in test_temp_format
        assert 'string encoding one JSON object' in test_temp_format
        assert 'that action input_schema' in test_temp_format
        assert 'for parameterless actions use the string "{}"' in test_temp_format
        assert 'Do not return a JSON array, extra fields, executable code' in test_temp_format
        assert 'multiple code blocks, or any text outside the code block' in test_temp_format
        assert 'state_id=state-21-1' in llm.prompts[0]
        assert 'takeoff: 起飞到指定的正数目标高度。' in llm.prompts[0]
        assert 'MX_UNIMPLEMENTED' not in llm.prompts[0]
        action_library = llm.prompts[0].split('AtomicActionLibrary:\n', 1)[1].split(
            '\nObservation source:', 1,
        )[0]
        assert 'type=ROS2 service' in action_library
        assert 'JSON object encoded as a string' in action_library
        assert 'altitude is positive upward' in action_library
        assert 'INCONCLUSIVE is not verified task success' in action_library
        for expected_output_field in (
            'source', 'proposal_id', 'decision_id', 'execution_id', 'reason_codes',
            'execution_status', 'failure_code', 'error_code', 'verification',
        ):
            assert expected_output_field in action_library
        test_temp_schemas = {
            line.split(':', 1)[0]: json.loads(line.split('input_schema=', 1)[1])
            for line in action_library.splitlines() if 'input_schema=' in line
        }
        test_input_arguments = {
            'takeoff': {'target_altitude_m': 3.0},
            'goto': {
                'north_m': 1.0, 'east_m': 2.0, 'altitude_m': 3.0,
                'acceptance_radius_m': 1.0,
            },
            'hold': {'duration_s': 1.0}, 'rtl': {}, 'land': {},
        }
        assert set(test_temp_schemas) == set(test_input_arguments)
        for spec in list_skill_specs():
            input_schema = test_temp_schemas[spec.name.value]
            assert input_schema['additionalProperties'] is False
            assert set(input_schema.get('required', [])) == set(
                test_input_arguments[spec.name.value]
            )
            if spec.name is SkillName.HOLD:
                assert input_schema['properties']['duration_s']['type'] == 'number'
                assert input_schema['properties']['duration_s']['exclusiveMinimum'] == 0
                assert HoldArgs().duration_s is None
                assert 'V1 has no cancellation protocol' in action_library
            assert input_schema == spec.args_schema
            arguments = parse_skill_arguments(spec.name, test_input_arguments[spec.name.value])
            assert arguments.model_dump() == test_input_arguments[spec.name.value]
        replay = TraceReplay(trace_path)
        observations = tuple(replay.iter_observation_records())
        assert len(observations) == 2
        assert all(record.source == 'px4_world_state_adapter' for record in observations)
        assert 'state_id=state-21-1' in observations[0].observation
        assert 'frame=NED' in observations[0].observation
        assert 'null=unknown' in observations[0].observation
        assert observations[0].observation in llm.prompts[0]
        assert observations[0].message == 'WorldState snapshot acquired'
        reply, = replay.iter_llm_response_records()
        assert reply.prompt == llm.prompts[0]
        assert reply.success is False and reply.response == ''
        assert json.loads(reply.info)['error_code'] == (
            'LLM_CONFIG_ERROR' if test_input_failure == 'config' else 'LLM_TIMEOUT'
        )
        assert 'test-only-secret' not in reply.info
        assert test_temp_opener.open.call_count == (0 if test_input_failure == 'config' else 1)
        test_temp_step.assert_not_called()
        test_temp_llm_calls = [
            call for call in test_temp_call.call_args_list if isinstance(call.args[2], LLM.Request)
        ]
        assert len(test_temp_llm_calls) == 1
        assert test_temp_llm_calls[0].kwargs == {
            'timeout_s': 35.0, 'discovery_timeout_s': 5.0,
        }
        test_temp_output = capfd.readouterr()
        test_temp_terminal = test_temp_output.out + test_temp_output.err
        assert '本轮决策失败，未派发行为' in test_temp_terminal
        assert json.loads(reply.info)['error_code'] in test_temp_terminal
        assert all(record.observation in test_temp_terminal for record in observations)
        assert '动作序列执行成功' not in test_temp_terminal
        assert 'test-only-secret' not in test_temp_terminal
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in (llm, observation, source):
            node.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('test_input_model', ['fixed', 'api'])
def test_main_executes_two_actions_then_observes_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_input_model: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    '''验证固定模式及 HTTP 替身的序列执行、再观察和操作者输出。'''
    test_temp_response = MagicMock()
    test_temp_response.__enter__.return_value = test_temp_response
    test_temp_response.status = 200
    test_temp_response.read.return_value = json.dumps({
        'id': 'test-response', 'model': 'deepseek-flash',
        'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': _FIXED_BEHAVIOR,
        }}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                  'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 10},
    }).encode()
    test_temp_opener = Mock()
    test_temp_opener.open.return_value = test_temp_response
    monkeypatch.setattr(llm_node.urllib.request, 'build_opener', lambda *_: test_temp_opener)
    monkeypatch.setenv('FLIGHT_LLM_API_KEY', 'test-only-secret')
    monkeypatch.setenv('FLIGHT_LLM_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('FLIGHT_LLM_MODEL', 'deepseek-flash')
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
    llm.set_parameters([Parameter('mx_fixed_behavior', value=test_input_model == 'fixed')])
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
        assert test_temp_opener.open.call_count == (2 if test_input_model == 'api' else 0)
        if test_input_model == 'api':
            for reply in replay.iter_llm_response_records():
                assert json.loads(reply.info)['usage']['total_tokens'] == 15
                assert 'test-only-secret' not in reply.info
        replies = tuple(replay.iter_llm_response_records())
        assert [record.prompt for record in replies] == llm.prompts
        assert all(record.success and record.response == _FIXED_BEHAVIOR for record in replies)
        if test_input_model == 'fixed':
            assert all(record.info == 'MX_FIXED_BEHAVIOR: hold,rtl' for record in replies)
        else:
            assert all(json.loads(record.info)['source'] == 'deepseek_api' for record in replies)
        test_temp_output = capfd.readouterr()
        test_temp_terminal = test_temp_output.out + test_temp_output.err
        assert '动作序列执行成功' in test_temp_terminal
        assert '动作序列执行失败或被拒绝' in test_temp_terminal
        assert 'ATOMIC_ACTION_FAILED name=hold: hold-failed' in test_temp_terminal
        assert test_temp_terminal.count('任务验证：INCONCLUSIVE') == 2
        assert all(
            record.observation in test_temp_terminal for record in replay.iter_observation_records()
        )
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    '''核对观察失败的终端说明与零派发，Trace 写入失败仍向上传播。'''
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
            test_temp_output = capfd.readouterr()
            test_temp_terminal = test_temp_output.out + test_temp_output.err
            assert '观察获取失败（来源：px4_world_state_adapter）' in test_temp_terminal
            assert all(record.message in test_temp_terminal for record in records)
            assert '动作序列执行成功' not in test_temp_terminal
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
