"""LLM service with an explicit unavailable result until MX-3."""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from rosllm_srvs.srv import LLM


class LLMNode(Node):
    """Own the ROS-LLM service without generating autonomous behavior."""

    def __init__(self) -> None:
        super().__init__('llm_node')
        self.create_service(LLM, 'llm', self.get_response)

    def get_response(self, _request: LLM.Request, response: LLM.Response) -> LLM.Response:
        """Return an explicit failure for every prompt."""

        response.success = False
        response.response = ''
        response.info = 'LLM_UNAVAILABLE_MX2'
        return response


def main() -> None:
    """Run the default unavailable LLM service."""

    rclpy.init()
    node = LLMNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
