"""LLM service with an opt-in fixed behavior for MX-3 integration."""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from rosllm_srvs.srv import LLM

_FIXED_BEHAVIOR = (
    '```json\n'
    '{"name":"hold","input":"wait"}\n'
    '{"name":"rtl","input":"home"}\n'
    '```'
)


class LLMNode(Node):
    """Own the ROS-LLM service without generating autonomous behavior."""

    def __init__(self) -> None:
        super().__init__('llm_node')
        self.declare_parameter('mx_fixed_behavior', False)
        self.create_service(LLM, 'call_llm', self.get_response)

    def get_response(self, _request: LLM.Request, response: LLM.Response) -> LLM.Response:
        """Return fixed test behavior only when the ROS parameter enables it."""

        if self.get_parameter('mx_fixed_behavior').value is True:
            response.success = True
            response.response = _FIXED_BEHAVIOR
            response.info = 'MX_FIXED_BEHAVIOR: hold,rtl'
            return response
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
