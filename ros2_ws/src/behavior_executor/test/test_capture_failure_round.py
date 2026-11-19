'''视觉工具错误经正式 ROS 主链交付；消息源、模型回复和后续动作使用测试替身。'''
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import rclpy
from agent_comm.ros_agent_srv import main
from behavior_executor.sequence_executor import SequenceExecutor
from flight_agent_ros import inspect_image_server as test_temp_inspect_module
from flight_agent_ros.adapters.px4_world_state_adapter import Px4WorldStateAdapter
from flight_agent_ros.capture_photo_server import CapturePhotoServer
from observation_manager.observation_manager_node import ObservationManagerNode
from PIL import Image as PngImage
from px4_msgs.msg import VehicleLocalPosition
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from rosllm_srvs.srv import LLM, AtomicAction
from sensor_msgs.msg import CameraInfo, Image

from flight_agent.components.tracing import TraceReplay
from flight_agent.components.vehicle.state import WorldStateAggregator


@pytest.mark.parametrize(('test_input_fault', 'expected_output_error'), [
    ('no_frame', 'FRAME_TIMEOUT'), ('stale_frame', 'FRAME_TIMEOUT'),
    ('save_failure', 'SAVE_FAILED'),
    ('inspect_unavailable', 'ATOMIC_ACTION_UNAVAILABLE name=inspect_image'),
    ('detector_unavailable', 'DETECTOR_UNAVAILABLE'), ('detector_timeout', 'DETECTION_TIMEOUT'),
])
def test_temp_visual_failure_through_human_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    test_input_fault: str, expected_output_error: str,
) -> None:
    '''正式视觉服务产生错误后，入口保存已有图片和失败结果，停止后续派发。'''
    test_input_detection_fault = test_input_fault in {
        'inspect_unavailable', 'detector_unavailable', 'detector_timeout'}
    test_temp_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    test_input_names = ['capture_photo', 'inspect_image', 'rtl']
    test_input_behavior = '```json\n' + '\n'.join(json.dumps({
        'name': name, 'input': json.dumps({'target_class': 'person'} if name == 'inspect_image' else {}),
    }) for name in test_input_names) + '\n```'
    (tmp_path / 'test_temp_case.json').write_text(json.dumps({
        'fault': test_input_fault, 'expected_error': expected_output_error,
        'behavior': test_input_behavior, 'expected_followup_call_cnt': 0,
    }, ensure_ascii=False, indent=2))
    rclpy.init(args=['--ros-args', '-p', f'behavior_result_dir:={tmp_path / "behavior_results"}'])
    aggregator = WorldStateAggregator()
    source = Px4WorldStateAdapter(aggregator=aggregator, trace_path=tmp_path / 'world.jsonl')
    aggregator.update_local_position(SimpleNamespace(
        timestamp=100, x=0., y=0., z=0., vx=0., vy=0., vz=0., xy_valid=True, z_valid=True,
    ), datetime.now(UTC))
    source._record_snapshot()
    observation = ObservationManagerNode(source)
    test_temp_calls = []
    test_temp_original_capture = CapturePhotoServer.capture

    def capture(self: CapturePhotoServer, request: AtomicAction.Request,
                response: AtomicAction.Response) -> AtomicAction.Response:
        '''仅记录真实采集回调的请求和结果。'''
        test_temp_started = time.monotonic()
        result = test_temp_original_capture(self, request, response)
        test_temp_calls.append({'input': request.input, 'prev_output': request.prev_output,
            'success': result.success, 'output': result.output,
            'elapsed_s': time.monotonic() - test_temp_started})
        return result

    monkeypatch.setattr(CapturePhotoServer, 'capture', capture)
    server = CapturePhotoServer(tmp_path / 'photos')
    test_temp_inspections = []
    test_temp_workers = []
    inspect_server = None
    if test_input_fault.startswith('detector_'):
        # 本批只验证工具运行故障传播，真实工具初始化由原检测组件测试覆盖。
        monkeypatch.setattr(test_temp_inspect_module, 'create_detector',
                            lambda: (None, {'name': 'test_temp_unexecuted_detector'}))
        test_temp_original_inspect = test_temp_inspect_module.InspectImageServer.inspect

        def inspect(self: test_temp_inspect_module.InspectImageServer,
                    request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
            '''记录正式检测回调，保留参数解析、等待预算、回收与错误封装。'''
            test_temp_started = time.monotonic()
            result = test_temp_original_inspect(self, request, response)
            test_temp_inspections.append({'input': request.input, 'prev_output': request.prev_output,
                'success': result.success, 'output': result.output,
                'elapsed_s': time.monotonic() - test_temp_started})
            return result

        monkeypatch.setattr(test_temp_inspect_module.InspectImageServer, 'inspect', inspect)
        inspect_server = test_temp_inspect_module.InspectImageServer(server.output_dir)
        test_temp_popen = subprocess.Popen

        def test_temp_launch(*test_input_args: object, **test_input_kwargs: object) -> subprocess.Popen:
            '''替换工具启动边界；超时使用真实管道及真实休眠进程，保留正式五秒预算。'''
            if test_input_fault == 'detector_unavailable':
                raise FileNotFoundError('test_temp_detector_unavailable')
            test_temp_worker = test_temp_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                              **test_input_kwargs)
            test_temp_workers.append(test_temp_worker)
            return test_temp_worker

        monkeypatch.setattr(test_temp_inspect_module.subprocess, 'Popen', test_temp_launch)
    sequence = SequenceExecutor(action_timeout_s=10)
    test_temp_source = rclpy.create_node('test_temp_capture_failure_sources')
    test_temp_llm_calls = []
    test_temp_followup_calls = []
    test_temp_write_calls = []
    test_temp_message_cnt = 0

    def test_temp_llm(request: LLM.Request, response: LLM.Response) -> LLM.Response:
        '''固定模型输出，未调用 Provider 或真实模型。'''
        test_temp_llm_calls.append(request.prompt)
        response.success, response.response, response.info = True, test_input_behavior, 'test_temp_fixed_llm'
        return response

    def test_temp_followup(request: AtomicAction.Request,
                           response: AtomicAction.Response) -> AtomicAction.Response:
        '''若错误派发后续动作，记录调用并立即返回，避免测试挂起。'''
        test_temp_followup_calls.append({'input': request.input, 'prev_output': request.prev_output})
        response.success, response.output = True, 'test_temp_unexpected_followup'
        return response

    test_temp_source.create_service(LLM, 'call_llm', test_temp_llm)
    for test_input_name in test_input_names[1:]:
        if test_input_detection_fault and test_input_name == 'inspect_image':
            continue
        test_temp_source.create_service(AtomicAction, test_input_name, test_temp_followup)
    test_temp_publishers = [test_temp_source.create_publisher(test_input_type, test_input_topic,
        qos_profile_sensor_data) for test_input_type, test_input_topic in (
            (Clock, '/clock'), (Image, '/camera/image_raw'), (CameraInfo, '/camera/camera_info'),
            (VehicleLocalPosition, '/fmu/out/vehicle_local_position'))]
    test_input_pixels = bytes(1280 * 960 * 3)

    def test_temp_publish() -> None:
        '''持续推进构造时钟；过期场景持续发送源时间落后两秒的完整图片。'''
        nonlocal test_temp_message_cnt
        test_temp_message_cnt += 1
        test_input_ns = 4_000_000_000 + test_temp_message_cnt * 40_000_000
        test_input_clock = Clock()
        test_input_clock.clock.sec, test_input_clock.clock.nanosec = divmod(test_input_ns, 10**9)
        test_temp_publishers[0].publish(test_input_clock)
        if test_input_fault == 'no_frame':
            return
        image = Image(width=1280, height=960, encoding='rgb8', step=3840, data=test_input_pixels)
        test_input_stamp = test_input_ns - (2_000_000_000 if test_input_fault == 'stale_frame' else 0)
        image.header.stamp.sec, image.header.stamp.nanosec = divmod(test_input_stamp, 10**9)
        image.header.frame_id = 'test_temp_camera'
        info = CameraInfo(width=1280, height=960, header=image.header,
                          k=[500., 0., 640., 0., 500., 480., 0., 0., 1.],
                          p=[500., 0., 640., 0., 0., 500., 480., 0., 0., 0., 1., 0.])
        position = VehicleLocalPosition(xy_valid=True, z_valid=True)
        for test_temp_publisher, test_input_message in zip(
            test_temp_publishers[1:], (image, info, position), strict=True,
        ):
            test_temp_publisher.publish(test_input_message)

    test_temp_source.create_timer(0.04, test_temp_publish)
    if test_input_fault == 'save_failure':
        def test_temp_fail_png(self: PngImage.Image, fp: object, *args: object, **kwargs: object) -> None:
            '''仅替换 PNG 文件写入，保留正式有效帧等待及保存错误封装。'''
            test_temp_write_calls.append(str(fp))
            raise OSError('test_temp_injected_png_write_failure')
        monkeypatch.setattr(PngImage.Image, 'save', test_temp_fail_png)
    executor = MultiThreadedExecutor(num_threads=6)
    test_temp_nodes = (source, observation, server, sequence, test_temp_source) + (
        (inspect_server,) if inspect_server is not None else ())
    for node in test_temp_nodes:
        executor.add_node(node)
    test_temp_thread = Thread(target=executor.spin, daemon=True)
    test_temp_thread.start()
    test_temp_input_cnt = 0

    def test_temp_read_input(test_input_prompt: str) -> str:
        '''真实入口初始观察后注入一轮输入，下一轮提示时结束。'''
        nonlocal test_temp_input_cnt
        test_temp_input_cnt += 1
        if test_temp_input_cnt == 1:
            test_temp_deadline = time.monotonic() + 5
            while not server.clock_ready(time.monotonic_ns()) or (
                test_input_fault != 'no_frame' and (not server._images or not server._infos)
            ):
                assert time.monotonic() < test_temp_deadline
                time.sleep(0.01)
            return '拍一张新照片，检测其中是否有人，然后返航。'
        raise EOFError

    try:
        trace_path = tmp_path / 'agent.jsonl'
        main(input_reader=test_temp_read_input, trace_path=trace_path)
        assert test_temp_input_cnt == 2 and len(test_temp_llm_calls) == 1
        assert len(test_temp_calls) == 1 and test_temp_calls[0]['success'] == test_input_detection_fault
        assert test_temp_calls[0]['input'] == '{}' and test_temp_calls[0]['prev_output'] == ''
        output = json.loads(test_temp_calls[0]['output'])
        assert test_temp_followup_calls == []
        if test_input_detection_fault:
            assert output['error_code'] is None
            assert len(list(server.output_dir.iterdir())) == 1
            assert hashlib.sha256(Path(output['image_path']).read_bytes()).hexdigest() == output['sha256']
            metadata = json.loads(Path(output['metadata_path']).read_text())
            assert metadata['photo_id'] == output['photo_id']
            assert metadata['timing']['image_received_monotonic_ns'] > metadata['timing']['request_monotonic_ns']
            if test_input_fault == 'inspect_unavailable':
                assert not test_temp_inspections
            else:
                assert len(test_temp_inspections) == 1 and not test_temp_inspections[0]['success']
                assert test_temp_inspections[0]['prev_output'] == test_temp_calls[0]['output']
                assert json.loads(test_temp_inspections[0]['input']) == {'target_class': 'person'}
                test_temp_detection = json.loads(test_temp_inspections[0]['output'])
                assert test_temp_detection['error_code'] == expected_output_error
                assert test_temp_detection['detections'] is None and test_temp_detection['detection_cnt'] is None
                if test_input_fault == 'detector_timeout':
                    assert test_temp_inspections[0]['elapsed_s'] >= 5
                    assert len(test_temp_workers) == 1 and test_temp_workers[0].poll() is not None
                else:
                    assert not test_temp_workers
        else:
            assert output['error_code'] == expected_output_error
            assert all(output[test_temp_field] is None for test_temp_field in (
                'photo_id', 'image_path', 'metadata_path', 'sha256'))
            assert not list(server.output_dir.iterdir())
        if test_input_fault == 'save_failure':
            assert len(test_temp_write_calls) == 1
        elif not test_input_detection_fault:
            assert test_temp_calls[0]['elapsed_s'] >= 5
            assert not test_temp_write_calls
        if test_input_fault == 'no_frame':
            assert not server._images
        elif test_input_fault == 'stale_frame':
            assert server._images and server._infos
        test_temp_reports = list((tmp_path / 'behavior_results').glob('*.json'))
        assert len(test_temp_reports) == 1
        report = json.loads(test_temp_reports[0].read_text())
        assert not report['success'] and len(report['steps']) == (2 if test_input_detection_fault else 1)
        assert report['steps'][0]['name'] == 'capture_photo'
        assert report['steps'][0]['success'] == test_input_detection_fault
        assert report['steps'][0]['output'] == test_temp_calls[0]['output']
        if test_input_detection_fault:
            assert report['steps'][1]['name'] == 'inspect_image'
            if test_input_fault == 'inspect_unavailable':
                assert report['steps'][1]['success'] is None and report['steps'][1]['output'] is None
                assert report['steps'][1]['error'] == expected_output_error
            else:
                assert report['steps'][1]['success'] is False and report['steps'][1]['error'] is None
                assert report['steps'][1]['output'] == test_temp_inspections[0]['output']
        else:
            assert 'ATOMIC_ACTION_FAILED name=capture_photo' in report['summary']
        observations = tuple(TraceReplay(trace_path).iter_observation_records())
        assert len(observations) == 2 and all(item.success for item in observations)
        reply, = TraceReplay(trace_path).iter_llm_response_records()
        assert reply.response == test_input_behavior and reply.info == 'test_temp_fixed_llm'
    finally:
        executor.shutdown(timeout_sec=10)
        test_temp_thread.join(timeout=5)
        for node in reversed(test_temp_nodes):
            node.destroy_node()
        rclpy.shutdown()
        test_temp_output = capfd.readouterr()
        test_temp_terminal = test_temp_output.out + test_temp_output.err
        (tmp_path / 'test_temp_terminal.log').write_text(test_temp_terminal)
        (tmp_path / 'test_temp_calls.json').write_text(json.dumps({
            'capture': test_temp_calls, 'followup': test_temp_followup_calls,
            'inspect': test_temp_inspections,
            'workers': [{'pid': worker.pid, 'returncode': worker.poll()} for worker in test_temp_workers],
            'png_write': test_temp_write_calls, 'published_message_cnt': test_temp_message_cnt,
        }, ensure_ascii=False, indent=2))
        test_temp_root = Path(__file__).resolve().parents[4]
        (tmp_path / 'test_temp_manifest.json').write_text(json.dumps({
            'commit': test_temp_commit,
            'substitutions': ['fixed_llm_service', 'constructed_camera_clock_position',
                              'followup_action_probes'] + (['png_write_error'] if test_input_fault == 'save_failure' else [])
                              + (['detector_initialization', 'worker_launch_boundary'] if inspect_server is not None else [])
                              + (['inspect_service_not_registered'] if test_input_fault == 'inspect_unavailable' else []),
            'source_sha256': {str(path.relative_to(test_temp_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [Path(__file__), *[test_temp_root / name for name in (
                    'ros2_ws/src/flight_agent_ros/flight_agent_ros/capture_photo_server.py',
                    'ros2_ws/src/flight_agent_ros/flight_agent_ros/inspect_image_server.py',
                    'ros2_ws/src/behavior_executor/behavior_executor/sequence_executor.py',
                    'ros2_ws/src/agent_comm/agent_comm/ros_agent_srv.py')]]},
        }, indent=2))
    assert '动作序列执行失败或被拒绝' in test_temp_terminal
    assert expected_output_error in test_temp_terminal
    assert '动作序列执行成功' not in test_temp_terminal
    assert 'BEHAVIOR_RESULT_SAVE_FAILED' not in test_temp_terminal
    if test_input_detection_fault:
        assert output['image_path'] in test_temp_terminal
        assert '该图片未检出人物' not in test_temp_terminal
