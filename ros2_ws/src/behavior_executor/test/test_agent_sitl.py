'''真实模型与 PX4/Gazebo 的两轮人工起降；仅显式运行。'''

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import pytest
import rclpy
from ament_index_python.packages import get_package_prefix
from px4_msgs.msg import VehicleCommand, VehicleCommandAck, VehicleLocalPosition
from rosllm_srvs.srv import LLM, AtomicAction, ExecuteBehavior, Observation
from test_process_round import test_temp_stop

from flight_agent.components.tracing import TraceReplay


@pytest.mark.sitl
@pytest.mark.skipif(os.environ.get('FLIGHT_M5_LIVE_API') != '1', reason='真实模型须显式启用')
@pytest.mark.parametrize('test_input_model', ['live'])
def test_temp_two_human_rounds_takeoff_land(test_input_model: str) -> None:
    '''通过独立生产入口执行两轮真实模型起降，保留飞行和模型证据。'''
    test_temp_root = Path(__file__).resolve().parents[4]
    test_temp_run = test_temp_root / 'artifacts/m5-4b-1' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    test_temp_run.mkdir(parents=True)
    test_temp_spec = importlib.util.spec_from_file_location(
        'test_temp_sitl_helpers',
        test_temp_root / 'ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py',
    )
    test_temp_sitl = importlib.util.module_from_spec(test_temp_spec)
    test_temp_spec.loader.exec_module(test_temp_sitl)
    constraints = json.loads((test_temp_root / 'configs/flight_safety.example.json').read_text())
    constraints.update(max_altitude_m=5.0, max_mission_radius_m=10.0)
    constraints['geofence'].update(
        north_min_m=-10.0, north_max_m=10.0, east_min_m=-10.0, east_max_m=10.0,
    )
    (test_temp_run / 'safety.json').write_text(json.dumps(constraints, indent=2))
    test_temp_processes: dict[str, subprocess.Popen[str]] = {}
    test_temp_commands = {}
    test_temp_messages = []
    test_input_humans = [
        '请只执行一个动作：起飞到离地3米，然后等待我的下一条指令。',
        '请只执行一个动作：立即降落，然后等待我的下一条指令。',
    ]
    rclpy.init()
    node = rclpy.create_node('test_temp_agent_sitl_probe')
    test_temp_sitl._subscribe_messages(node, {
        '/fmu/in/vehicle_command': VehicleCommand,
        '/fmu/out/vehicle_command_ack': VehicleCommandAck,
        '/fmu/out/vehicle_local_position': VehicleLocalPosition,
    }, test_temp_messages)
    try:
        with ExitStack() as test_temp_stack:
            for test_temp_role, test_temp_package, test_temp_entry in [
                ('simulation', '', ''), ('flight', 'flight_agent_ros', 'flight_atomic_action_server'),
                ('observation', 'observation_manager', 'observation_manager_node'),
                ('sequence', 'behavior_executor', 'sequence_executor'),
                ('llm', 'agent_comm', 'llm_node'), ('agent', 'agent_comm', 'ros_agent_srv'),
            ]:
                if test_temp_role == 'agent':
                    test_temp_sitl._wait_state(node, test_temp_run, lambda state:
                        state.get('armed') is False and state.get('landed') is True
                        and state.get('position_valid') and state.get('home_valid')
                        and state.get('health_flags', {}).get('preflight_checks_pass'))
                    test_temp_stack.enter_context(test_temp_sitl._px4_settings(
                        test_temp_run, {'speed_limit_mps': 8.0, 'heartbeat_hz': 10},
                    ))
                    for test_input_type, test_input_service in [
                        (LLM, 'call_llm'), (Observation, 'get_observation'),
                        (ExecuteBehavior, 'execute_behavior'),
                        (AtomicAction, 'takeoff'), (AtomicAction, 'land'),
                    ]:
                        client = node.create_client(test_input_type, test_input_service)
                        assert client.wait_for_service(timeout_sec=10), test_input_service
                        node.destroy_client(client)
                test_temp_env = os.environ.copy()
                if test_temp_role != 'llm':
                    test_temp_env.pop('FLIGHT_LLM_API_KEY', None)
                test_temp_env.update(
                    GZ_PARTITION=test_temp_run.name, ROS_LOG_DIR=str(test_temp_run / 'test_temp_ros_logs'),
                    FLIGHT_AGENT_TRACE_PATH=str(test_temp_run / (
                        'world.jsonl' if test_temp_role == 'flight' else 'observation_world.jsonl'
                    )),
                )
                if test_temp_role == 'simulation':
                    test_temp_command = [str(test_temp_root / 'scripts/ros2_px4/start_simulation.sh')]
                else:
                    test_temp_prefix = Path(get_package_prefix(test_temp_package))
                    test_temp_command = [str(test_temp_prefix / 'lib' / test_temp_package / test_temp_entry)]
                    if test_temp_role == 'flight':
                        test_temp_command += ['--safety-json', str(test_temp_run / 'safety.json'),
                                              '--trace-path', str(test_temp_run / 'safety.jsonl')]
                    elif test_temp_role == 'agent':
                        test_temp_command += ['--ros-args', '-p',
                                              f'agent_trace_path:={test_temp_run / "agent.jsonl"}']
                test_temp_commands[test_temp_role] = test_temp_command
                test_temp_log = test_temp_stack.enter_context(
                    (test_temp_run / f'test_temp_{test_temp_role}.log').open('w')
                )
                test_temp_processes[test_temp_role] = subprocess.Popen(
                    test_temp_command, env=test_temp_env, stdin=subprocess.PIPE, text=True,
                    stdout=test_temp_log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                test_temp_stack.callback(test_temp_stop, test_temp_processes[test_temp_role])
            test_temp_agent = test_temp_processes['agent']
            for test_temp_round, test_input_human in enumerate(test_input_humans, start=1):
                test_temp_wait_prompt(node, test_temp_run, test_temp_round)
                replies = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_llm_response_records())
                assert len(replies) == test_temp_round - 1
                with (test_temp_run / 'test_temp_inputs.jsonl').open('a') as test_temp_input_log:
                    test_temp_input_log.write(json.dumps({
                        'human': test_input_human, 'at': datetime.now(UTC).isoformat(),
                    }, ensure_ascii=False) + '\n')
                test_temp_agent.stdin.write(test_input_human + '\n')
                test_temp_agent.stdin.flush()
                test_temp_wait_prompt(node, test_temp_run, test_temp_round + 1)
                replies = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_llm_response_records())
                results = tuple(TraceReplay(test_temp_run / 'safety.jsonl').iter_skill_result_records())
                assert len(replies) == len(results) == test_temp_round
                assert all(item.success for item in replies)
                assert [item.command.skill_name.value for item in results] == ['takeoff', 'land'][:test_temp_round]
                assert all(item.result.status.value == 'SUCCEEDED' for item in results)
                if test_temp_round == 1:
                    test_temp_sitl._wait_state(node, test_temp_run, lambda state:
                        state.get('flight_mode') == 'AUTO_LOITER' and state.get('armed') is True
                        and state.get('landed') is False and state.get('position_ned_m')
                        and abs(state['position_ned_m']['z'] + 3.0) <= 0.5)
                    test_temp_sitl._spin_for(node, 1.0)
                    assert len(tuple(TraceReplay(test_temp_run / 'agent.jsonl')
                                     .iter_llm_response_records())) == 1
            test_temp_agent.communicate(timeout=10)
            assert test_temp_agent.returncode == 0
            observations = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_observation_records())
            assert len(observations) == 3 and all(item.success for item in observations)
            assert all(item.observation in reply.prompt for item, reply in zip(observations, replies))
            state = test_temp_sitl._latest_state(test_temp_run)
            assert state['landed'] is True and state['armed'] is False
            assert not state['failsafe_active']
            assert any(item['topic'] == '/fmu/out/vehicle_command_ack' for item in test_temp_messages)
    finally:
        node.destroy_node()
        rclpy.shutdown()
        (test_temp_run / 'test_temp_command_messages.json').write_text(json.dumps(test_temp_messages))
        (test_temp_run / 'test_temp_manifest.json').write_text(json.dumps({
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'px4': subprocess.check_output(['git', '-C', str(Path.home() / 'PX4-Autopilot'),
                                           'rev-parse', 'HEAD'], text=True).strip(),
            'model': test_input_model, 'commands': test_temp_commands,
            'processes': {role: {'pid': item.pid, 'returncode': item.returncode}
                          for role, item in test_temp_processes.items()},
        }, indent=2))
        print(f'Evidence: {test_temp_run}', flush=True)


def test_temp_wait_prompt(node: object, test_temp_run: Path, expected_output_prompt_cnt: int) -> None:
    '''处理原始消息并等待实际终端提示，超时不重发人工输入或模型请求。'''
    test_temp_deadline = monotonic() + 90
    while monotonic() < test_temp_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        if (test_temp_run / 'test_temp_agent.log').read_text().count('任务> ') >= expected_output_prompt_cnt:
            return
    pytest.fail(f'等待人工提示超时：{test_temp_run}')


test_temp_wait_prompt.__test__ = False
