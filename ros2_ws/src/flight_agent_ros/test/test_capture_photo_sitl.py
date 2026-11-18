'''从正式入口运行地面拍照；需要真实 PX4/Gazebo 和已构建 ROS 工作区。'''
from __future__ import annotations

import hashlib
import json
import os
import pty
import signal
import subprocess
import time
from collections import deque
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Thread

import pytest
import rclpy
from flight_agent_ros.capture_photo_server import stamp_ns
from PIL import Image as PngImage
from px4_msgs.msg import VehicleLocalPosition
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from rclpy.serialization import serialize_message
from rosidl_runtime_py.convert import message_to_ordereddict
from rosllm_srvs.srv import AtomicAction
from sensor_msgs.msg import CameraInfo, Image

pytestmark = pytest.mark.sitl


def test_temp_capture_sitl(monkeypatch: pytest.MonkeyPatch) -> None:
    '''真实入口拍两张，核对来源与文件，再检查暂停后正式服务拒绝旧缓存。'''
    test_temp_root = Path(__file__).resolve().parents[4]
    test_temp_dir = test_temp_root/'artifacts/m6_t03b'/datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    test_temp_dir.mkdir(parents=True)
    print(f'Artifacts: {test_temp_dir}', flush=True)
    test_temp_names = subprocess.check_output(['ps', '-eo', 'comm='], text=True).splitlines()
    assert not any(test_temp_name.strip() in {'px4', 'MicroXRCEAgent', 'parameter_bridg'}
                   for test_temp_name in test_temp_names), 'Existing runtime'
    test_temp_env = dict(os.environ, GZ_SIM_RESOURCE_PATH=str(test_temp_root/'configs/m6/models')+':'+os.environ.get('GZ_SIM_RESOURCE_PATH', ''),
                         ROS_LOG_DIR=str(test_temp_dir/'ros_logs'))
    monkeypatch.setenv('ROS_LOG_DIR', test_temp_env['ROS_LOG_DIR'])
    test_temp_processes, test_temp_logs = [], []
    test_temp_master, test_temp_slave = pty.openpty()
    test_temp_node = test_temp_executor = test_temp_thread = None
    test_temp_paused = False
    test_temp_control = ['gz', 'service', '-s', '/world/default/control', '--reqtype',
                         'gz.msgs.WorldControl', '--reptype', 'gz.msgs.Boolean', '--timeout', '5000', '--req']
    try:
        for test_temp_name, test_temp_command in (
            ('simulation', [str(test_temp_root/'scripts/ros2_px4/start_simulation.sh'), '--model', 'x500_mono_cam']),
            ('bridge', [str(test_temp_root/'scripts/ros2_px4/start_camera_bridge.sh')]),
            ('capture', ['ros2', 'run', 'flight_agent_ros', 'capture_photo_server', '--output-dir', str(test_temp_dir/'photos')]),
        ):
            test_temp_log = (test_temp_dir/f'{test_temp_name}.log').open('w')
            test_temp_logs.append(test_temp_log)
            test_temp_processes.append(subprocess.Popen(test_temp_command, cwd=test_temp_root, env=test_temp_env,
                stdin=test_temp_slave if test_temp_name == 'simulation' else subprocess.DEVNULL,
                stdout=test_temp_log, stderr=subprocess.STDOUT, start_new_session=True))
            if test_temp_name == 'simulation':
                test_temp_started = time.monotonic()
                while time.monotonic() - test_temp_started < 65:
                    test_temp_text = (test_temp_dir/'simulation.log').read_text(errors='replace')
                    if 'Ready for takeoff' in test_temp_text:
                        break
                    assert test_temp_processes[0].poll() is None, 'Simulation exited'
                    time.sleep(0.5)
                else:
                    raise AssertionError('Simulation readiness timeout')
                assert 'gazebo already running' not in test_temp_text
                test_temp_scene = subprocess.run([str(test_temp_root/'scripts/ros2_px4/configure_m6_scene.sh'), 'target'],
                    env=test_temp_env, capture_output=True, text=True, timeout=25, check=False)
                (test_temp_dir/'test_temp_scene.log').write_text(test_temp_scene.stdout+test_temp_scene.stderr)
                assert test_temp_scene.returncode == 0
        rclpy.init()
        test_temp_node = rclpy.create_node('test_temp_capture_verifier')
        test_temp_images, test_temp_infos, test_temp_positions = deque(maxlen=30), deque(maxlen=120), deque(maxlen=200)

        def test_temp_record(test_input_buffer: deque, test_input_message: object) -> None:
            '''用可检查签名的 Python 回调保存真实消息。'''
            test_input_buffer.append(test_input_message)

        test_temp_subscriptions = [test_temp_node.create_subscription(test_temp_type, test_temp_topic,
            partial(test_temp_record, test_temp_buffer), qos_profile_sensor_data) for test_temp_type, test_temp_topic, test_temp_buffer in (
                (Image, '/camera/image_raw', test_temp_images), (CameraInfo, '/camera/camera_info', test_temp_infos),
                (VehicleLocalPosition, '/fmu/out/vehicle_local_position', test_temp_positions))]
        assert len(test_temp_subscriptions) == 3
        test_temp_client = test_temp_node.create_client(AtomicAction, 'capture_photo')
        test_temp_executor = MultiThreadedExecutor(num_threads=2)
        test_temp_executor.add_node(test_temp_node)
        test_temp_thread = Thread(target=test_temp_executor.spin, daemon=True)
        test_temp_thread.start()
        assert test_temp_client.wait_for_service(timeout_sec=10)
        test_temp_started = time.monotonic()
        while not (test_temp_images and test_temp_infos and test_temp_positions and
                   test_temp_positions[-1].xy_valid and test_temp_positions[-1].z_valid):
            assert time.monotonic() - test_temp_started < 30, 'No valid camera/position baseline'
            assert all(test_temp_process.poll() is None for test_temp_process in test_temp_processes)
            time.sleep(0.05)
        test_temp_results = []
        for test_temp_index in range(2):
            response = test_temp_client.call(AtomicAction.Request(input='{}', prev_output=''), timeout_sec=8)
            assert response is not None and response.success, response
            output = json.loads(response.output)
            metadata = json.loads(Path(output['metadata_path']).read_text())
            test_temp_matches = [image for image in list(test_temp_images) if stamp_ns(image) == metadata['image']['stamp_ns']]
            test_temp_info_matches = [info for info in list(test_temp_infos) if stamp_ns(info) == metadata['image']['stamp_ns']]
            test_temp_position_matches = [position for position in list(test_temp_positions)
                                         if position.timestamp == metadata['vehicle_position']['timestamp']]
            assert test_temp_matches and test_temp_info_matches and test_temp_position_matches
            image, info, position = test_temp_matches[-1], test_temp_info_matches[-1], test_temp_position_matches[-1]
            with PngImage.open(output['image_path']) as test_temp_saved:
                assert test_temp_saved.size == (1280, 960) and test_temp_saved.tobytes() == bytes(image.data)
            assert output['sha256'] == metadata['sha256'] == hashlib.sha256(Path(output['image_path']).read_bytes()).hexdigest()
            assert metadata['camera_info'] == {'source_topic': '/camera/camera_info', **message_to_ordereddict(info)}
            assert all(metadata['vehicle_position'][test_temp_key] == getattr(position, test_temp_key)
                       for test_temp_key in ('timestamp_sample', 'x', 'y', 'z', 'xy_valid', 'z_valid'))
            assert metadata['timing']['image_received_monotonic_ns'] > metadata['timing']['request_monotonic_ns']
            for test_temp_name, test_temp_message in [('image', image), ('info', info), ('position', position)]:
                (test_temp_dir/f'test_temp_{test_temp_index}_{test_temp_name}.cdr').write_bytes(serialize_message(test_temp_message))
            test_temp_results.append({'output': output, 'image_stamp_ns': stamp_ns(image), 'timing': metadata['timing']})
        assert test_temp_results[0]['output']['photo_id'] != test_temp_results[1]['output']['photo_id']
        assert test_temp_results[1]['image_stamp_ns'] > test_temp_results[0]['image_stamp_ns']
        assert hashlib.sha256(Path(test_temp_results[0]['output']['image_path']).read_bytes()).hexdigest() == test_temp_results[0]['output']['sha256']
        test_temp_pause = subprocess.run([*test_temp_control, 'pause: true'], capture_output=True, text=True, timeout=8, check=False)
        test_temp_paused = True
        assert test_temp_pause.returncode == 0 and 'true' in test_temp_pause.stdout
        time.sleep(1.3)
        response = test_temp_client.call(AtomicAction.Request(input='{}'), timeout_sec=7)
        assert response is not None and not response.success
        output = json.loads(response.output)
        assert output['error_code'] == 'CLOCK_UNAVAILABLE' and output['image_path'] is None
        assert len(list((test_temp_dir/'photos').iterdir())) == 2
        (test_temp_dir/'test_temp_results.json').write_text(json.dumps({'photos': test_temp_results,
            'paused_response': output, 'scope': 'Actual ROS entrypoint, Gazebo/PX4/bridge; no stubs, flight, Agent or detector'}, indent=2))
    finally:
        test_temp_resume_ok = True
        if test_temp_paused:
            try:
                test_temp_resume = subprocess.run([*test_temp_control, 'pause: false'], capture_output=True,
                                                  text=True, timeout=8, check=False)
                test_temp_resume_ok = test_temp_resume.returncode == 0 and 'true' in test_temp_resume.stdout
                (test_temp_dir/'test_temp_resume.log').write_text(test_temp_resume.stdout+test_temp_resume.stderr)
            except (OSError, subprocess.SubprocessError) as test_temp_error:
                test_temp_resume_ok = False
                (test_temp_dir/'test_temp_resume.log').write_text(str(test_temp_error))
        if test_temp_executor is not None:
            test_temp_executor.shutdown(timeout_sec=5)
        if test_temp_thread is not None:
            test_temp_thread.join(timeout=2)
        if test_temp_node is not None:
            test_temp_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        for test_temp_process in reversed(test_temp_processes):
            for test_temp_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(test_temp_process.pid, test_temp_signal)
                except ProcessLookupError:
                    break
                time.sleep(1)
            test_temp_process.wait(timeout=5)
        os.close(test_temp_master)
        os.close(test_temp_slave)
        for test_temp_log in test_temp_logs:
            test_temp_log.close()
        (test_temp_dir/'test_temp_processes_after.txt').write_text(subprocess.check_output(['ps', '-eo', 'pid=,comm='], text=True))
        assert test_temp_resume_ok, 'World resume failed; runtime was stopped'
