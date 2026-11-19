'''真实有人入口的定点飞行、拍照、检测及返航交付；仅显式运行。'''
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pty
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from itertools import pairwise
from math import hypot
from pathlib import Path

import pytest
import rclpy
from agent_comm.ros_agent_srv import RosAgent
from ament_index_python.packages import get_package_prefix
from PIL import Image
from px4_msgs.msg import TrajectorySetpoint, VehicleCommand, VehicleLocalPosition
from rosllm_srvs.srv import LLM, AtomicAction, ExecuteBehavior, Observation
from test_process_round import test_temp_stop

from flight_agent.components.tracing import TraceReplay


@pytest.mark.sitl
@pytest.mark.skipif(os.environ.get('FLIGHT_M6_LIVE_API') != '1', reason='真实模型须显式启用')
@pytest.mark.parametrize('test_input_model', ['live'])
@pytest.mark.parametrize(('test_input_point_cnt', 'test_input_scene'),
                         [(1, 'target'), (2, 'target'), (1, 'empty')],
                         ids=['single', 'two_points', 'no_target'])
def test_temp_agent_visual_flight(test_input_model: str, test_input_point_cnt: int,
                                test_input_scene: str) -> None:
    '''固定 P1/P2 输入，核对逐点真实飞行、新照片及返航后的独立交付。'''
    test_temp_root = Path(__file__).resolve().parents[4]
    test_temp_stage = 'm6_t06a' if test_input_scene == 'empty' else ('m6_t05a' if test_input_point_cnt == 1 else 'm6_t05b')
    test_temp_run = test_temp_root / 'artifacts' / test_temp_stage / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    test_temp_run.mkdir(parents=True)
    print(f'Evidence: {test_temp_run}', flush=True)
    assert not any(name.strip() in {'px4', 'MicroXRCEAgent', 'parameter_bridg'} for name in
                   subprocess.check_output(['ps', '-eo', 'comm='], text=True).splitlines())
    expected_output_points = [[0, 3, -1.5], [-1, 3, -1.5]][:test_input_point_cnt]
    test_input_visits = ''.join(
        f'飞到局部NED坐标北{north}米、东{east}米、高度1.5米，接受半径{0.5 if index == 0 else 0.25}米；'
        '拍一张新照片；检测这张新照片中是否有人；'
        for index, (north, east, _) in enumerate(expected_output_points))
    test_input_human = (
        f'请在本轮按顺序且只执行{2 + 3 * test_input_point_cnt}个动作：起飞到离地1.5米；'
        + test_input_visits + 'RTL返回起飞点并自动着陆。'
        '不要添加悬停或其他动作，完成后结束本轮。'
    )
    expected_output_names = ['takeoff'] + ['goto', 'capture_photo', 'inspect_image'] * test_input_point_cnt + ['rtl']
    expected_output_arguments = [{'target_altitude_m': 1.5}]
    for test_temp_index, (north, east, _) in enumerate(expected_output_points):
        expected_output_arguments.extend([
            {'north_m': north, 'east_m': east, 'altitude_m': 1.5, 'acceptance_radius_m': 0.5 if test_temp_index == 0 else 0.25},
            {}, {'target_class': 'person'},
        ])
    expected_output_arguments.append({})
    (test_temp_run / 'test_temp_case.json').write_text(json.dumps({
        'human': test_input_human, 'names': expected_output_names,
        'arguments': expected_output_arguments, 'targets_ned_m': expected_output_points,
        'horizontal_tolerances_m': [0.5, 0.25][:test_input_point_cnt], 'vertical_tolerance_m': 0.5,
        'min_displacements_m': [2.0, 0.5][:test_input_point_cnt], 'max_sample_gap_s': 0.5,
        'scene': test_input_scene, 'min_detection_cnt': 0 if test_input_scene == 'empty' else 1,
        'max_detection_cnt': 0 if test_input_scene == 'empty' else None, 'max_home_error_m': 1.0,
    }, ensure_ascii=False, indent=2))
    test_temp_spec = importlib.util.spec_from_file_location('test_temp_sitl_helpers',
        test_temp_root / 'ros2_ws/src/flight_agent_ros/test/test_flight_sitl.py')
    test_temp_sitl = importlib.util.module_from_spec(test_temp_spec)
    test_temp_spec.loader.exec_module(test_temp_sitl)
    constraints = json.loads((test_temp_root / 'configs/flight_safety.example.json').read_text())
    constraints.update(max_altitude_m=5.0, max_mission_radius_m=10.0)
    constraints['geofence'].update(north_min_m=-10.0, north_max_m=10.0,
                                   east_min_m=-10.0, east_max_m=10.0)
    (test_temp_run / 'safety.json').write_text(json.dumps(constraints, indent=2))
    test_temp_processes: dict[str, subprocess.Popen[str]] = {}
    test_temp_commands = {}
    test_temp_messages = []
    test_temp_master, test_temp_slave = pty.openpty()
    rclpy.init()
    node = rclpy.create_node('test_temp_agent_visual_probe')
    test_temp_sitl._subscribe_messages(node, {
        '/fmu/in/vehicle_command': VehicleCommand,
        '/fmu/out/vehicle_local_position': VehicleLocalPosition,
        '/fmu/in/trajectory_setpoint': TrajectorySetpoint,
    }, test_temp_messages)
    try:
        with ExitStack() as test_temp_stack:
            for test_temp_role, test_temp_package, test_temp_entry in (
                ('simulation', '', ''), ('bridge', '', ''),
                ('flight', 'flight_agent_ros', 'flight_atomic_action_server'),
                ('capture', 'flight_agent_ros', 'capture_photo_server'),
                ('inspect', 'flight_agent_ros', 'inspect_image_server'),
                ('observation', 'observation_manager', 'observation_manager_node'),
                ('sequence', 'behavior_executor', 'sequence_executor'),
                ('llm', 'agent_comm', 'llm_node'), ('agent', 'agent_comm', 'ros_agent_srv'),
            ):
                test_temp_env = dict(os.environ, ROS_LOG_DIR=str(test_temp_run / 'test_temp_ros_logs'),
                    GZ_SIM_RESOURCE_PATH=str(test_temp_root / 'configs/m6/models') + ':' + os.environ.get('GZ_SIM_RESOURCE_PATH', ''),
                    FLIGHT_AGENT_TRACE_PATH=str(test_temp_run / (
                        'world.jsonl' if test_temp_role == 'flight' else 'observation_world.jsonl')))
                if test_temp_role != 'llm':
                    test_temp_env.pop('FLIGHT_LLM_API_KEY', None)
                if test_temp_role in {'simulation', 'bridge'}:
                    test_temp_command = [str(test_temp_root / 'scripts/ros2_px4' /
                        ('start_simulation.sh' if test_temp_role == 'simulation' else 'start_camera_bridge.sh'))]
                    if test_temp_role == 'simulation':
                        test_temp_command += ['--model', 'x500_mono_cam']
                else:
                    test_temp_command = [str(Path(get_package_prefix(test_temp_package)) /
                                            'lib' / test_temp_package / test_temp_entry)]
                    if test_temp_role == 'flight':
                        test_temp_command = [sys.executable, str(Path(__file__).with_name('test_agent_sitl.py')),
                            str(test_temp_run), *test_temp_command, '--safety-json', str(test_temp_run / 'safety.json'),
                            '--trace-path', str(test_temp_run / 'safety.jsonl')]
                    elif test_temp_role in {'capture', 'inspect'}:
                        test_temp_python = sys.executable
                        if test_temp_role == 'inspect':
                            test_temp_python = '/usr/bin/python3'
                            test_temp_env['PYTHONPATH'] = ':'.join(path for path in
                                test_temp_env.get('PYTHONPATH', '').split(':')
                                if path and not Path(path).is_relative_to(test_temp_root / '.venv'))
                        test_temp_command = [test_temp_python, str(Path(__file__).with_name('visual_process_profile.py')),
                            str(test_temp_run), test_temp_role, *test_temp_command,
                            '--output-dir' if test_temp_role == 'capture' else '--photo-root', str(test_temp_run / 'photos')]
                    elif test_temp_role == 'agent':
                        state = test_temp_sitl._wait_state(node, test_temp_run, lambda state:
                            state.get('armed') is False and state.get('landed') is True
                            and state.get('position_valid') and state.get('home_valid')
                            and state.get('health_flags', {}).get('preflight_checks_pass'))
                        test_temp_sitl._save(test_temp_run / 'test_temp_initial_state.json', state)
                        test_temp_stack.enter_context(test_temp_sitl._px4_settings(
                            test_temp_run, {'speed_limit_mps': 8.0, 'heartbeat_hz': 10}))
                        test_temp_stack.enter_context(test_temp_sitl._px4_rtl_settings(test_temp_run, {
                            'RTL_RETURN_ALT': 3.0, 'RTL_DESCEND_ALT': 2.0,
                            'RTL_LAND_DELAY': 0.0, 'RTL_TYPE': 0, 'RTL_CONE_ANG': 0}))
                        for test_input_type, test_input_service in (
                            (LLM, 'call_llm'), (Observation, 'get_observation'),
                            (ExecuteBehavior, 'execute_behavior'),
                            *((AtomicAction, name) for name in expected_output_names),
                        ):
                            client = node.create_client(test_input_type, test_input_service)
                            assert client.wait_for_service(timeout_sec=10), test_input_service
                            node.destroy_client(client)
                        test_temp_command += ['--ros-args', '-p', f'agent_trace_path:={test_temp_run / "agent.jsonl"}',
                                              '-p', f'behavior_result_dir:={test_temp_run / "behavior_results"}']
                test_temp_commands[test_temp_role] = test_temp_command
                test_temp_log = test_temp_stack.enter_context((test_temp_run / f'test_temp_{test_temp_role}.log').open('w'))
                test_temp_process = subprocess.Popen(test_temp_command, cwd=test_temp_root, env=test_temp_env,
                    stdin=test_temp_slave if test_temp_role == 'simulation' else subprocess.PIPE,
                    stdout=test_temp_log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                test_temp_processes[test_temp_role] = test_temp_process
                test_temp_stack.callback(test_temp_stop, test_temp_process)
                if test_temp_role == 'simulation':
                    test_temp_deadline = time.monotonic() + 65
                    while 'Ready for takeoff' not in (test_temp_run / 'test_temp_simulation.log').read_text(errors='replace'):
                        assert time.monotonic() < test_temp_deadline and test_temp_process.poll() is None
                        time.sleep(0.2)
                    test_temp_scene = subprocess.run([str(test_temp_root / 'scripts/ros2_px4/configure_m6_scene.sh'), test_input_scene],
                        env=test_temp_env, capture_output=True, text=True, timeout=25, check=False)
                    (test_temp_run / 'test_temp_scene.log').write_text(test_temp_scene.stdout + test_temp_scene.stderr)
                    assert test_temp_scene.returncode == 0
                    test_temp_world = subprocess.run(['gz', 'service', '-s', '/world/default/scene/info',
                        '--reqtype', 'gz.msgs.Empty', '--reptype', 'gz.msgs.Scene', '--timeout', '5000', '--req', ''],
                        env=test_temp_env, capture_output=True, text=True, timeout=10, check=False)
                    (test_temp_run / 'test_temp_scene_info.txt').write_text(test_temp_world.stdout + test_temp_world.stderr)
                    assert test_temp_world.returncode == 0 and 'name: "ground_plane"' in test_temp_world.stdout
                    if test_input_scene == 'empty':
                        assert 'm6_person' not in test_temp_world.stdout
                    else:
                        assert 'name: "m6_person"' in test_temp_world.stdout
                elif test_temp_role == 'capture':
                    client = node.create_client(AtomicAction, 'capture_photo')
                    assert client.wait_for_service(timeout_sec=10), 'capture_photo'
                    node.destroy_client(client)
            test_temp_agent = test_temp_processes['agent']
            for expected_output_prompt_cnt in (1, 2):
                test_temp_deadline = time.monotonic() + 240
                while (test_temp_run / 'test_temp_agent.log').read_text().count('任务> ') < expected_output_prompt_cnt:
                    assert time.monotonic() < test_temp_deadline and test_temp_agent.poll() is None
                    rclpy.spin_once(node, timeout_sec=0.05)
                if expected_output_prompt_cnt == 1:
                    test_temp_agent.stdin.write(test_input_human + '\n')
                    test_temp_agent.stdin.flush()
            test_temp_agent.communicate(timeout=10)
            assert test_temp_agent.returncode == 0
            replies = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_llm_response_records())
            assert len(replies) == 1 and replies[0].success
            assert json.loads(replies[0].info)['source'] == 'deepseek_api'
            actions = [json.loads(line) for line in RosAgent.extract_behavior(
                LLM.Response(response=replies[0].response)).splitlines()]
            assert [action['name'] for action in actions] == expected_output_names
            assert [json.loads(action['input']) for action in actions] == expected_output_arguments
            test_temp_terminal = (test_temp_run / 'test_temp_agent.log').read_text()
            test_temp_paths = [Path(line.split('行为结果文件：', 1)[1].strip())
                for line in test_temp_terminal.splitlines() if '行为结果文件：' in line]
            assert len(test_temp_paths) == 1
            report = json.loads(test_temp_paths[0].read_text())
            assert report['success'] and [step['name'] for step in report['steps']] == expected_output_names
            assert all(step['success'] and step['error'] is None for step in report['steps'])
            test_temp_flight_calls = [json.loads(line) for line in (test_temp_run / 'test_temp_service_calls.jsonl').read_text().splitlines()]
            # 飞行与视觉观察脚本共用文件；按回调字段区分并恢复实际顺序。
            test_temp_captures = [call for call in test_temp_flight_calls if 'name' not in call]
            test_temp_flights = [call for call in test_temp_flight_calls if 'name' in call]
            test_temp_inspections = [json.loads(line) for line in (test_temp_run / 'test_temp_inspect_calls.jsonl').read_text().splitlines()]
            assert len(test_temp_captures) == len(test_temp_inspections) == test_input_point_cnt
            test_temp_calls = [test_temp_flights[0]]
            for test_temp_index in range(test_input_point_cnt):
                test_temp_calls.extend([test_temp_flights[test_temp_index + 1],
                    test_temp_captures[test_temp_index], test_temp_inspections[test_temp_index]])
            test_temp_calls.append(test_temp_flights[-1])
            assert [call['name'] for call in test_temp_flights] == ['takeoff'] + ['goto'] * test_input_point_cnt + ['rtl']
            for test_temp_index, call in enumerate(test_temp_calls):
                assert call['success'] and call['output'] == report['steps'][test_temp_index]['output']
                assert json.loads(call['input']) == expected_output_arguments[test_temp_index]
                assert call['prev_output'] == ('' if test_temp_index == 0 else test_temp_calls[test_temp_index - 1]['output'])
            test_temp_outputs = []
            test_temp_metadata = []
            for test_temp_capture, test_temp_inspect, expected_output_point in zip(
                test_temp_captures, test_temp_inspections, expected_output_points, strict=True,
            ):
                output = json.loads(test_temp_capture['output'])
                metadata = json.loads(Path(output['metadata_path']).read_text())
                test_temp_detection = json.loads(test_temp_inspect['output'])
                assert output['error_code'] is None and test_temp_detection['error_code'] is None
                for test_temp_field in ('photo_id', 'image_path', 'sha256'):
                    assert metadata[test_temp_field] == output[test_temp_field] == test_temp_detection[test_temp_field]
                assert test_temp_detection['metadata_path'] == output['metadata_path']
                assert hashlib.sha256(Path(output['image_path']).read_bytes()).hexdigest() == output['sha256']
                assert metadata['timing']['image_received_monotonic_ns'] > metadata['timing']['request_monotonic_ns']
                position = metadata['vehicle_position']
                assert position['xy_valid'] and position['z_valid']
                assert hypot(position['x'] - expected_output_point[0], position['y'] - expected_output_point[1]) <= 0.5 and abs(position['z'] + 1.5) <= 0.5
                assert test_temp_detection['detection_cnt'] == len(test_temp_detection['detections'])
                if test_input_scene == 'empty':
                    assert test_temp_detection['detection_cnt'] == 0
                else:
                    assert test_temp_detection['detection_cnt'] > 0
                assert test_temp_inspect['output'] in test_temp_terminal
                assert output['image_path'] in test_temp_terminal
                with Image.open(output['image_path']) as image:
                    image.verify()
                test_temp_outputs.append(output)
                test_temp_metadata.append(metadata)
            for test_temp_field in ('photo_id', 'image_path', 'metadata_path', 'sha256'):
                assert len({item[test_temp_field] for item in test_temp_outputs}) == test_input_point_cnt
            for test_temp_before, test_temp_after in pairwise(test_temp_metadata):
                assert (test_temp_before['timing']['image_received_monotonic_ns']
                        < test_temp_after['timing']['request_monotonic_ns'])
            results = tuple(TraceReplay(test_temp_run / 'safety.jsonl').iter_skill_result_records())
            assert [item.command.skill_name.value for item in results] == ['takeoff'] + ['goto'] * test_input_point_cnt + ['rtl']
            assert all(item.result.status.value == 'SUCCEEDED' for item in results)
            for test_temp_index, expected_output_point in enumerate(expected_output_points):
                test_temp_point_run = test_temp_run / f'test_temp_P{test_temp_index + 1}'
                test_temp_point_run.mkdir()
                test_temp_sitl._check_goto_evidence(test_temp_point_run,
                    results[test_temp_index + 1].model_dump(mode='json'), test_temp_messages, {
                        'target_ned_m': expected_output_point,
                        'horizontal_tolerance_m': 0.5 if test_temp_index == 0 else 0.25,
                        'vertical_tolerance_m': 0.5, 'min_displacement_m': 2.0 if test_temp_index == 0 else 0.5,
                        'max_sample_gap_s': 0.5})
            state = test_temp_sitl._wait_state(node, test_temp_run, lambda state:
                state.get('landed') is True and state.get('armed') is False)
            test_temp_sitl._save(test_temp_run / 'test_temp_final_state.json', state)
            test_temp_home = json.loads((test_temp_run / 'test_temp_initial_state.json').read_text())['home_position_ned_m']
            assert hypot(state['position_ned_m']['x'] - test_temp_home['x'],
                         state['position_ned_m']['y'] - test_temp_home['y']) <= 1.0
            test_temp_states = [json.loads(line)['state'] for line in (test_temp_run / 'world.jsonl').read_text().splitlines()]
            assert any(item['flight_mode'] == 'AUTO_RTL' and item['armed'] for item in test_temp_states)
            assert not any(item['failsafe_active'] for item in test_temp_states)
            observations = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_observation_records())
            assert len(observations) == 2 and all(item.success for item in observations)
            assert observations[0].observation in replies[0].prompt
            assert json.loads(observations[-1].observation.splitlines()[-1])['landed'] is True
    finally:
        node.destroy_node()
        rclpy.shutdown()
        os.close(test_temp_master)
        os.close(test_temp_slave)
        (test_temp_run / 'test_temp_command_messages.json').write_text(json.dumps(test_temp_messages))
        (test_temp_run / 'test_temp_manifest.json').write_text(json.dumps({
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'px4': subprocess.check_output(['git', '-C', str(Path.home() / 'PX4-Autopilot'), 'rev-parse', 'HEAD'], text=True).strip(),
            'model': test_input_model, 'scene': test_input_scene, 'commands': test_temp_commands, 'substitutions': [],
            'source_sha256': {str(path.relative_to(test_temp_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [Path(__file__), *[test_temp_root / path for path in (
                    'manifests/environment.yaml', 'configs/m6/person.sdf', 'configs/m6/resources.sha256',
                    'scripts/ros2_px4/configure_m6_scene.sh',
                    'ros2_ws/src/agent_comm/agent_comm/ros_agent_srv.py',
                    'ros2_ws/src/behavior_executor/behavior_executor/sequence_executor.py',
                    'ros2_ws/src/flight_agent_ros/flight_agent_ros/capture_photo_server.py',
                    'ros2_ws/src/flight_agent_ros/flight_agent_ros/inspect_image_server.py')]]},
            'processes': {name: {'pid': item.pid, 'returncode': item.returncode} for name, item in test_temp_processes.items()},
        }, indent=2))
        (test_temp_run / 'test_temp_processes_after.txt').write_text(subprocess.check_output(['ps', '-eo', 'pid=,comm='], text=True))
