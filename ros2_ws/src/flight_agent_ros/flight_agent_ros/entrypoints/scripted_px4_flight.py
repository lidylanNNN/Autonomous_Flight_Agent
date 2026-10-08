'''Run the M3 scripted flight through the real PX4/ROS 2 execution path.'''

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from threading import Thread
from uuid import uuid4

import rclpy
from px4_msgs.msg import VehicleCommand
from rclpy.executors import SingleThreadedExecutor

from flight_agent.contracts import (
    ApprovedSkillCommand,
    FlightExecutionBackendProtocol,
    GoToArgs,
    HoldArgs,
    RTLArgs,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    TakeoffArgs,
    WorldState,
)
from flight_agent_ros.adapters.flight_execution_backend import (
    Px4Ros2FlightExecutionBackend,
)
from flight_agent_ros.adapters.px4_command_plan_executor import Px4CommandPlanExecutor
from flight_agent_ros.adapters.px4_offboard_setpoint_adapter import (
    Px4OffboardSetpointAdapter,
)
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    Px4CommandAckStatus,
    Px4VehicleCommandAdapter,
    VehicleCommandParameters,
)
from flight_agent_ros.nodes.world_state_node import WorldStateNode


@dataclass(frozen=True)
class ScriptedFlightParameters:
    '''M3 固定飞行脚本的可调参数。'''

    takeoff_altitude_m: float = 10.0
    goto_north_m: float = 10.0
    goto_east_m: float = 5.0
    goto_altitude_m: float = 10.0
    acceptance_radius_m: float = 1.0
    hold_duration_s: float = 3.0
    skill_timeout_s: float = 60.0


def main() -> None:
    '''创建真实 ROS 对象，运行一次 M3 脚本并完成清理。'''

    parameters, startup_timeout_s, set_home_to_current = _parse_arguments()
    rclpy.init()
    world_state_node = WorldStateNode()
    command_adapter = Px4VehicleCommandAdapter()
    offboard_adapter = Px4OffboardSetpointAdapter()
    executor = SingleThreadedExecutor()
    nodes = (world_state_node, command_adapter, offboard_adapter)
    for node in nodes:
        executor.add_node(node)
    ros_thread = Thread(target=executor.spin, name='flight-agent-ros', daemon=True)
    ros_thread.start()

    backend = Px4Ros2FlightExecutionBackend(
        state_reader=world_state_node.read_world_state,
        plan_executor=Px4CommandPlanExecutor(command_adapter),
        offboard_adapter=offboard_adapter,
    )
    exit_code = 0
    try:
        results = asyncio.run(
            _run_when_vehicle_ready(
                backend,
                world_state_node,
                command_adapter,
                parameters,
                startup_timeout_s=startup_timeout_s,
                set_home_to_current=set_home_to_current,
            )
        )
        if not results or results[-1].status is not SkillExecutionStatus.SUCCEEDED:
            exit_code = 1
    except (KeyboardInterrupt, RuntimeError, TimeoutError, ValueError) as error:
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


def _parse_arguments() -> tuple[ScriptedFlightParameters, float, bool]:
    '''解析 M3 脚本命令行参数。'''

    parser = argparse.ArgumentParser(description='Run the M3 PX4 scripted flight')
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
    arguments = parser.parse_args()
    parameters = ScriptedFlightParameters(
        takeoff_altitude_m=arguments.takeoff_altitude_m,
        goto_north_m=arguments.goto_north_m,
        goto_east_m=arguments.goto_east_m,
        goto_altitude_m=arguments.goto_altitude_m,
        acceptance_radius_m=arguments.acceptance_radius_m,
        hold_duration_s=arguments.hold_duration_s,
        skill_timeout_s=arguments.skill_timeout_s,
    )
    _script_steps(parameters)
    return parameters, arguments.startup_timeout_s, arguments.set_home_to_current


