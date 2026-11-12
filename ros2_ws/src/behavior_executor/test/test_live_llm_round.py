'''单动作入口验收；真实 API 必须显式启用，飞行与状态始终使用替身。'''

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import rclpy
from agent_comm import llm_node
from agent_comm.ros_agent_srv import main
from behavior_executor.sequence_executor import SequenceExecutor
from flight_agent_ros.flight_atomic_action_server import build_flight_atomic_action_server
from flight_agent_ros.safety_config import load_safety_constraints
from observation_manager.observation_manager_node import ObservationManagerNode
from rclpy.executors import MultiThreadedExecutor

from flight_agent.components.tracing import TraceRecorder, TraceReplay
from flight_agent.components.vehicle.flight_execution import MockFlightExecutionBackend
from flight_agent.contracts import (
    ApprovedSkillCommand,
    ControlAuthority,
    GlobalPosition,
    SafetyDecisionType,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
    WorldState,
)


@pytest.mark.parametrize('test_input_model', [
    'offline',
    pytest.param('live', marks=pytest.mark.skipif(
        os.environ.get('FLIGHT_M5_LIVE_API') != '1', reason='真实付费 API 仅显式启用',
    )),
])
def test_single_takeoff_from_human_entry_through_model_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_input_model: str,
) -> None:
    '''由实际主入口完成一次起飞请求、Gate 执行、再观察并等待人。'''
    test_input_human = '请只执行一个动作：起飞到离地3米，然后等待我的下一条指令。'
    if test_input_model == 'offline':
        monkeypatch.setenv('FLIGHT_LLM_API_KEY', 'test-only-secret')
        monkeypatch.setenv('FLIGHT_LLM_BASE_URL', 'https://api.deepseek.com')
        monkeypatch.setenv('FLIGHT_LLM_MODEL', 'deepseek-flash')
        test_temp_response = MagicMock()
        test_temp_response.__enter__.return_value = test_temp_response
        test_temp_response.status = 200
        test_temp_response.read.return_value = json.dumps({
            'id': 'test-response', 'model': 'deepseek-flash',
            'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant',
                'content': '```json\n{"name":"takeoff","input":"{\\"target_altitude_m\\":3}"}\n```',
            }}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                      'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 10},
        }).encode()
        test_temp_opener = Mock()
        test_temp_opener.open.return_value = test_temp_response
        monkeypatch.setattr(llm_node.urllib.request, 'build_opener', lambda *_: test_temp_opener)
    assert os.environ.get('FLIGHT_LLM_API_KEY'), '缺少环境密钥；未发请求'
    test_temp_requests = 0
    test_temp_completion = llm_node._request_completion

    def record_request(prompt: str, info: dict[str, object]) -> str:
        '''请求前落盘 prompt 并限制仅调用一次；不保存认证信息、不修改回复。'''
        nonlocal test_temp_requests
        test_temp_requests += 1
        assert test_temp_requests == 1, '禁止本验收自动追加模型请求'
        assert len(prompt.encode('utf-8')) < 20_000, 'prompt 超出本轮人工预算估计范围'
        (tmp_path / 'prompt.txt').write_text(prompt)
        return test_temp_completion(prompt, info)

    monkeypatch.setattr(llm_node, '_request_completion', record_request)
    state = WorldState(
        state_id='test-ground-state', source_timestamp_us=100, received_at=datetime.now(UTC),
        state_age_ms=0, position_ned_m=Vector3(x=0, y=0, z=0),
        velocity_ned_mps=Vector3(x=0, y=0, z=0), battery_percent=80,
        armed=False, landed=True, flight_mode='POSCTL', position_valid=True,
        home_valid=True, home_position_ned_m=Vector3(x=0, y=0, z=0),
        home_position_wgs84=GlobalPosition(latitude_deg=47, longitude_deg=8, altitude_amsl_m=500),
        link_healthy=True, health_flags={
            'battery_connected': True, 'battery_valid': True, 'battery_fault_free': True,
            'failure_detector_clear': True, 'preflight_checks_pass': True,
        },
    )
    backend = MockFlightExecutionBackend(state)
    test_temp_world_recorder = TraceRecorder(tmp_path / 'world.jsonl')

    def read_state() -> WorldState:
        '''提供持续新鲜的模拟状态；该状态来源不是 PX4 实测。'''
        state = backend._state.model_copy(update={'received_at': datetime.now(UTC), 'state_age_ms': 0})
        test_temp_world_recorder.record_world_state(state)
        return state

    async def get_state() -> WorldState:
        '''让真实 Gate 和观察管理器读取同一替身状态来源。'''
        return read_state()

    monkeypatch.setattr(backend, 'get_world_state', get_state)
    test_temp_commands: list[ApprovedSkillCommand] = []
    test_temp_execute = backend.execute
    safety_trace_path = tmp_path / 'safety.jsonl'

    async def execute(command: ApprovedSkillCommand) -> SkillResult:
        '''记录真实 Gate 批准的命令，再调用已有 Backend 替身。'''
        decision, = TraceReplay(safety_trace_path).iter_safety_decision_records()
        assert decision.decision.decision is SafetyDecisionType.APPROVE
        assert command.decision_id == decision.decision.decision_id
        test_temp_commands.append(command)
        return await test_temp_execute(command)

    monkeypatch.setattr(backend, 'execute', execute)
    test_input_safety = Path(__file__).resolve().parents[4] / 'configs/flight_safety.example.json'
    constraints = load_safety_constraints(test_input_safety).model_copy(update={'max_altitude_m': 5.0})
    (tmp_path / 'safety.json').write_text(constraints.model_dump_json(indent=2))
    (tmp_path / 'test_temp_evidence.json').write_text(json.dumps({
        'model_mode': test_input_model, 'human_input': test_input_human,
        'state_source': 'in_memory_substitute', 'backend': 'MockFlightExecutionBackend',
        'authority': 'AGENT_ALLOWED_substitute', 'speed_limit_mps': 8,
    }, ensure_ascii=False, indent=2))
    rclpy.init()
    server = build_flight_atomic_action_server(
        constraints, backend, trace_path=safety_trace_path,
        authority_reader=lambda: ControlAuthority.AGENT_ALLOWED,
        speed_limit_source=SimpleNamespace(read_validated_speed_limit=AsyncMock(return_value=8.0)),
    )
    observation = ObservationManagerNode(SimpleNamespace(read_world_state=read_state))
    llm = llm_node.LLMNode()
    sequence = SequenceExecutor()
    executor = MultiThreadedExecutor(num_threads=4)
    test_temp_nodes = (observation, llm, sequence, server)
    for node in test_temp_nodes:
        executor.add_node(node)
    test_temp_thread = Thread(target=executor.spin, daemon=True)
    test_temp_thread.start()
    test_temp_inputs = 0
    agent_trace_path = tmp_path / 'agent.jsonl'

    def read_input(_prompt: str) -> str:
        '''只提供一次人工输入；再次等待时退出，不触发第二轮模型调用。'''
        nonlocal test_temp_inputs
        test_temp_inputs += 1
        if test_temp_inputs == 1:
            return test_input_human
        raise EOFError

    try:
        main(input_reader=read_input, trace_path=agent_trace_path)
        reply, = TraceReplay(agent_trace_path).iter_llm_response_records()
        info = json.loads(reply.info)
        if isinstance(info.get('usage'), dict):
            usage = info['usage']
            test_temp_cost = (
                usage['prompt_cache_hit_tokens'] * 0.04
                + usage['prompt_cache_miss_tokens'] * 2 + usage['completion_tokens'] * 8
            ) / 1_000_000
            (tmp_path / 'test_temp_cost.json').write_text(json.dumps({
                'usage': usage, 'peak_rate_cost_cny': test_temp_cost,
                'pricing_url': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
                'rates_per_million_cny': {'cache_hit': 0.04, 'cache_miss': 2, 'output': 8},
            }, indent=2))
            assert test_temp_cost < 1
        assert reply.success, reply.info
        assert test_temp_inputs == 2 and test_temp_requests == 1
        assert len(test_temp_commands) == 1
        command, = test_temp_commands
        assert command.skill_name is SkillName.TAKEOFF
        assert command.arguments.target_altitude_m == 3
        result, = TraceReplay(safety_trace_path).iter_skill_result_records()
        assert result.command == command
        assert result.result.status is SkillExecutionStatus.SUCCEEDED
        observations = tuple(TraceReplay(agent_trace_path).iter_observation_records())
        assert len(observations) == 2 and all(item.success for item in observations)
        assert f'HumanInput: {test_input_human}' in reply.prompt
        assert f'Observation: {observations[0].observation}\n' in reply.prompt
        assert json.loads(observations[0].observation.splitlines()[-1])['landed'] is True
        assert json.loads(observations[1].observation.splitlines()[-1])['position_ned_m']['z'] == -3
        print(f'Evidence: {tmp_path}')
    finally:
        executor.shutdown()
        test_temp_thread.join(timeout=5)
        for node in reversed(test_temp_nodes):
            node.destroy_node()
        rclpy.shutdown()
