"""Exercise the real human entrypoint through ROS observation and LLM services."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import rclpy
from agent_comm.llm_node import LLMNode
from agent_comm.ros_agent_srv import RosAgent, RosEnv, main
from behavior_executor.sequence_executor import SequenceExecutor
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from observation_manager.observation_manager_node import ObservationManagerNode
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from rosllm_srvs.srv import AtomicAction, LLM, Observation

from flight_agent.components.vehicle.state import WorldStateAggregator


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
        main(input_reader=read_input)
        assert observation.calls == 2
        assert len(llm.prompts) == 1
        assert 'HumanInput: 检查当前位置' in llm.prompts[0]
        assert 'state_id=state-21-1' in llm.prompts[0]
        assert 'takeoff: 起飞到指定的正数目标高度。' in llm.prompts[0]
        assert 'input=MX_UNIMPLEMENTED' in llm.prompts[0]
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
        calls.append(('hold', request.input, request.prev_output))
        response.success = not fail_hold
        response.output = 'hold-output' if response.success else 'hold-failed'
        return response

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
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
        main(input_reader=read_input)
        assert observation.calls == 3
        assert len(llm.prompts) == 2
        assert calls == [
            ('hold', 'wait', ''), ('rtl', 'home', 'hold-output'), ('hold', 'wait', ''),
        ]
        assert steps == [(-0.5, False, {}), (-1.0, False, {})]
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
