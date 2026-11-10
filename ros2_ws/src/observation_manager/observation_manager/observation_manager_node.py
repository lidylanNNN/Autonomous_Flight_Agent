"""Expose PX4 WorldState through the ROS-LLM Observation service."""

from __future__ import annotations

import json

import rclpy
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rosllm_srvs.srv import Observation


class ObservationManagerNode(Node):
    """Own the PX4 state reader and answer observation requests."""

    def __init__(self, state_source: Px4WorldStateAdapter | None = None) -> None:
        super().__init__('observation_manager_node')
        self.state_source = state_source or Px4WorldStateAdapter()
        self.create_service(Observation, 'get_observation', self.get_observation)

    def get_observation(
        self, _request: Observation.Request, response: Observation.Response
    ) -> Observation.Response:
        '''读取快照并生成状态摘要；取得快照不表示安全批准或任务完成。'''

        try:
            state = self.state_source.read_world_state()
        except RuntimeError as error:
            response.success = False
            response.observation = ''
            response.source = 'px4_world_state_adapter'
            response.message = str(error)
            return response

        response.success = True
        summary = state.model_dump(mode='json')
        summary['state_age_ms'] = state.age_ms()
        response.observation = (
            f'WorldState snapshot state_id={state.state_id}\n'
            'Local frame=NED: x=north, y=east, z=down; '
            'position in m, velocity in m/s; battery in percent.\n'
            'null=unknown; validity and health flags are snapshot facts, '
            'not safety approval or task verification.\n'
            + json.dumps(summary, ensure_ascii=False, sort_keys=True)
        )
        response.source = 'px4_world_state_adapter'
        response.message = 'WorldState snapshot acquired'
        return response


def main() -> None:
    """Run observation service and PX4 subscriptions in one executor."""

    rclpy.init()
    node = ObservationManagerNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    executor.add_node(node.state_source)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.state_source.destroy_node()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
