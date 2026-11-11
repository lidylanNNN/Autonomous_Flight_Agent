"""Human-driven ROS-LLM entrypoint for observation and behavior execution."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from rclpy.client import Client
from rclpy.node import Node
from rosllm_srvs.srv import LLM, ExecuteBehavior, Observation

from flight_agent.components.skills.registry import get_skill_spec
from flight_agent.components.tracing import TraceRecorder
from flight_agent.contracts import SkillName

_SERVICE_TIMEOUT_S = 5.0
_LLM_RESPONSE_TIMEOUT_S = 35.0
# 与 SequenceExecutor 默认预算同步：最多 16 项，每项发现服务和等待响应各 150 秒。
_BEHAVIOR_TIMEOUT_S = 16 * (150.0 + 150.0) + _SERVICE_TIMEOUT_S
_BEHAVIOR_BLOCK = re.compile(r'\s*```json\s*\n(?P<behavior>.*?)\n```\s*', re.DOTALL)


def _call_service(
    node: Node, client: Client, request: Any, timeout_s: float = _SERVICE_TIMEOUT_S,
    *, discovery_timeout_s: float | None = None,
) -> Any:
    '''分别限制服务发现和响应等待；未指定发现预算时保持原行为。'''

    if not client.wait_for_service(
        timeout_sec=timeout_s if discovery_timeout_s is None else discovery_timeout_s,
    ):
        raise RuntimeError(f'SERVICE_UNAVAILABLE: {client.srv_name}')
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_s)
    if not future.done() or future.result() is None:
        raise RuntimeError(f'SERVICE_CALL_FAILED: {client.srv_name}')
    return future.result()


class RosEnv:
    """Own observation and behavior service access and the action library."""

    def __init__(self, node: Node, recorder: TraceRecorder) -> None:
        self._node = node
        self._recorder = recorder
        self._observation_client = node.create_client(Observation, 'get_observation')
        self._behavior_client = node.create_client(ExecuteBehavior, 'execute_behavior')

    def get_obs(self) -> Observation.Response:
        """Request the current observation from Observation Manager."""

        response = cast(
            Observation.Response,
            _call_service(self._node, self._observation_client, Observation.Request()),
        )
        try:
            self._recorder.record_observation(
                success=response.success, observation=response.observation,
                source=response.source, message=response.message,
            )
        except OSError as error:
            raise OSError(f'OBSERVATION_TRACE_WRITE_FAILED: {error}') from error
        return response

    def get_atomic_action_library(self) -> str:
        '''从动作白名单和 Registry 通用生成飞行服务说明。'''

        config_path = Path(get_package_share_directory('agent_comm')) / 'config' / 'atomic_action_library.yaml'
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or not isinstance(config.get('skills'), list):
            raise TypeError('INVALID_ATOMIC_ACTION_LIBRARY')
        descriptions = [
            (
                'All actions: type=ROS2 service (rosllm_srvs/srv/AtomicAction). '
                'input is a JSON object encoded as a string; input_schema below describes that object. '
                'All distances/altitudes/radii are in metres, duration_s in seconds. '
                'goto uses local NED north/east; altitude is positive upward. '
                'Only business arguments are allowed; no safety limits, authority, IDs or timeout. '
                'prev_output carries the preceding action output unchanged; '
                'flight services do not interpret it as arguments or authorization. '
                'V1 has no cancellation protocol.'
            ),
            (
                'All actions output: AtomicAction.Response(success: bool, output: JSON string). '
                'output fields: source (flight_atomic_action_server), '
                'proposal_id/decision_id/execution_id (string or null), reason_codes (string array), '
                'execution_status/failure_code/error_code (string or null), '
                'verification (INCONCLUSIVE). Unproduced fields are null; reason_codes may be empty. '
                'success=true requires Safety APPROVE and execution SUCCEEDED; '
                'false reports rejection, failure or service error. '
                'INCONCLUSIVE is not verified task success.'
            ),
        ]
        for name in config['skills']:
            spec = get_skill_spec(SkillName(name))
            descriptions.append(
                f'{spec.name.value}: {spec.description} '
                f'input_schema={json.dumps(spec.args_schema, ensure_ascii=False, sort_keys=True)}'
            )
        return '\n'.join(descriptions)

    def step(self, behavior: str) -> tuple[Observation.Response, float, bool, dict[str, object]]:
        """Execute one behavior, then refresh the observation even if it fails."""

        result = cast(
            ExecuteBehavior.Response,
            _call_service(
                self._node,
                self._behavior_client,
                ExecuteBehavior.Request(behavior=behavior),
                timeout_s=_BEHAVIOR_TIMEOUT_S,
            ),
        )
        self._node.get_logger().info(
            f'execute_behavior success={result.success} info={result.info} message={result.message}'
        )
        obs = self.get_obs()
        return obs, (-0.5 if result.success else -1.0), False, {}


class RosAgent:
    """Combine human input and observation, then call the LLM service."""

    def __init__(self, node: Node, action_library: str, recorder: TraceRecorder) -> None:
        self._node = node
        self._recorder = recorder
        self._action_library = action_library
        self._llm_client = node.create_client(LLM, 'call_llm')

    def get_action(self, human: str, obs: Observation.Response) -> LLM.Response:
        '''组合人工输入、观察、动作库与输出格式指令；观察失败时不调用模型。'''

        if not obs.success:
            return LLM.Response(success=False, response='', info='OBSERVATION_UNAVAILABLE')
        prompt = (
            'ResponseFormat:\n'
            'Return only one ```json code block containing JSON Lines, '
            'with 1 to 16 actions, one JSON object per line. '
            'Each object must contain exactly "name" and "input". '
            'The name must be an action name from AtomicActionLibrary. '
            'The input must be a string encoding one JSON object that conforms to '
            'that action input_schema; for parameterless actions use the string "{}". '
            'Do not return a JSON array, extra fields, executable code, '
            'multiple code blocks, or any text outside the code block.\n'
            f'AtomicActionLibrary:\n{self._action_library}\n'
            f'Observation source: {obs.source}\nObservation: {obs.observation}\n'
            f'HumanInput: {human}'
        )
        response = cast(
            LLM.Response,
            _call_service(
                self._node, self._llm_client, LLM.Request(prompt=prompt),
                timeout_s=_LLM_RESPONSE_TIMEOUT_S, discovery_timeout_s=_SERVICE_TIMEOUT_S,
            ),
        )
        try:
            self._recorder.record_llm_response(
                prompt=prompt, success=response.success, response=response.response, info=response.info,
            )
        except OSError as error:
            raise OSError(f'LLM_TRACE_WRITE_FAILED: {error}') from error
        return response

    @staticmethod
    def extract_behavior(result: LLM.Response) -> str:
        """Keep every JSON Lines action in one fenced LLM response."""

        match = _BEHAVIOR_BLOCK.fullmatch(result.response)
        if match is None or not match.group('behavior').strip():
            raise ValueError('INVALID_BEHAVIOR_RESPONSE: expected one nonempty json code block')
        return match.group('behavior').strip()


def main(
    input_reader: Callable[[str], str] | None = None, *, trace_path: Path | None = None,
) -> None:
    """Observe, wait for a human, execute valid behavior, then observe again."""

    owns_context = not rclpy.ok()
    if owns_context:
        rclpy.init()
    node = rclpy.create_node('ros_agent_srv')
    try:
        parameter = node.declare_parameter(
            'agent_trace_path', f'artifacts/agent_trace_{uuid4().hex}.jsonl',
        )
        resolved_path = trace_path if trace_path is not None else Path(str(parameter.value))
        recorder = TraceRecorder(resolved_path)
        node.get_logger().info(f'agent_trace_path={resolved_path}')
        env = RosEnv(node, recorder)
        agent = RosAgent(node, env.get_atomic_action_library(), recorder)
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
                obs = env.get_obs()
                continue
            try:
                behavior = agent.extract_behavior(result)
            except ValueError as error:
                node.get_logger().warning(str(error))
                obs = env.get_obs()
                continue
            node.get_logger().info(f'behavior_source={result.info}')
            try:
                obs, reward, done, info = env.step(behavior)
            except RuntimeError as error:
                node.get_logger().warning(str(error))
                obs = env.get_obs()
                continue
            node.get_logger().info(f'behavior_reward={reward} done={done} info={info}')
    finally:
        node.destroy_node()
        if owns_context and rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
