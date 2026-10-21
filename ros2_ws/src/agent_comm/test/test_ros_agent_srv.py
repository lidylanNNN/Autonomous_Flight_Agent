"""Exercise the real human entrypoint through ROS observation and LLM services."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import rclpy
from agent_comm.llm_node import LLMNode
from agent_comm.ros_agent_srv import main
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from observation_manager.observation_manager_node import ObservationManagerNode
from rclpy.executors import MultiThreadedExecutor
from rosllm_srvs.srv import LLM, Observation

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
