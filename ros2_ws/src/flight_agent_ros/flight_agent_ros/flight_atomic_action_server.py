'''Safety-gated ROS AtomicAction service for flight skills.'''

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from math import isfinite
from pathlib import Path
from uuid import uuid4

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rosllm_srvs.srv import AtomicAction

from flight_agent.components.safety import SafetyExecutionGate, SafetySupervisor
from flight_agent.components.tracing import TraceRecorder
from flight_agent.contracts import (
    AtomicActionProposal,
    ControlAuthority,
    ControllerSpeedLimitProtocol,
    FlightExecutionBackendProtocol,
    MissionConstraints,
    SafetyDecisionType,
    SkillExecutionStatus,
    SkillName,
)
from flight_agent_ros.adapters.flight_execution_backend import (
    Px4Ros2FlightExecutionBackend,
)
from flight_agent_ros.adapters.px4_command_plan_executor import Px4CommandPlanExecutor
from flight_agent_ros.adapters.px4_offboard_setpoint_adapter import (
    Px4OffboardSetpointAdapter,
)
from flight_agent_ros.adapters.px4_parameter_adapter import Px4ParameterAdapter
from flight_agent_ros.adapters.px4_vehicle_command_adapter import Px4VehicleCommandAdapter
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from flight_agent_ros.safety_config import load_safety_constraints


def _output() -> dict[str, object]:
    return {
        'source': 'flight_atomic_action_server',
        'proposal_id': None,
        'decision_id': None,
        'execution_id': None,
        'reason_codes': [],
        'execution_status': None,
        'failure_code': None,
        'error_code': None,
        'verification': 'INCONCLUSIVE',
    }


class FlightAtomicActionServer(Node):
    '''Own the hold service, fixed limits, and safety gate.'''

    def __init__(
        self,
        constraints: MissionConstraints,
        backend: FlightExecutionBackendProtocol,
        gate: SafetyExecutionGate,
    ) -> None:
        super().__init__('flight_atomic_action_server')
        self._constraints = constraints
        self._backend = backend
        self._gate = gate
        # The default mutually exclusive callback group serializes hold requests.
        self.create_service(AtomicAction, 'hold', self.hold)

    def hold(
        self, request: AtomicAction.Request, response: AtomicAction.Response
    ) -> AtomicAction.Response:
        '''Validate the finite hold request, then enter the safety gate.'''

        output = _output()
        response.success = False
        try:
            arguments = json.loads(request.input)
        except (json.JSONDecodeError, TypeError):
            arguments = None
        if not isinstance(arguments, dict):
            output['error_code'] = 'INVALID_INPUT'
        else:
            duration = arguments.get('duration_s')
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not isfinite(duration)
                or duration <= 0
            ):
                output['error_code'] = 'INVALID_INPUT'
            else:
                proposal_id = f'proposal-{uuid4().hex}'
                execution_id = f'exec-{uuid4().hex}'
                output['proposal_id'] = proposal_id
                output['execution_id'] = execution_id
                try:
                    response.success = asyncio.run(
                        self._submit_hold(arguments, proposal_id, execution_id, output)
                    )
                except Exception as error:  # noqa: BLE001 - ROS response must report gate failures
                    self.get_logger().error(f'hold service failed: {error}')
                    output['error_code'] = 'SERVICE_ERROR'

        response.output = json.dumps(output, sort_keys=True)
        return response

    async def _submit_hold(
        self,
        arguments: dict[str, object],
        proposal_id: str,
        execution_id: str,
        output: dict[str, object],
    ) -> bool:
        state = await self._backend.get_world_state()
        proposal = AtomicActionProposal(
            proposal_id=proposal_id,
            skill_name=SkillName.HOLD,
            arguments=arguments,
            based_on_state_id=state.state_id,
        )
        decision, result = await self._gate.submit(
            proposal, self._constraints, execution_id=execution_id
        )
        output['decision_id'] = decision.decision_id
        output['reason_codes'] = [code.value for code in decision.reason_codes]
        if result is not None:
            output['execution_status'] = result.status.value
            output['failure_code'] = result.failure_code
        return (
            decision.decision is SafetyDecisionType.APPROVE
            and result is not None
            and result.status is SkillExecutionStatus.SUCCEEDED
        )


def build_flight_atomic_action_server(
    constraints: MissionConstraints,
    backend: FlightExecutionBackendProtocol,
    *,
    trace_path: Path,
    authority_reader: Callable[[], ControlAuthority],
    speed_limit_source: ControllerSpeedLimitProtocol,
) -> FlightAtomicActionServer:
    '''Build the same service and gate used by the production entrypoint.'''

    gate = SafetyExecutionGate(
        SafetySupervisor(speed_limit_source, policy_version='mx4-flight-actions'),
        backend,
        TraceRecorder(trace_path),
        authority_reader,
    )
    return FlightAtomicActionServer(constraints, backend, gate)


def main() -> None:
    '''Load limits before advertising the hold service.'''

    parser = argparse.ArgumentParser(description='Run safety-gated flight AtomicAction services')
    parser.add_argument('--safety-json', required=True, type=Path)
    parser.add_argument('--trace-path', type=Path, default=Path('artifacts/flight_trace.jsonl'))
    arguments = parser.parse_args()
    constraints = load_safety_constraints(arguments.safety_json)

    rclpy.init()
    nodes: list[Node] = []
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        world_state = Px4WorldStateAdapter()
        nodes.append(world_state)
        command_adapter = Px4VehicleCommandAdapter()
        nodes.append(command_adapter)
        offboard_adapter = Px4OffboardSetpointAdapter()
        nodes.append(offboard_adapter)
        backend = Px4Ros2FlightExecutionBackend(
            state_reader=world_state.read_world_state,
            plan_executor=Px4CommandPlanExecutor(command_adapter),
            offboard_adapter=offboard_adapter,
        )
        server = build_flight_atomic_action_server(
            constraints,
            backend,
            trace_path=arguments.trace_path,
            authority_reader=lambda: ControlAuthority.UNKNOWN,
            speed_limit_source=Px4ParameterAdapter(),
        )
        nodes.append(server)
        for node in nodes:
            executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        for node in reversed(nodes):
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
