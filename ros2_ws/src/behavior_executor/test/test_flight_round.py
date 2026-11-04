'''从人工入口验证真实动作服务和 Gate；PX4、权限与限速来源使用测试替身。'''

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import rclpy
from agent_comm.llm_node import LLMNode
from agent_comm.ros_agent_srv import RosEnv, main
from behavior_executor.sequence_executor import SequenceExecutor
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from flight_agent_ros.flight_atomic_action_server import (
    FlightAtomicActionServer,
    build_flight_atomic_action_server,
)
from flight_agent_ros.safety_config import load_safety_constraints
from observation_manager.observation_manager_node import ObservationManagerNode
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from rosllm_srvs.srv import LLM, AtomicAction, Observation

from flight_agent.components.tracing import TraceRecorder, TraceReplay
from flight_agent.components.vehicle.flight_execution import MockFlightExecutionBackend
from flight_agent.components.vehicle.state import WorldStateAggregator
from flight_agent.contracts import (
    ApprovedSkillCommand,
    ControlAuthority,
    GlobalPosition,
    SafetyDecisionType,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
)


@pytest.mark.parametrize('outcome', [
    'success', 'execution_failure', 'unknown_authority', 'execution_timeout',
    'execution_error', 'result_trace_failure', 'error_trace_failure', 'decision_trace_failure',
])
def test_human_round_through_flight_service_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    '''验证两步成功、首项失败和权限拒绝后，主入口均重新观察并等待人工输入。'''
    rclpy.init()
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(aggregator=aggregator, trace_path=tmp_path / 'world.jsonl')

    def publish_test_position(timestamp: int) -> None:
        '''在 PX4 消息边界注入测试位置，经实际聚合器和适配器生成观察快照。'''
        aggregator.update_local_position(SimpleNamespace(
            timestamp=timestamp, x=0.0, y=0.0, z=-2.0,
            vx=0.0, vy=0.0, vz=0.0, xy_valid=True, z_valid=True,
        ), datetime.now(UTC))
        source._record_snapshot()

    publish_test_position(100)
    state = source.read_world_state().model_copy(update={
        'armed': True, 'landed': False, 'position_valid': True,
        'battery_percent': 80.0, 'link_healthy': True, 'home_valid': True,
        'home_position_ned_m': Vector3(x=0.0, y=0.0, z=0.0),
        'home_position_wgs84': GlobalPosition(
            latitude_deg=47.0, longitude_deg=8.0, altitude_amsl_m=500.0,
        ),
        'health_flags': {
            'battery_connected': True, 'battery_valid': True, 'battery_fault_free': True,
            'failure_detector_clear': True, 'preflight_checks_pass': True,
        },
    })
    backend = MockFlightExecutionBackend(state)
    trace_path = tmp_path / 'safety.jsonl'
    _commands: list[ApprovedSkillCommand] = []
    _results: list[SkillResult] = []
    original_execute = backend.execute

    async def execute(command: ApprovedSkillCommand) -> SkillResult:
        '''记录实际批准命令，确认决策已落盘，再模拟执行与后续 PX4 状态更新。'''
        trace_record = list(TraceReplay(trace_path).iter_safety_decision_records())[-1]
        assert trace_record.decision.decision is SafetyDecisionType.APPROVE
        assert trace_record.decision.decision_id == command.decision_id
        assert trace_record.proposal.proposal_id == command.proposal_id
        _commands.append(command)
        if outcome in {'execution_error', 'error_trace_failure'}:
            raise OSError('TEST_BACKEND_UNAVAILABLE')
        result = await original_execute(command)
        publish_test_position(100 + len(_commands))
        if outcome in {'execution_failure', 'execution_timeout'}:
            result = result.model_copy(update={
                'status': (
                    SkillExecutionStatus.FAILED if outcome == 'execution_failure'
                    else SkillExecutionStatus.TIMED_OUT
                ),
                'failure_code': 'TEST_EXECUTION_FAILURE',
            })
        _results.append(result)
        return result

    monkeypatch.setattr(backend, 'execute', execute)
    failed_method = {
        'decision_trace_failure': 'record_safety_decision',
        'result_trace_failure': 'record_skill_result',
        'error_trace_failure': 'record_skill_execution_error',
    }.get(outcome)
    if failed_method is not None:
        def fail_write(*_args, **_kwargs):
            raise PermissionError('TEST_TRACE_DENIED')
        monkeypatch.setattr(TraceRecorder, failed_method, fail_write)
    speed_source = SimpleNamespace(read_validated_speed_limit=AsyncMock(return_value=8.0))
    authority = (
        ControlAuthority.UNKNOWN if outcome == 'unknown_authority'
        else ControlAuthority.AGENT_ALLOWED
    )
    _action_calls: list[tuple[SkillName, str, str, bool, str]] = []
    original_handle_action = FlightAtomicActionServer._handle_action

    def handle_action(
        server: FlightAtomicActionServer, skill_name: SkillName,
        request: AtomicAction.Request, response: AtomicAction.Response,
    ) -> AtomicAction.Response:
        '''记录实际动作请求和响应，仍由原服务执行全部解析、Gate 与结果封装。'''
        result = original_handle_action(server, skill_name, request, response)
        _action_calls.append((
            skill_name, request.input, request.prev_output, result.success, result.output,
        ))
        return result

    monkeypatch.setattr(FlightAtomicActionServer, '_handle_action', handle_action)
    example = Path(__file__).resolve().parents[4] / 'configs/flight_safety.example.json'
    server = build_flight_atomic_action_server(
        load_safety_constraints(example), backend, trace_path=trace_path,
        authority_reader=lambda: authority, speed_limit_source=speed_source,
    )
    observation = ObservationManagerNode(source)
    _llm_calls: list[tuple[str, bool, str, str]] = []
    original_llm_response = LLMNode.get_response

    def llm_response(node: LLMNode, request: LLM.Request, response: LLM.Response) -> LLM.Response:
        result = original_llm_response(node, request, response)
        _llm_calls.append((request.prompt, result.success, result.response, result.info))
        return result

    monkeypatch.setattr(LLMNode, 'get_response', llm_response)
    llm = LLMNode()
    llm.set_parameters([Parameter('mx_fixed_behavior', value=True)])
    sequence = SequenceExecutor()
    _observations: list[Observation.Response] = []
    _rewards: list[float] = []
    original_get_obs, original_step = RosEnv.get_obs, RosEnv.step

    def get_obs(env: RosEnv) -> Observation.Response:
        '''保留真实 ROS 观察调用，只记录入口收到的响应。'''
        result = original_get_obs(env)
        _observations.append(result)
        return result

    def step(env: RosEnv, behavior: str) -> tuple[Observation.Response, float, bool, dict[str, object]]:
        '''保留真实序列服务调用，只记录传回主循环的结果。'''
        obs, reward, done, info = original_step(env, behavior)
        _rewards.append(reward)
        return obs, reward, done, info

    monkeypatch.setattr(RosEnv, 'get_obs', get_obs)
    monkeypatch.setattr(RosEnv, 'step', step)
    executor = MultiThreadedExecutor(num_threads=4)
    nodes = (source, observation, llm, sequence, server)
    for node in nodes:
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    _inputs = 0

    def read_input(_prompt: str) -> str:
        '''只输入一轮；下一次提示输入前必须已收到执行结果和第二次观察。'''
        nonlocal _inputs
        _inputs += 1
        assert len(_observations) == _inputs
        if _inputs == 1:
            return '执行固定两步'
        assert len(_rewards) == 1
        raise EOFError

    try:
        agent_trace_path = tmp_path / 'agent.jsonl'
        main(input_reader=read_input, trace_path=agent_trace_path)
        assert _inputs == 2
        assert all(obs.success for obs in _observations)
        assert _rewards == ([-0.5] if outcome == 'success' else [-1.0])
        expected_skills = (
            [SkillName.HOLD, SkillName.RTL] if outcome == 'success' else
            [] if outcome in {'unknown_authority', 'decision_trace_failure'} else [SkillName.HOLD]
        )
        assert [command.skill_name for command in _commands] == expected_skills
        agent_replay = TraceReplay(agent_trace_path)
        trace_observation_records = tuple(agent_replay.iter_observation_records())
        assert [
            (trace_record.success, trace_record.observation, trace_record.source, trace_record.message)
            for trace_record in trace_observation_records
        ] == [
            (obs.success, obs.observation, obs.source, obs.message) for obs in _observations
        ]
        assert all(
            trace_record.source == 'px4_world_state_adapter:mx_placeholder'
            for trace_record in trace_observation_records
        )
        trace_llm_record, = agent_replay.iter_llm_response_records()
        assert [(
            trace_llm_record.prompt, trace_llm_record.success,
            trace_llm_record.response, trace_llm_record.info,
        )] == _llm_calls
        assert trace_llm_record.info == 'MX_FIXED_BEHAVIOR: hold,rtl'
        assert f'Observation: {trace_observation_records[0].observation}\n' in trace_llm_record.prompt
        assert 'HumanInput: 执行固定两步' in trace_llm_record.prompt
        assert 'input=MX_UNIMPLEMENTED output=MX_UNIMPLEMENTED' in trace_llm_record.prompt
        assert [json.loads(line)['type'] for line in agent_trace_path.read_text().splitlines()] == [
            'observation', 'llm_response', 'observation',
        ]
        if outcome == 'decision_trace_failure':
            assert not trace_path.exists()
            assert len(_action_calls) == 1
            assert json.loads(_action_calls[0][-1])['error_code'] == 'SERVICE_ERROR'
            return  # 决策未落盘，执行 ID 和 Backend 调用均不得出现。
        trace_decision_records = list(TraceReplay(trace_path).iter_safety_decision_records())
        assert [trace_record.proposal.skill_name for trace_record in trace_decision_records] == (
            ['hold', 'rtl'] if outcome == 'success' else ['hold']
        )
        assert len(_action_calls) == len(trace_decision_records)
        previous_output = ''
        for (skill, payload, previous, success, output), trace_record in zip(
            _action_calls, trace_decision_records, strict=True,
        ):
            assert skill.value == trace_record.proposal.skill_name
            assert json.loads(payload) == ({'duration_s': 1.0} if skill is SkillName.HOLD else {})
            assert previous == previous_output
            previous_output = output
            _action_output = json.loads(output)
            assert _action_output['proposal_id'] == trace_record.proposal.proposal_id
            assert _action_output['verification'] == 'INCONCLUSIVE'
            execution_error = outcome in {
                'execution_error', 'result_trace_failure', 'error_trace_failure',
            }
            assert _action_output['decision_id'] == (
                None if execution_error else trace_record.decision.decision_id
            )
            assert _action_output['error_code'] == ('SERVICE_ERROR' if execution_error else None)
            assert success is (outcome == 'success')
            if outcome == 'unknown_authority' or execution_error:
                assert _action_output['execution_id'] is None
                assert _action_output['execution_status'] is None
                if outcome == 'unknown_authority':
                    assert 'AUTHORITY_NOT_GRANTED' in _action_output['reason_codes']
            else:
                command = next(
                    item for item in _commands if item.proposal_id == _action_output['proposal_id']
                )
                assert _action_output['execution_id'] == command.execution_id
                assert _action_output['decision_id'] == command.decision_id
                assert command.approved_state_id == trace_record.decision.checked_state_id
                assert _action_output['execution_status'] == (
                    'SUCCEEDED' if outcome == 'success' else
                    'TIMED_OUT' if outcome == 'execution_timeout' else 'FAILED'
                )
                assert _action_output['failure_code'] == (
                    None if outcome == 'success' else 'TEST_EXECUTION_FAILURE'
                )
        if outcome == 'unknown_authority':
            assert 'AUTHORITY_NOT_GRANTED' in trace_decision_records[0].decision.reason_codes
            speed_source.read_validated_speed_limit.assert_not_called()
        else:
            for command, trace_record in zip(_commands, trace_decision_records, strict=True):
                assert command.proposal_id == trace_record.proposal.proposal_id
                assert command.decision_id == trace_record.decision.decision_id
            if _results:
                assert f'state_id=state-{100 + len(_commands)}-' in _observations[-1].observation
                assert _observations[-1].observation != _observations[0].observation
            assert len({command.execution_id for command in _commands}) == len(_commands)
        replay = TraceReplay(trace_path)
        trace_result_records = tuple(replay.iter_skill_result_records())
        trace_error_records = tuple(replay.iter_skill_execution_error_records())
        if outcome in {'success', 'execution_failure', 'execution_timeout'}:
            assert [trace_record.command for trace_record in trace_result_records] == _commands
            assert [trace_record.result for trace_record in trace_result_records] == _results
            for trace_record, trace_decision_record in zip(
                trace_result_records, trace_decision_records, strict=True,
            ):
                assert trace_record.command.decision_id == trace_decision_record.decision.decision_id
                assert (
                    trace_record.command.approved_state_id
                    == trace_decision_record.decision.checked_state_id
                )
                assert trace_decision_record.recorded_at <= trace_record.recorded_at
        else:
            assert not trace_result_records
        if outcome == 'execution_error':
            trace_error_record, = trace_error_records
            assert trace_error_record.command == _commands[0]
            assert (
                trace_error_record.command.decision_id
                == trace_decision_records[0].decision.decision_id
            )
            assert (trace_error_record.error_type, trace_error_record.error_message) == (
                'OSError', 'TEST_BACKEND_UNAVAILABLE',
            )
        else:
            assert not trace_error_records
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in reversed(nodes):
            node.destroy_node()
        rclpy.shutdown()
