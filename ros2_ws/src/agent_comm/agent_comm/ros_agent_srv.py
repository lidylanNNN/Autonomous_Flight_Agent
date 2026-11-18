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

from flight_agent.components.skills.registry import get_atomic_action_spec
from flight_agent.components.tracing import TraceRecorder

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
        '''持有观察/执行客户端、Trace 和独立结果目录。'''
        self._node = node
        self._recorder = recorder
        self._result_dir = Path(str(node.declare_parameter(
            'behavior_result_dir', 'artifacts/behavior_results',
        ).value)).resolve()
        self._observation_client = node.create_client(Observation, 'get_observation')
        self._behavior_client = node.create_client(ExecuteBehavior, 'execute_behavior')

    def get_obs(self) -> Observation.Response:
        '''取得并记录观察，同时向操作者显示状态或获取失败原因。'''

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
        if response.success:
            self._node.get_logger().info(
                f'当前观察（来源：{response.source}）：\n{response.observation}'
            )
        else:
            self._node.get_logger().warning(
                f'观察获取失败（来源：{response.source}）：{response.message}'
            )
        return response

    def get_atomic_action_library(self) -> str:
        '''从共享白名单生成说明，区分飞行审批结果与拍照保存结果。'''

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
                'Flight actions only (takeoff, goto, hold, rtl, land) output fields: '
                'source (flight_atomic_action_server), '
                'proposal_id/decision_id/execution_id (string or null), reason_codes (string array), '
                'execution_status/failure_code/error_code (string or null), '
                'verification (INCONCLUSIVE). Unproduced fields are null; reason_codes may be empty. '
                'success=true requires Safety APPROVE and execution SUCCEEDED; '
                'false reports rejection, failure or service error. '
                'INCONCLUSIVE is not verified task success.'
            ),
        ]
        for name in config['skills']:
            spec = get_atomic_action_spec(name)
            descriptions.append(
                f'{spec.name}: {spec.description} '
                f'input_schema={json.dumps(spec.args_schema, ensure_ascii=False, sort_keys=True)}'
            )
            if spec.output_description:
                descriptions.append(f'{spec.name} output fields: {spec.output_description}')
        return '\n'.join(descriptions)

    def step(self, behavior: str) -> tuple[Observation.Response, float, bool, dict[str, object]]:
        '''执行行为并展示原始诊断，随后更新观察；执行成功不等于任务已验证。'''

        result = cast(
            ExecuteBehavior.Response,
            _call_service(
                self._node,
                self._behavior_client,
                ExecuteBehavior.Request(behavior=behavior),
                timeout_s=_BEHAVIOR_TIMEOUT_S,
            ),
        )
        status = '动作序列执行成功' if result.success else '动作序列执行失败或被拒绝'
        self._deliver_result(result, status)
        self._node.get_logger().info('任务验证：INCONCLUSIVE（未完成独立任务验证）')
        obs = self.get_obs()
        return obs, (-0.5 if result.success else -1.0), False, {}

    def _deliver_result(self, result: ExecuteBehavior.Response, status: str) -> None:
        '''显示服务原始结果并独立保存；交付错误不改变执行事实。'''
        logger = self._node.get_logger()
        try:
            report = json.loads(result.message)
            if (
                not isinstance(report, dict) or type(report.get('schema_version')) is not int
                or report['schema_version'] != 1 or not isinstance(report.get('summary'), str)
                or not isinstance(report.get('steps'), list)
            ):
                raise ValueError('invalid result envelope')
            for index, step in enumerate(report['steps'], start=1):
                if (
                    not isinstance(step, dict) or type(step.get('index')) is not int
                    or step['index'] != index or not isinstance(step.get('name'), str)
                    or not step['name'] or not {'success', 'output', 'error'} <= step.keys()
                    or (step['success'] is not None and type(step['success']) is not bool)
                    or (step['output'] is not None and not isinstance(step['output'], str))
                    or (step['error'] is not None and not isinstance(step['error'], str))
                    or (step['success'] is None and (
                        step['output'] is not None or not step['error']
                    ))
                    or (step['success'] is not None and (
                        step['output'] is None or step['error'] is not None
                    ))
                ):
                    raise ValueError('invalid step result')
        except (ValueError, TypeError) as error:
            logger.error(f'INVALID_BEHAVIOR_RESULT: {error}; {status}：{result.message}')
            return
        logger.info(f'{status}：{report["summary"]}')
        for step in report['steps']:
            logger.info(
                f'动作结果[{step["index"]}] {step["name"]} success={step["success"]}: '
                f'{step["output"] if step["output"] is not None else step["error"]}'
            )
        report = {
            'schema_version': 1, 'success': result.success, 'info': result.info,
            'summary': report['summary'], 'steps': report['steps'],
        }
        path = self._result_dir / f'{uuid4().hex}.json'
        created = False
        try:
            self._result_dir.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                created = True
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        except OSError as error:
            logger.error(f'BEHAVIOR_RESULT_SAVE_FAILED: {error}')
            if created:
                try:
                    path.unlink()
                except OSError as cleanup_error:
                    logger.error(f'不完整结果文件清理失败：{path}: {cleanup_error}')
            return
        logger.info(f'行为结果文件：{path}')


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
                node.get_logger().warning(f'模型调用失败，未派发行为：{error}')
                obs = env.get_obs()
                continue
            if not result.success:
                node.get_logger().warning(f'本轮决策失败，未派发行为：{result.info}')
                obs = env.get_obs()
                continue
            try:
                behavior = agent.extract_behavior(result)
            except ValueError as error:
                node.get_logger().warning(f'行为提取失败，未派发行为：{error}')
                obs = env.get_obs()
                continue
            node.get_logger().info(f'behavior_source={result.info}')
            try:
                obs, reward, done, info = env.step(behavior)
            except RuntimeError as error:
                node.get_logger().warning(f'行为服务调用失败，执行状态未知：{error}')
                obs = env.get_obs()
                continue
            node.get_logger().debug(f'behavior_reward={reward} done={done} info={info}')
    finally:
        node.destroy_node()
        if owns_context and rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
