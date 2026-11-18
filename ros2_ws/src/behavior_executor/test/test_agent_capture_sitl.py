'''实际有人入口经真实模型拍照或拍照后检测；显式运行，飞机保持在地面。'''
from __future__ import annotations

import hashlib
import json
import os
import pty
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import pytest
import rclpy
from agent_comm.ros_agent_srv import RosAgent
from ament_index_python.packages import get_package_prefix
from PIL import Image
from rosllm_srvs.srv import LLM, AtomicAction, ExecuteBehavior, Observation
from test_agent_sitl import test_temp_wait_prompt
from test_process_round import test_temp_stop

from flight_agent.components.tracing import TraceReplay


@pytest.mark.sitl
@pytest.mark.skipif(os.environ.get('FLIGHT_M6_LIVE_API') != '1', reason='真实模型须显式启用')
@pytest.mark.parametrize('test_input_model', ['live'])
@pytest.mark.parametrize('test_input_task', ['capture', 'inspect'])
def test_temp_agent_capture(test_input_model: str, test_input_task: str) -> None:
    '''运行成功和暂停后失败两轮，核对模型选择、真实服务返回及保存引用。'''
    test_temp_root = Path(__file__).resolve().parents[4]
    test_temp_stage = 'm6_t04c' if test_input_task == 'inspect' else 'm6_t03c'
    test_temp_run = test_temp_root / 'artifacts' / test_temp_stage / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    test_temp_run.mkdir(parents=True)
    print(f'Evidence: {test_temp_run}', flush=True)
    assert not any(name.strip() in {'px4', 'MicroXRCEAgent', 'parameter_bridg'} for name in
                   subprocess.check_output(['ps', '-eo', 'comm='], text=True).splitlines())
    test_temp_processes: dict[str, subprocess.Popen[str]] = {}
    test_temp_commands = {}
    test_temp_master, test_temp_slave = pty.openpty()
    test_temp_control = ['gz', 'service', '-s', '/world/default/control', '--reqtype',
                         'gz.msgs.WorldControl', '--reptype', 'gz.msgs.Boolean', '--timeout', '5000', '--req']
    test_temp_paused = False
    rclpy.init()
    node = rclpy.create_node('test_temp_agent_capture_probe')
    try:
        with ExitStack() as test_temp_stack:
            for test_temp_role, test_temp_package, test_temp_entry in (
                ('simulation', '', ''), ('bridge', '', ''),
                ('capture', 'flight_agent_ros', 'capture_photo_server'),
                *((('inspect', 'flight_agent_ros', 'inspect_image_server'),)
                  if test_input_task == 'inspect' else ()),
                ('observation', 'observation_manager', 'observation_manager_node'),
                ('sequence', 'behavior_executor', 'sequence_executor'),
                ('llm', 'agent_comm', 'llm_node'), ('agent', 'agent_comm', 'ros_agent_srv'),
            ):
                test_temp_env = dict(os.environ, ROS_LOG_DIR=str(test_temp_run / 'ros_logs'),
                    GZ_SIM_RESOURCE_PATH=str(test_temp_root / 'configs/m6/models') + ':' + os.environ.get('GZ_SIM_RESOURCE_PATH', ''),
                    FLIGHT_AGENT_TRACE_PATH=str(test_temp_run / 'world.jsonl'))
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
                    if test_temp_role in {'capture', 'inspect'}:
                        test_temp_option = '--output-dir' if test_temp_role == 'capture' else '--photo-root'
                        test_temp_python = sys.executable
                        if test_temp_role == 'inspect':
                            test_temp_python = '/usr/bin/python3'
                            test_temp_env['PYTHONPATH'] = ':'.join(
                                path for path in test_temp_env.get('PYTHONPATH', '').split(':')
                                if path and not Path(path).is_relative_to(test_temp_root / '.venv')
                            )
                        test_temp_command = [test_temp_python, str(Path(__file__).with_name('visual_process_profile.py')),
                            str(test_temp_run), test_temp_role, *test_temp_command,
                            test_temp_option, str(test_temp_run / 'photos')]
                    if test_temp_role == 'agent':
                        test_temp_command += ['--ros-args', '-p', f'agent_trace_path:={test_temp_run / "agent.jsonl"}']
                        for test_input_type, test_input_service in (
                            (LLM, 'call_llm'), (Observation, 'get_observation'),
                            (ExecuteBehavior, 'execute_behavior'), (AtomicAction, 'capture_photo'),
                        ):
                            client = node.create_client(test_input_type, test_input_service)
                            assert client.wait_for_service(timeout_sec=10), test_input_service
                            node.destroy_client(client)
                        if test_input_task == 'inspect':
                            client = node.create_client(AtomicAction, 'inspect_image')
                            assert client.wait_for_service(timeout_sec=10), 'inspect_image'
                            node.destroy_client(client)
                        client = node.create_client(Observation, 'get_observation')
                        test_temp_deadline = time.monotonic() + 30
                        while True:
                            test_temp_future = client.call_async(Observation.Request())
                            rclpy.spin_until_future_complete(node, test_temp_future, timeout_sec=3)
                            response = test_temp_future.result() if test_temp_future.done() else None
                            if response is not None and response.success and json.loads(
                                response.observation.splitlines()[-1],
                            )['position_valid']:
                                break
                            assert time.monotonic() < test_temp_deadline, 'No valid initial observation'
                            time.sleep(0.1)
                        node.destroy_client(client)
                test_temp_commands[test_temp_role] = test_temp_command
                test_temp_log = test_temp_stack.enter_context((test_temp_run / f'test_temp_{test_temp_role}.log').open('w'))
                test_temp_process = subprocess.Popen(test_temp_command, cwd=test_temp_root, env=test_temp_env,
                    stdin=test_temp_slave if test_temp_role == 'simulation' else subprocess.PIPE,
                    stdout=test_temp_log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                test_temp_processes[test_temp_role] = test_temp_process
                test_temp_stack.callback(test_temp_stop, test_temp_process)
                if test_temp_role == 'capture':
                    client = node.create_client(AtomicAction, 'capture_photo')
                    assert client.wait_for_service(timeout_sec=10), 'capture_photo'
                    node.destroy_client(client)
                if test_temp_role == 'simulation':
                    test_temp_deadline = time.monotonic() + 65
                    while 'Ready for takeoff' not in (test_temp_run / 'test_temp_simulation.log').read_text(errors='replace'):
                        assert time.monotonic() < test_temp_deadline and test_temp_process.poll() is None
                        time.sleep(0.2)
                    test_temp_scene = subprocess.run([str(test_temp_root / 'scripts/ros2_px4/configure_m6_scene.sh'), 'target'],
                        env=test_temp_env, capture_output=True, text=True, timeout=25, check=False)
                    (test_temp_run / 'test_temp_scene.log').write_text(test_temp_scene.stdout + test_temp_scene.stderr)
                    assert test_temp_scene.returncode == 0
            test_temp_agent = test_temp_processes['agent']
            for test_temp_round in (1, 2):
                test_temp_wait_prompt(node, test_temp_run, test_temp_round)
                if test_temp_round == 2:
                    test_temp_paused = True
                    test_temp_pause = subprocess.run([*test_temp_control, 'pause: true'], capture_output=True, text=True, timeout=8, check=False)
                    assert test_temp_pause.returncode == 0 and 'true' in test_temp_pause.stdout
                    time.sleep(1.3)
                test_input_human = '请只拍一张新照片，除此之外不执行其他动作，完成后等待我的下一条指令。'
                if test_input_task == 'inspect':
                    test_input_human = ('请在本轮按顺序且只执行两个动作：拍一张新照片；检测这张新照片中是否有人。'
                                        '不执行起飞、移动、悬停、返航或降落。两个动作完成后结束本轮。')
                test_temp_agent.stdin.write(test_input_human + '\n')
                test_temp_agent.stdin.flush()
                test_temp_wait_prompt(node, test_temp_run, test_temp_round + 1)
                replies = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_llm_response_records())
                assert len(replies) == test_temp_round and replies[-1].success
                assert json.loads(replies[-1].info)['source'] == 'deepseek_api'
                actions = [json.loads(line) for line in RosAgent.extract_behavior(
                    LLM.Response(response=replies[-1].response)).splitlines()]
                expected_output_names = ['capture_photo', 'inspect_image'] if test_input_task == 'inspect' else ['capture_photo']
                assert [action['name'] for action in actions] == expected_output_names
                assert json.loads(actions[0]['input']) == {}
                if test_input_task == 'inspect':
                    assert json.loads(actions[1]['input']) == {'target_class': 'person'}
                assert f'HumanInput: {test_input_human}' in replies[-1].prompt
                test_temp_calls = [json.loads(line) for line in (test_temp_run / 'test_temp_service_calls.jsonl').read_text().splitlines()]
                assert len(test_temp_calls) == test_temp_round
                assert test_temp_calls[-1]['prev_output'] == ''
                assert json.loads(test_temp_calls[-1]['input']) == {}
                response = test_temp_calls[-1]
                output = json.loads(response['output'])
                if test_input_task == 'inspect':
                    test_temp_inspections = [json.loads(line) for line in
                        (test_temp_run / 'test_temp_inspect_calls.jsonl').read_text().splitlines()]
                    assert len(test_temp_inspections) == 1
                    if test_temp_round == 1:
                        assert test_temp_inspections[0]['prev_output'] == response['output']
                        assert json.loads(test_temp_inspections[0]['input']) == {'target_class': 'person'}
                        assert test_temp_inspections[0]['success']
                        test_temp_detection = json.loads(test_temp_inspections[0]['output'])
                        assert test_temp_detection['source'] == 'inspect_image_server'
                        assert test_temp_detection['target_class'] == 'person'
                        assert test_temp_detection['error_code'] is None
                        assert test_temp_detection['detection_cnt'] == len(test_temp_detection['detections']) > 0
                        for test_temp_field in ('photo_id', 'image_path', 'metadata_path', 'sha256'):
                            assert test_temp_detection[test_temp_field] == output[test_temp_field]
                if test_temp_round == 1:
                    assert response['success'] and output['error_code'] is None
                    metadata = json.loads(Path(output['metadata_path']).read_text())
                    assert metadata['photo_id'] == output['photo_id']
                    assert metadata['image_path'] == output['image_path']
                    assert metadata['sha256'] == output['sha256'] == hashlib.sha256(Path(output['image_path']).read_bytes()).hexdigest()
                    assert metadata['timing']['image_received_monotonic_ns'] > metadata['timing']['request_monotonic_ns']
                    with Image.open(output['image_path']) as image:
                        image.verify()
                    assert '动作序列执行成功' in (test_temp_run / 'test_temp_agent.log').read_text()
                else:
                    assert not response['success'] and output['error_code'] == 'CLOCK_UNAVAILABLE'
                    assert output['image_path'] is None and output['metadata_path'] is None
                    assert 'ATOMIC_ACTION_FAILED name=capture_photo' in (test_temp_run / 'test_temp_agent.log').read_text()
                    assert 'CLOCK_UNAVAILABLE' in (test_temp_run / 'test_temp_agent.log').read_text()
            test_temp_agent.communicate(timeout=10)
            assert test_temp_agent.returncode == 0
            observations = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_observation_records())
            assert len(observations) == 3 and all(item.success for item in observations)
            assert all(json.loads(item.observation.splitlines()[-1])['armed'] is False for item in observations)
            assert all(item.observation in reply.prompt for item, reply in zip(observations, replies))
            assert len(list((test_temp_run / 'photos').iterdir())) == 1
            test_temp_resume = subprocess.run([*test_temp_control, 'pause: false'], capture_output=True, text=True, timeout=8, check=False)
            assert test_temp_resume.returncode == 0 and 'true' in test_temp_resume.stdout
            test_temp_paused = False
    finally:
        if test_temp_paused:
            subprocess.run([*test_temp_control, 'pause: false'], capture_output=True, timeout=8, check=False)
        node.destroy_node()
        rclpy.shutdown()
        os.close(test_temp_master)
        os.close(test_temp_slave)
        (test_temp_run / 'test_temp_manifest.json').write_text(json.dumps({
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'model': test_input_model, 'commands': test_temp_commands, 'substitutions': [],
            'task': test_input_task,
            'source_sha256': {str(path.relative_to(test_temp_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [Path(__file__), Path(__file__).with_name('visual_process_profile.py'),
                             test_temp_root / 'ros2_ws/src/agent_comm/agent_comm/ros_agent_srv.py',
                             test_temp_root / 'ros2_ws/src/agent_comm/config/atomic_action_library.yaml',
                             test_temp_root / 'src/flight_agent/components/skills/registry.py',
                             test_temp_root / 'ros2_ws/src/behavior_executor/behavior_executor/sequence_executor.py',
                             test_temp_root / 'ros2_ws/src/flight_agent_ros/flight_agent_ros/capture_photo_server.py',
                             test_temp_root / 'ros2_ws/src/flight_agent_ros/flight_agent_ros/inspect_image_server.py',
                             test_temp_root / 'manifests/environment.yaml']},
            'processes': {name: {'pid': item.pid, 'returncode': item.returncode} for name, item in test_temp_processes.items()},
        }, indent=2))
        (test_temp_run / 'test_temp_processes_after.txt').write_text(subprocess.check_output(['ps', '-eo', 'pid=,comm='], text=True))