async def _run_when_vehicle_ready(
    backend: FlightExecutionBackendProtocol,
    world_state_node: WorldStateNode,
    command_adapter: Px4VehicleCommandAdapter,
    parameters: ScriptedFlightParameters,
    *,
    startup_timeout_s: float,
    set_home_to_current: bool,
) -> list[SkillResult]:
    '''等待 PX4 状态满足脚本启动条件后运行任务。'''

    state = await _wait_for_vehicle_ready(
        world_state_node, startup_timeout_s, require_home=False
    )
    if not state.home_valid and set_home_to_current:
        _print_json({'type': 'home_sync_started', 'state_id': state.state_id})
        ack = await command_adapter.submit_and_wait(
            execution_id=f'm3-home-sync-{uuid4().hex}',
            command_id=VehicleCommand.VEHICLE_CMD_DO_SET_HOME,
            timeout_s=min(5.0, startup_timeout_s),
            parameters=VehicleCommandParameters(param1=1.0),
        )
        _print_json({'type': 'home_sync_ack', 'status': ack.status})
        if ack.status is not Px4CommandAckStatus.ACCEPTED:
            raise RuntimeError(ack.failure_code or 'PX4_HOME_SYNC_FAILED')
    await _wait_for_vehicle_ready(world_state_node, startup_timeout_s, require_home=True)
    return await run_scripted_px4_flight(backend, parameters)


async def _wait_for_vehicle_ready(
    world_state_node: WorldStateNode,
    timeout_s: float,
    *,
    require_home: bool,
) -> WorldState:
    '''等待新鲜、已连接且具备 Home 和局部位置的 PX4 状态。'''

    if timeout_s <= 0.0:
        raise ValueError('startup timeout must be positive')
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    latest_state: WorldState | None = None
    while loop.time() < deadline:
        if world_state_node.ready:
            latest_state = world_state_node.read_world_state()
            if _vehicle_is_ready(latest_state, require_home=require_home):
                return latest_state
        await asyncio.sleep(0.1)
    detail = latest_state.model_dump(mode='json') if latest_state is not None else None
    raise TimeoutError(f'PX4_STARTUP_TIMEOUT: latest_state={detail}')


def _vehicle_is_ready(state: WorldState, *, require_home: bool) -> bool:
    '''判断 M3 脚本所需的最小 PX4 状态是否齐备。'''

    return bool(
        state.is_fresh(1_000)
        and state.link_healthy
        and state.position_valid
        and state.position_ned_m is not None
        and (
            not require_home
            or (state.home_valid and state.home_position_wgs84 is not None)
        )
        and state.landed is not None
        and not state.failsafe_active
    )


async def run_scripted_px4_flight(
    backend: FlightExecutionBackendProtocol,
    parameters: ScriptedFlightParameters,
) -> list[SkillResult]:
    '''依次执行 Takeoff、GoTo、Hold 和 RTL，并在首次失败时停止。'''

    run_id = uuid4().hex
    results: list[SkillResult] = []
    for sequence, (skill_name, arguments) in enumerate(
        _script_steps(parameters), start=1
    ):
        state = await backend.get_world_state()
        _print_json(
            {
                'type': 'script_step_started',
                'sequence': sequence,
                'skill_name': skill_name,
                'state_id': state.state_id,
            }
        )
        command = ApprovedSkillCommand(
            execution_id=f'm3-{run_id}-{sequence}-{skill_name}',
            proposal_id=f'm3-scripted-proposal-{run_id}',
            decision_id=f'm3-scripted-fixture-{run_id}',
            skill_name=skill_name,
            arguments=arguments,
            timeout_s=parameters.skill_timeout_s,
            approved_state_id=state.state_id,
        )
        result = await backend.execute(command)
        results.append(result)
        _print_json({'type': 'skill_result', **result.model_dump(mode='json')})
        if result.status is not SkillExecutionStatus.SUCCEEDED:
            break
    return results


def _script_steps(
    parameters: ScriptedFlightParameters,
) -> tuple[
    tuple[SkillName, TakeoffArgs | GoToArgs | HoldArgs | RTLArgs], ...
]:
    '''构造固定脚本的强类型 Skill 参数。'''

    return (
        (
            SkillName.TAKEOFF,
            TakeoffArgs(target_altitude_m=parameters.takeoff_altitude_m),
        ),
        (
            SkillName.GOTO,
            GoToArgs(
                north_m=parameters.goto_north_m,
                east_m=parameters.goto_east_m,
                altitude_m=parameters.goto_altitude_m,
                acceptance_radius_m=parameters.acceptance_radius_m,
            ),
        ),
        (SkillName.HOLD, HoldArgs(duration_s=parameters.hold_duration_s)),
        (SkillName.RTL, RTLArgs()),
    )


def _print_json(payload: dict[str, object]) -> None:
    '''立即输出一条可供人和脚本读取的运行事件。'''

    print(json.dumps(payload, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
