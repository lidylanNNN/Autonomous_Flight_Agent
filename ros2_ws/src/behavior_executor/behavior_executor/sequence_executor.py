"""Execute a validated JSON Lines behavior through AtomicAction services."""

from __future__ import annotations

import json
from pathlib import Path

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rosllm_srvs.srv import AtomicAction, ExecuteBehavior

_MAX_ACTIONS = 16
_DEFAULT_TIMEOUT_S = 30.0


class SequenceExecutor(Node):
    """Own the behavior service and its allowlisted AtomicAction clients."""

    def __init__(self, action_timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        super().__init__('sequence_executor')
        if action_timeout_s <= 0:
            raise ValueError('action_timeout_s must be positive')
        self._timeout_s = action_timeout_s
        config_path = (
            Path(get_package_share_directory('agent_comm'))
            / 'config'
            / 'atomic_action_library.yaml'
        )
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        names = config.get('skills') if isinstance(config, dict) else None
        if not isinstance(names, list) or not names or any(
            not isinstance(name, str) or not name for name in names
        ) or len(names) != len(set(names)):
            raise ValueError('INVALID_ATOMIC_ACTION_LIBRARY')
        self._action_names = frozenset(names)
        self._callbacks = ReentrantCallbackGroup()
        self._action_clients = {
            name: self.create_client(AtomicAction, name, callback_group=self._callbacks)
            for name in names
        }
        self.create_service(
            ExecuteBehavior,
            'execute_behavior',
            self.execute_behavior,
            callback_group=self._callbacks,
        )

    def _parse(self, behavior: str) -> list[tuple[str, str]]:
        actions: list[tuple[str, str]] = []
        for line_number, raw_line in enumerate(behavior.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                action = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f'INVALID_BEHAVIOR line={line_number}: {error.msg}') from error
            if not isinstance(action, dict) or set(action) != {'name', 'input'}:
                raise ValueError(f'INVALID_BEHAVIOR line={line_number}: expected name and input')
            name, action_input = action['name'], action['input']
            if not isinstance(name, str) or name not in self._action_names:
                raise ValueError(f'INVALID_ACTION line={line_number}: {name!r}')
            if not isinstance(action_input, str):
                raise ValueError(f'INVALID_INPUT line={line_number}: expected text')
            actions.append((name, action_input))
            if len(actions) > _MAX_ACTIONS:
                raise ValueError(f'INVALID_BEHAVIOR: more than {_MAX_ACTIONS} actions')
        if not actions:
            raise ValueError('INVALID_BEHAVIOR: empty sequence')
        return actions

    def execute_behavior(
        self, request: ExecuteBehavior.Request, response: ExecuteBehavior.Response
    ) -> ExecuteBehavior.Response:
        """Validate before dispatch, pass each output forward, and stop on failure."""

        try:
            actions = self._parse(request.behavior)
        except ValueError as error:
            response.success = False
            response.message = str(error)
            response.info = -1
            return response

        prev_output = ''
        for name, action_input in actions:
            client = self._action_clients[name]
            if not client.wait_for_service(timeout_sec=self._timeout_s):
                response.success = False
                response.message = f'ATOMIC_ACTION_UNAVAILABLE name={name}'
                response.info = -2
                return response
            try:
                result = client.call(
                    AtomicAction.Request(input=action_input, prev_output=prev_output),
                    timeout_sec=self._timeout_s,
                )
            except Exception as error:
                response.success = False
                response.message = f'ATOMIC_ACTION_CALL_FAILED name={name}: {error}'
                response.info = -6
                return response
            if result is None:
                response.success = False
                response.message = f'ATOMIC_ACTION_TIMEOUT name={name}'
                response.info = -6
                return response
            if not result.success:
                response.success = False
                response.message = f'ATOMIC_ACTION_FAILED name={name}: {result.output}'
                response.info = 0
                return response
            prev_output = result.output

        response.success = True
        response.message = 'successfully executed behavior'
        response.info = 0
        return response


def main() -> None:
    """Run the ROS 2 behavior service with concurrent action response handling."""

    rclpy.init()
    node = SequenceExecutor()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
