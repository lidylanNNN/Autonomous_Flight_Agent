'''通过已安装 ROS 入口检测既有正式拍照产物，不启动仿真或 Agent。'''
import json
import os
import signal
import subprocess
from pathlib import Path
from threading import Thread

import pytest
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rosllm_srvs.srv import AtomicAction


def test_temp_installed_entrypoint(tmp_path: Path) -> None:
    '''真实服务和 HOG 消费 T03b 图片，保存请求、响应及进程日志。'''
    test_temp_root = Path(__file__).resolve().parents[4]
    test_input_root = test_temp_root/'artifacts/m6_t03b/20261118T042848Z/photos'
    metadata_path = test_input_root/'7584737f-2dea-4915-8aa3-5e578aa96330/metadata.json'
    if not metadata_path.exists():
        pytest.skip('需要本地 T03b 正式拍照证据')
    metadata = json.loads(metadata_path.read_text())
    reference = {test_temp_key: metadata[test_temp_key] for test_temp_key in
                 ('photo_id', 'image_path', 'sha256')}
    reference.update(source='capture_photo_server', error_code=None,
                     metadata_path=str(metadata_path))
    rclpy.init()
    test_temp_node = rclpy.create_node('test_temp_inspect_client')
    test_temp_executor = SingleThreadedExecutor()
    test_temp_executor.add_node(test_temp_node)
    test_temp_thread = Thread(target=test_temp_executor.spin, daemon=True)
    test_temp_thread.start()
    with (tmp_path/'test_temp_inspect.log').open('w') as test_temp_log:
        test_temp_process = subprocess.Popen(
            ['ros2', 'run', 'flight_agent_ros', 'inspect_image_server',
             '--photo-root', str(test_input_root)], stdout=test_temp_log, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            test_temp_client = test_temp_node.create_client(AtomicAction, 'inspect_image')
            assert test_temp_client.wait_for_service(timeout_sec=10)
            request = AtomicAction.Request(input='{"target_class":"person"}',
                                           prev_output=json.dumps(reference))
            response = test_temp_client.call(request, timeout_sec=8)
            assert response is not None and response.success, response
            output = json.loads(response.output)
            assert output['photo_id'] == reference['photo_id']
            assert output['sha256'] == reference['sha256']
            assert output['detections'][0]['xywh'] == [601, 348, 84, 167]
            assert output['detection_cnt'] == 1 and output['error_code'] is None
            (tmp_path/'test_temp_result.json').write_text(json.dumps(
                {'input': request.input, 'prev_output': reference, 'success': response.success,
                 'output': output, 'scope': 'Installed ROS entrypoint, real HOG, saved T03b image; '
                  'no current Gazebo, flight, capture service or Agent'}, indent=2))
        finally:
            os.killpg(test_temp_process.pid, signal.SIGINT)
            test_temp_process.wait(timeout=5)
            test_temp_executor.shutdown(timeout_sec=3)
            test_temp_thread.join(timeout=3)
            test_temp_node.destroy_node()
            rclpy.shutdown()
