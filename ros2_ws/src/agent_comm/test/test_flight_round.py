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
from rosllm_srvs.srv import AtomicAction, Observation

from flight_agent.components.tracing import TraceReplay
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


@pytest.mark.parametrize('outcome', ['success', 'execution_failure', 'unknown_authority'])
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
    commands: list[ApprovedSkillCommand] = []
    original_execute = backend.execute

    async def execute(command: ApprovedSkillCommand) -> SkillResult:
        '''记录实际批准命令，确认决策已落盘，再模拟执行与后续 PX4 状态更新。'''
        record = list(TraceReplay(trace_path).iter_safety_decision_records())[-1]
        assert record.decision.decision is SafetyDecisionType.APPROVE
        assert record.decision.decision_id == command.decision_id
        assert record.proposal.proposal_id == command.proposal_id
        commands.append(command)
        result = await original_execute(command)
        publish_test_position(100 + len(commands))
        if outcome == 'execution_failure':
            return result.model_copy(update={
                'status': SkillExecutionStatus.FAILED, 'failure_code': 'TEST_EXECUTION_FAILURE',
            })
        return result

    monkeypatch.setattr(backend, 'execute', execute)
    speed_source = SimpleNamespace(read_validated_speed_limit=AsyncMock(return_value=8.0))
    authority = (
        ControlAuthority.UNKNOWN if outcome == 'unknown_authority'
        else ControlAuthority.AGENT_ALLOWED
    )
    action_calls: list[tuple[SkillName, str, str, bool, str]] = []
    original_handle_action = FlightAtomicActionServer._handle_action

    def handle_action(
        server: FlightAtomicActionServer, skill_name: SkillName,
        request: AtomicAction.Request, response: AtomicAction.Response,
    ) -> AtomicAction.Response:
        '''记录实际动作请求和响应，仍由原服务执行全部解析、Gate 与结果封装。'''
        result = original_handle_action(server, skill_name, request, response)
        action_calls.append((
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
    llm = LLMNode()
    llm.set_parameters([Parameter('mx_fixed_behavior', value=True)])
    sequence = SequenceExecutor()
    observations: list[Observation.Response] = []
    rewards: list[float] = []
    original_get_obs, original_step = RosEnv.get_obs, RosEnv.step

    def get_obs(env: RosEnv) -> Observation.Response:
        '''保留真实 ROS 观察调用，只记录入口收到的响应。'''
        result = original_get_obs(env)
        observations.append(result)
        return result

    def step(env: RosEnv, behavior: str) -> tuple[Observation.Response, float, bool, dict[str, object]]:
        '''保留真实序列服务调用，只记录传回主循环的结果。'''
        obs, reward, done, info = original_step(env, behavior)
        rewards.append(reward)
        return obs, reward, done, info

    monkeypatch.setattr(RosEnv, 'get_obs', get_obs)
    monkeypatch.setattr(RosEnv, 'step', step)
    executor = MultiThreadedExecutor(num_threads=4)
    nodes = (source, observation, llm, sequence, server)
    for node in nodes:
        executor.add_node(node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    inputs = 0

    def read_input(_prompt: str) -> str:
        '''只输入一轮；下一次提示输入前必须已收到执行结果和第二次观察。'''
        nonlocal inputs
        inputs += 1
        assert len(observations) == inputs
        if inputs == 1:
            return '执行固定两步'
        assert len(rewards) == 1
        raise EOFError

    try:
        main(input_reader=read_input)
        assert inputs == 2
        assert all(obs.success for obs in observations)
        assert rewards == ([-0.5] if outcome == 'success' else [-1.0])
        expected_skills = {
            'success': [SkillName.HOLD, SkillName.RTL],
            'execution_failure': [SkillName.HOLD], 'unknown_authority': [],
        }[outcome]
        assert [command.skill_name for command in commands] == expected_skills
        records = list(TraceReplay(trace_path).iter_safety_decision_records())
        assert [record.proposal.skill_name for record in records] == (
            ['hold', 'rtl'] if outcome == 'success' else ['hold']
        )
        assert len(action_calls) == len(records)
        previous_output = ''
        for (skill, payload, previous, success, output), record in zip(
            action_calls, records, strict=True,
        ):
            assert skill.value == record.proposal.skill_name
            assert json.loads(payload) == ({'duration_s': 1.0} if skill is SkillName.HOLD else {})
            assert previous == previous_output
            previous_output = output
            evidence = json.loads(output)
            assert evidence['proposal_id'] == record.proposal.proposal_id
            assert evidence['decision_id'] == record.decision.decision_id
            assert evidence['verification'] == 'INCONCLUSIVE'
            assert evidence['error_code'] is None
            assert success is (outcome == 'success')
            if outcome == 'unknown_authority':
                assert evidence['execution_id'] is None
                assert evidence['execution_status'] is None
                assert 'AUTHORITY_NOT_GRANTED' in evidence['reason_codes']
            else:
                command = next(item for item in commands if item.proposal_id == evidence['proposal_id'])
                assert evidence['execution_id'] == command.execution_id
                assert evidence['decision_id'] == command.decision_id
                assert command.approved_state_id == record.decision.checked_state_id
                assert evidence['execution_status'] == (
                    'SUCCEEDED' if outcome == 'success' else 'FAILED'
                )
                assert evidence['failure_code'] == (
                    None if outcome == 'success' else 'TEST_EXECUTION_FAILURE'
                )
        if outcome == 'unknown_authority':
            assert 'AUTHORITY_NOT_GRANTED' in records[0].decision.reason_codes
            speed_source.read_validated_speed_limit.assert_not_called()
        else:
            assert f'state_id=state-{100 + len(commands)}-' in observations[-1].observation
            assert observations[-1].observation != observations[0].observation
            assert len({command.execution_id for command in commands}) == len(commands)
    finally:
        executor.shutdown()
        thread.join(timeout=5)
        for node in reversed(nodes):
            node.destroy_node()
        rclpy.shutdown()
