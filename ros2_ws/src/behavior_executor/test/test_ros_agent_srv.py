"""Exercise the real human entrypoint through ROS observation and LLM services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
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


def test_temp_timeout_budgets_cover_skills_and_full_sequence() -> None:
    '''防止 Skill 或执行器预算增长后，上层仍用较短的旧等待预算。'''
    expected_output_skill_timeout_s = max(spec.timeout_s for spec in list_skill_specs())
    assert _LLM_RESPONSE_TIMEOUT_S > llm_node._HTTP_TIMEOUT_S
    assert _DEFAULT_TIMEOUT_S >= expected_output_skill_timeout_s + 30.0
    assert _BEHAVIOR_TIMEOUT_S > _MAX_ACTIONS * 2 * _DEFAULT_TIMEOUT_S


class RecordingObservationManager(ObservationManagerNode):
    def __init__(self, source: Px4WorldStateAdapter) -> None:
        self.test_temp_call_cnt = 0
        super().__init__(source)

    def get_observation(
        self, request: Observation.Request, response: Observation.Response
    ) -> Observation.Response:
        self.test_temp_call_cnt += 1
        return super().get_observation(request, response)


class RecordingLLM(LLMNode):
    def __init__(self) -> None:
        '''创建真实模型服务，并保存测试用 prompt 记录集合。'''
        self.test_temp_prompts: list[str] = []
        super().__init__()

    def get_response(self, request: LLM.Request, response: LLM.Response) -> LLM.Response:
        '''记录请求后调用真实模型服务实现。'''
        self.test_temp_prompts.append(request.prompt)
        return super().get_response(request, response)


@pytest.mark.parametrize('test_input_failure', ['config', 'timeout'])
def test_temp_main_observes_before_human_input_then_reobserves_after_llm_failure(
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
    test_temp_thread = Thread(target=executor.spin, daemon=True)
    test_temp_thread.start()
    test_temp_input_cnt = 0

    def test_temp_read_input(_prompt: str) -> str:
        '''提供一次人工输入，再以 EOF 结束主循环。'''
        nonlocal test_temp_input_cnt
        test_temp_input_cnt += 1
        assert observation.test_temp_call_cnt == test_temp_input_cnt
        if test_temp_input_cnt == 1:
            return '检查当前位置'
        raise EOFError

    try:
        trace_path = tmp_path / 'agent.jsonl'
        main(input_reader=test_temp_read_input, trace_path=trace_path)
        assert observation.test_temp_call_cnt == 2
        assert len(llm.test_temp_prompts) == 1
        assert 'HumanInput: 检查当前位置' in llm.test_temp_prompts[0]
        test_temp_format = llm.test_temp_prompts[0].split('AtomicActionLibrary:', 1)[0]
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
        assert 'state_id=state-21-1' in llm.test_temp_prompts[0]
        assert 'takeoff: 起飞到指定的正数目标高度。' in llm.test_temp_prompts[0]
        assert 'MX_UNIMPLEMENTED' not in llm.test_temp_prompts[0]
        action_library = llm.test_temp_prompts[0].split('AtomicActionLibrary:\n', 1)[1].split(
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
        assert observations[0].observation in llm.test_temp_prompts[0]
        assert observations[0].message == 'WorldState snapshot acquired'
        reply, = replay.iter_llm_response_records()
        assert reply.prompt == llm.test_temp_prompts[0]
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
        test_temp_thread.join(timeout=5)
        for node in (llm, observation, source):
            node.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('test_input_model', ['fixed', 'api'])
def test_temp_main_executes_two_actions_then_observes_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_input_model: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    '''验证两轮人工触发、等待期间无决策，以及新观察进入下一轮 prompt。'''
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
    test_temp_actions = rclpy.create_node('mx3_entry_test_actions')
    test_temp_calls: list[tuple[str, str, str]] = []
    test_temp_fail_hold = False
    test_temp_steps: list[tuple[float, bool, dict[str, object]]] = []
    test_temp_original_step = RosEnv.step
    test_temp_waiting = Event()
    test_temp_resume = Event()
    test_temp_wait_samples: list[tuple[int, int, int]] = []
    test_temp_input_boundaries: list[tuple[int, int, int]] = []

    def test_temp_record_step(
        self: RosEnv, behavior: str,
    ) -> tuple[Observation.Response, float, bool, dict[str, object]]:
        '''调用真实环境步，仅记录返回值。'''
        result = test_temp_original_step(self, behavior)
        test_temp_steps.append(result[1:])
        return result

    monkeypatch.setattr(RosEnv, 'step', test_temp_record_step)

    def hold(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''验证实际收到的 hold JSON 参数，并返回脚本化动作结果。'''
        assert HoldArgs.model_validate_json(request.input).duration_s == 1.0
        test_temp_calls.append(('hold', request.input, request.prev_output))
        response.success = not test_temp_fail_hold
        response.output = 'hold-output' if response.success else 'hold-failed'
        return response

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''验证实际收到的 rtl 空参数对象，再返回动作替身结果。'''
        RTLArgs.model_validate_json(request.input)
        test_temp_calls.append(('rtl', request.input, request.prev_output))
        aggregator.update_local_position(SimpleNamespace(
            timestamp=24, x=4.0, y=5.0, z=-6.0,
            vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
        ), datetime.now(UTC))
        source._record_snapshot()
        response.success = True
        response.output = 'rtl-output'
        return response

    test_temp_actions.create_service(AtomicAction, 'hold', hold)
    test_temp_actions.create_service(AtomicAction, 'rtl', rtl)

    def test_temp_sample_wait() -> None:
        '''人工输入阻塞期间，让 ROS 定时回调记录真实服务与动作调用次数。'''
        if test_temp_waiting.is_set() and not test_temp_resume.is_set():
            test_temp_wait_samples.append((
                len(llm.test_temp_prompts), observation.test_temp_call_cnt, len(test_temp_calls),
            ))
            if len(test_temp_wait_samples) == 3:
                test_temp_resume.set()

    test_temp_actions.create_timer(0.05, test_temp_sample_wait)
    executor = MultiThreadedExecutor(num_threads=4)
    for node in (source, observation, llm, sequence, test_temp_actions):
        executor.add_node(node)
    test_temp_thread = Thread(target=executor.spin, daemon=True)
    test_temp_thread.start()
    test_temp_input_cnt = 0

    def test_temp_read_input(_prompt: str) -> str:
        '''逐轮释放人工输入，并在第二轮输入前阻塞等待 ROS 回调采样。'''
        nonlocal test_temp_input_cnt, test_temp_fail_hold
        test_temp_input_cnt += 1
        assert observation.test_temp_call_cnt == test_temp_input_cnt
        assert len(llm.test_temp_prompts) == test_temp_input_cnt - 1
        test_temp_input_boundaries.append((
            len(llm.test_temp_prompts), observation.test_temp_call_cnt, len(test_temp_calls),
        ))
        if test_temp_input_cnt == 1:
            return '执行固定两步'
        if test_temp_input_cnt == 2:
            test_temp_waiting.set()
            assert test_temp_resume.wait(timeout=5.0), 'ROS wait probes did not complete'
            assert test_temp_wait_samples == [(1, 2, 2)] * 3
            assert len(llm.test_temp_prompts) == 1 and len(test_temp_calls) == 2
            test_temp_fail_hold = True
            return '再次执行固定两步'
        raise EOFError

    try:
        trace_path = tmp_path / 'agent.jsonl'
        main(input_reader=test_temp_read_input, trace_path=trace_path)
        assert observation.test_temp_call_cnt == 3
        assert len(llm.test_temp_prompts) == 2
        assert test_temp_input_boundaries == [(0, 1, 0), (1, 2, 2), (2, 3, 3)]
        assert [
            (name, json.loads(payload), previous)
            for name, payload, previous in test_temp_calls
        ] == [
            ('hold', {'duration_s': 1.0}, ''),
            ('rtl', {}, 'hold-output'),
            ('hold', {'duration_s': 1.0}, ''),
        ]
        assert test_temp_steps == [(-0.5, False, {}), (-1.0, False, {})]
        replay = TraceReplay(trace_path)
        observations = tuple(replay.iter_observation_records())
        assert len(observations) == 3
        expected_output_positions = [
            {'x': 1.0, 'y': 2.0, 'z': -3.0},
            {'x': 4.0, 'y': 5.0, 'z': -6.0},
        ]
        for test_temp_index, test_input_human in enumerate(('执行固定两步', '再次执行固定两步')):
            assert f'HumanInput: {test_input_human}' in llm.test_temp_prompts[test_temp_index]
            assert observations[test_temp_index].observation in llm.test_temp_prompts[test_temp_index]
            state = json.loads(observations[test_temp_index].observation.splitlines()[-1])
            assert state['position_ned_m'] == expected_output_positions[test_temp_index]
        assert observations[0].observation not in llm.test_temp_prompts[1]
        assert [json.loads(line)['type'] for line in trace_path.read_text().splitlines()] == [
            'observation', 'llm_response', 'observation', 'llm_response', 'observation',
        ]
        assert test_temp_opener.open.call_count == (2 if test_input_model == 'api' else 0)
        if test_input_model == 'api':
            for reply in replay.iter_llm_response_records():
                assert json.loads(reply.info)['usage']['total_tokens'] == 15
                assert 'test-only-secret' not in reply.info
        replies = tuple(replay.iter_llm_response_records())
        assert [record.prompt for record in replies] == llm.test_temp_prompts
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
        test_temp_thread.join(timeout=5)
        for node in (test_temp_actions, sequence, llm, observation, source):
            node.destroy_node()
        rclpy.shutdown()


def test_temp_agent_rejects_behavior_outside_a_single_json_block() -> None:
    test_input_response = LLM.Response(success=True, response=(
        '```json\n{"name":"hold","input":"wait"}\n'
        '{"name":"rtl","input":"home"}\n```'
    ))
    assert RosAgent.extract_behavior(test_input_response).count('\n') == 1
    for test_input_text in (
        '{"name":"hold","input":"wait"}', '```json\n```', '```json\nx\n```\nextra',
    ):
        try:
            RosAgent.extract_behavior(LLM.Response(success=True, response=test_input_text))
        except ValueError as error:
            assert 'INVALID_BEHAVIOR_RESPONSE' in str(error)
        else:
            raise AssertionError(f'accepted invalid response: {test_input_text!r}')


@pytest.mark.parametrize(
    'test_input_scenario', ['missing_observation', 'observation_write', 'llm_write'],
)
def test_temp_main_records_observation_failure_and_stops_on_trace_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_input_scenario: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    '''核对观察失败的终端说明与零派发，Trace 写入失败仍向上传播。'''
    trace_path = tmp_path / 'configured_agent.jsonl'
    rclpy.init(args=['--ros-args', '-p', f'agent_trace_path:={trace_path}'])
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(aggregator=aggregator, trace_path=tmp_path / 'state.jsonl')
    if test_input_scenario != 'missing_observation':
        aggregator.update_local_position(SimpleNamespace(
            timestamp=23, x=1.0, y=2.0, z=-3.0,
            vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
        ), datetime.now(UTC))
        source._record_snapshot()
    observation = RecordingObservationManager(source)
    llm = RecordingLLM()
    llm.set_parameters([Parameter('mx_fixed_behavior', value=True)])
    test_temp_behavior = rclpy.create_node('mx5_behavior_dispatch_probe')
    test_temp_dispatched = []

    def test_temp_execute_behavior(
        request: ExecuteBehavior.Request, response: ExecuteBehavior.Response,
    ) -> ExecuteBehavior.Response:
        '''记录行为服务请求，并返回成功替身响应。'''
        test_temp_dispatched.append(request.behavior)
        response.success = True
        return response

    test_temp_behavior.create_service(ExecuteBehavior, 'execute_behavior', test_temp_execute_behavior)
    executor = MultiThreadedExecutor(num_threads=4)
    nodes = (source, observation, llm, test_temp_behavior)
    for node in nodes:
        executor.add_node(node)
    test_temp_thread = Thread(target=executor.spin, daemon=True)
    test_temp_thread.start()
    test_input_humans = iter(['执行固定两步'])

    def test_temp_read_input(_prompt: str) -> str:
        '''逐项提供预设人工输入，耗尽后以 EOF 结束主循环。'''
        try:
            return next(test_input_humans)
        except StopIteration:
            raise EOFError from None

    def test_temp_fail_write(*_args: object, **_kwargs: object) -> None:
        '''注入 Trace 写入失败。'''
        raise PermissionError('injected trace write denial')

    try:
        if test_input_scenario == 'missing_observation':
            main(input_reader=test_temp_read_input)
            records = tuple(TraceReplay(trace_path).iter_observation_records())
            assert len(records) == 2
            assert all(not record.success and record.observation == '' for record in records)
            assert all(record.source == 'px4_world_state_adapter' and record.message for record in records)
            assert not llm.test_temp_prompts
            assert not tuple(TraceReplay(trace_path).iter_llm_response_records())
            test_temp_output = capfd.readouterr()
            test_temp_terminal = test_temp_output.out + test_temp_output.err
            assert '观察获取失败（来源：px4_world_state_adapter）' in test_temp_terminal
            assert all(record.message in test_temp_terminal for record in records)
            assert '动作序列执行成功' not in test_temp_terminal
        else:
            test_temp_method = (
                'record_observation' if test_input_scenario == 'observation_write'
                else 'record_llm_response'
            )
            monkeypatch.setattr(TraceRecorder, test_temp_method, test_temp_fail_write)
            expected_output_trace = (
                'OBSERVATION' if test_input_scenario == 'observation_write' else 'LLM'
            )
            with pytest.raises(OSError, match=f'{expected_output_trace}_TRACE_WRITE_FAILED'):
                main(input_reader=test_temp_read_input)
            assert len(llm.test_temp_prompts) == (1 if test_input_scenario == 'llm_write' else 0)
        assert test_temp_dispatched == []
    finally:
        executor.shutdown()
        test_temp_thread.join(timeout=5)
        for node in reversed(nodes):
            node.destroy_node()
        rclpy.shutdown()
