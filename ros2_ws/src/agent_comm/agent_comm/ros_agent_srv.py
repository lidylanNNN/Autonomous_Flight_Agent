"""Human-driven ROS-LLM entrypoint for the MX observation slice."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from rclpy.client import Client
from rclpy.node import Node
from rosllm_srvs.srv import LLM, Observation

from flight_agent.components.skills.registry import get_skill_spec
from flight_agent.contracts import SkillName

_SERVICE_TIMEOUT_S = 5.0


def _call_service(node: Node, client: Client, request: Any) -> Any:
    """Call a service with a bounded wait; never leave the human loop hanging."""

    if not client.wait_for_service(timeout_sec=_SERVICE_TIMEOUT_S):
        raise RuntimeError(f'SERVICE_UNAVAILABLE: {client.srv_name}')
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=_SERVICE_TIMEOUT_S)
    if not future.done() or future.result() is None:
        raise RuntimeError(f'SERVICE_CALL_FAILED: {client.srv_name}')
    return future.result()


class RosEnv:
    """Own observation service access and the configured action library."""

    def __init__(self, node: Node) -> None:
        self._node = node
        self._observation_client = node.create_client(Observation, 'get_observation')

    def get_obs(self) -> Observation.Response:
        """Request the current observation from Observation Manager."""

        return cast(
            Observation.Response,
            _call_service(self._node, self._observation_client, Observation.Request()),
        )

    def get_atomic_action_library(self) -> str:
        """Read the action index and reuse Skill Registry descriptions."""

        config_path = Path(get_package_share_directory('agent_comm')) / 'config' / 'atomic_action_library.yaml'
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or not isinstance(config.get('skills'), list):
            raise TypeError('INVALID_ATOMIC_ACTION_LIBRARY')
        descriptions = []
        for name in config['skills']:
            spec = get_skill_spec(SkillName(name))
            descriptions.append(
                f'{spec.name.value}: {spec.description} '
                'input=MX_UNIMPLEMENTED output=MX_UNIMPLEMENTED'
            )
        return '\n'.join(descriptions)


class RosAgent:
    """Combine human input and observation, then call the LLM service."""

    def __init__(self, node: Node, action_library: str) -> None:
        self._node = node
        self._action_library = action_library
        self._llm_client = node.create_client(LLM, 'call_llm')

    def get_action(self, human: str, obs: Observation.Response) -> LLM.Response:
        """Return the LLM result; failed observations cannot become flight plans."""

        if not obs.success:
            return LLM.Response(success=False, response='', info='OBSERVATION_UNAVAILABLE')
        prompt = (
            f'AtomicActionLibrary:\n{self._action_library}\n'
            f'Observation source: {obs.source}\nObservation: {obs.observation}\n'
            f'HumanInput: {human}'
        )
        return cast(
            LLM.Response,
            _call_service(self._node, self._llm_client, LLM.Request(prompt=prompt)),
        )


def main(input_reader: Callable[[str], str] | None = None) -> None:
    """Observe, wait for a human, ask Agent, then observe again on failure."""

    owns_context = not rclpy.ok()
    if owns_context:
        rclpy.init()
    node = rclpy.create_node('ros_agent_srv')
    try:
        env = RosEnv(node)
        agent = RosAgent(node, env.get_atomic_action_library())
        obs = env.get_obs()
        read_input = input_reader or input
        while rclpy.ok():
            try:
                human = read_input('任务> ')
            except (EOFError, KeyboardInterrupt):
                break
            if not human.strip():
                continue
            try:
                result = agent.get_action(human, obs)
            except RuntimeError as error:
                node.get_logger().warning(str(error))
                obs = env.get_obs()
                continue
            if not result.success:
                node.get_logger().warning(result.info)
            else:
                node.get_logger().error('BEHAVIOR_EXECUTOR_NOT_CONNECTED_MX2')
            obs = env.get_obs()
    finally:
        node.destroy_node()
        if owns_context and rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
