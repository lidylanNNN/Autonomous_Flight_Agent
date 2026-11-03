'''Run a mission request through the safety-gated skeleton or explicit M3 debug path.'''

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Thread

import rclpy
from rclpy.executors import SingleThreadedExecutor

from flight_agent.components.safety import SafetyExecutionGate, SafetySupervisor
from flight_agent.components.tracing import TraceRecorder
from flight_agent.contracts import (
    ControlAuthority,
    ControllerSpeedLimitProtocol,
    FlightExecutionBackendProtocol,
    MissionContract,
    MissionRequest,
    MissionRunResult,
    MissionRunStatus,
)
from flight_agent.workflows.mission import MissionService
from flight_agent_ros.adapters.flight_execution_backend import (
    Px4Ros2FlightExecutionBackend,
)
from flight_agent_ros.adapters.px4_command_plan_executor import Px4CommandPlanExecutor
from flight_agent_ros.adapters.px4_offboard_setpoint_adapter import (
    Px4OffboardSetpointAdapter,
)
from flight_agent_ros.adapters.px4_parameter_adapter import Px4ParameterAdapter
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    Px4VehicleCommandAdapter,
)
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from flight_agent_ros.entrypoints.m3_scripted_flight import (
    _print_json,
    _wait_for_vehicle_ready,
    run_m3_debug,
)


def main() -> None:
    '''装配 ROS 对象，默认运行受控主链并清理资源。'''

    arguments = _parse_arguments()
    if arguments.legacy_m3_direct:
        run_m3_debug(arguments)
        return
    contract = MissionContract.model_validate_json(
        Path(arguments.contract_json).read_text(encoding='utf-8')
    )
    rclpy.init()
    world_state_adapter = Px4WorldStateAdapter()
    command_adapter = Px4VehicleCommandAdapter()
    offboard_adapter = Px4OffboardSetpointAdapter()
    executor = SingleThreadedExecutor()
    nodes = (world_state_adapter, command_adapter, offboard_adapter)
    for node in nodes:
        executor.add_node(node)
    ros_thread = Thread(target=executor.spin, name='flight-agent-ros', daemon=True)
    ros_thread.start()

    backend = Px4Ros2FlightExecutionBackend(
        state_reader=world_state_adapter.read_world_state,
        plan_executor=Px4CommandPlanExecutor(command_adapter),
        offboard_adapter=offboard_adapter,
    )
    exit_code = 0
    try:
        service = build_mission_service(
            backend,
            contract,
            trace_path=Path(arguments.trace_path),
            authority_reader=lambda: ControlAuthority.UNKNOWN,
            speed_limit_source=Px4ParameterAdapter(),
        )
        report = asyncio.run(
            _run_safety_mission(
                service,
                world_state_adapter,
                MissionRequest(mission_id=contract.mission_id, instruction=arguments.instruction),
                startup_timeout_s=arguments.startup_timeout_s,
            )
        )
        _print_json({'type': 'mission_run_result', **report.model_dump(mode='json')})
        exit_code = 2 if report.status is MissionRunStatus.UNVERIFIED else 1
    except (KeyboardInterrupt, OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(json.dumps({'type': 'script_error', 'error': str(error)}, sort_keys=True))
        exit_code = 1
    finally:
        executor.shutdown()
        ros_thread.join(timeout=5.0)
        for node in reversed(nodes):
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    if exit_code:
        raise SystemExit(exit_code)


def _parse_arguments() -> argparse.Namespace:
    '''解析受控任务入口与显式 M3 调试模式的参数。'''

    parser = argparse.ArgumentParser(description='Run a mission through Safety and PX4')
    parser.add_argument('--instruction')
    parser.add_argument('--contract-json')
    parser.add_argument(
        '--trace-path',
        default=os.environ.get('FLIGHT_AGENT_TRACE_PATH', 'artifacts/mission_trace.jsonl'),
    )
    parser.add_argument('--legacy-m3-direct', action='store_true')
    parser.add_argument('--takeoff-altitude-m', type=float, default=10.0)
    parser.add_argument('--goto-north-m', type=float, default=10.0)
    parser.add_argument('--goto-east-m', type=float, default=5.0)
    parser.add_argument('--goto-altitude-m', type=float, default=10.0)
    parser.add_argument('--acceptance-radius-m', type=float, default=1.0)
    parser.add_argument('--hold-duration-s', type=float, default=3.0)
    parser.add_argument('--skill-timeout-s', type=float, default=60.0)
    parser.add_argument('--startup-timeout-s', type=float, default=30.0)
    parser.add_argument(
        '--set-home-to-current',
        action='store_true',
        help='set PX4 Home to its current position when Home is unavailable',
    )
    raw_arguments = sys.argv[1:]
    arguments = parser.parse_args(raw_arguments)
    if arguments.legacy_m3_direct:
        if arguments.instruction is not None or arguments.contract_json is not None:
            parser.error('legacy M3 mode cannot accept a mission instruction or contract')
    elif not arguments.instruction or not arguments.contract_json:
        parser.error('mission mode requires --instruction and --contract-json')
    elif arguments.set_home_to_current:
        parser.error('mission mode cannot set PX4 Home outside the Safety gate')
    elif any(
        argument == option or argument.startswith(f'{option}=')
        for argument in raw_arguments
        for option in (
            '--takeoff-altitude-m',
            '--goto-north-m',
            '--goto-east-m',
            '--goto-altitude-m',
            '--acceptance-radius-m',
            '--hold-duration-s',
            '--skill-timeout-s',
        )
    ):
        parser.error('mission mode cannot accept legacy M3 flight parameters')
    return arguments


def build_mission_service(
    backend: FlightExecutionBackendProtocol,
    contract: MissionContract,
    *,
    trace_path: Path,
    authority_reader: Callable[[], ControlAuthority],
    speed_limit_source: ControllerSpeedLimitProtocol,
) -> MissionService:
    '''装配入口持有的 Gate 和顶层任务流程，供正式入口及集成测试共用。'''

    gate = SafetyExecutionGate(
        SafetySupervisor(speed_limit_source, policy_version='m4-skeleton'),
        backend,
        TraceRecorder(trace_path),
        authority_reader,
    )
    return MissionService(contract, backend, gate)


async def _run_safety_mission(
    service: MissionService,
    world_state_adapter: Px4WorldStateAdapter,
    request: MissionRequest,
    *,
    startup_timeout_s: float,
) -> MissionRunResult:
    '''等待可观测飞行器状态后，从用户原句运行受控主链。'''

    await _wait_for_vehicle_ready(world_state_adapter, startup_timeout_s, require_home=True)
    return await service.run(request)


if __name__ == '__main__':
    main()
