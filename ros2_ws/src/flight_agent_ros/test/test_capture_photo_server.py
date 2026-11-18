'''直接检查正式拍照代码；构造消息的测试不代表真实相机验证。'''
import hashlib
import json
import time
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
import rclpy
from flight_agent_ros.capture_photo_server import CaptureError, CapturePhotoServer, valid_frame
from PIL import Image as PngImage
from px4_msgs.msg import VehicleLocalPosition
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from rosllm_srvs.srv import AtomicAction
from sensor_msgs.msg import CameraInfo, Image


@pytest.fixture
def test_temp_server(tmp_path: Path) -> Iterator[CapturePhotoServer]:
    '''每个用例创建正式节点，并在用例结束后清理 ROS 资源。'''
    rclpy.init()
    server = CapturePhotoServer(tmp_path/'photos')
    try:
        yield server
    finally:
        server.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('test_input_text,expected_output_error', [
    ('[]', 'INVALID_INPUT'), ('{', 'INVALID_INPUT'), ('null', 'INVALID_INPUT'),
    ('{"path":"elsewhere"}', 'INVALID_INPUT'), ('{}', 'CLOCK_UNAVAILABLE'),
])
def test_temp_input_and_clock(test_temp_server: CapturePhotoServer, test_input_text: str,
                              expected_output_error: str) -> None:
    '''无有效基线或非法输入时，正式回调不交付任何文件引用。'''
    response = test_temp_server.capture(AtomicAction.Request(input=test_input_text),
                                        AtomicAction.Response())
    output = json.loads(response.output)
    assert not response.success and output['error_code'] == expected_output_error
    assert all(output[test_temp_key] is None for test_temp_key in
               ('photo_id', 'image_path', 'metadata_path', 'sha256'))
    assert not list(test_temp_server.output_dir.iterdir())


@pytest.fixture
def test_input_messages() -> tuple[Image, CameraInfo, VehicleLocalPosition]:
    '''提供明确标注的相机与位置输入，不将其当作真实传感器证据。'''
    image = Image(width=1280, height=960, encoding='rgb8', step=3840,
                  data=bytes([37, 91, 143]) * (1280 * 960))
    image.header.stamp.sec, image.header.stamp.nanosec = 2, 100_000_000
    image.header.frame_id = 'camera/imager'
    info = CameraInfo(width=1280, height=960)
    info.header = deepcopy(image.header)
    info.k = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
    info.p = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0.]
    position = VehicleLocalPosition(x=1., y=2., z=-3., xy_valid=True, z_valid=True,
                                    timestamp=12345, timestamp_sample=12340)
    return image, info, position


@pytest.mark.parametrize('test_input_target,test_input_field,test_input_value', [
    ('image', 'encoding', 'bgr8'), ('image', 'step', 1), ('image', 'data', b''),
    ('image', 'width', 640), ('info', 'height', 480),
    ('info', 'k', [0.] * 9), ('info', 'p', [0.] * 12), ('info', 'd', [float('nan')]),
])
def test_temp_image_requirements(test_input_messages: tuple, test_input_target: str,
                                 test_input_field: str, test_input_value: object) -> None:
    '''坏图和错配参数直接交给正式检查函数，不创建另一份规则。'''
    image, info, _ = test_input_messages
    assert valid_frame(image, info)
    setattr(image if test_input_target == 'image' else info, test_input_field, test_input_value)
    assert not valid_frame(image, info)


@pytest.mark.parametrize('test_input_changes,expected_output_success', [
    ({}, True), ({'received': 10_000_000_000}, False), ({'baseline': 2_100_000_000}, False),
    ({'clock': 2_600_000_000}, True), ({'clock': 2_600_000_001}, False),
    ({'clock': 2_099_999_999}, False), ({'clock': 0}, False),
    ({'progress': 9_400_000_000}, True), ({'progress': 9_399_999_999}, False),
    ({'now': 10_600_000_000}, True), ({'now': 10_600_000_001}, False),
    ({'now': 15_000_000_000}, False),
])
def test_temp_frame_time(test_temp_server: CapturePhotoServer, test_input_messages: tuple,
                          test_input_changes: dict, expected_output_success: bool) -> None:
    '''正式等待逻辑检查源时间、接收时间、时钟停止及各自的等号边界。'''
    image, info, position = test_input_messages
    test_input_times = {'received': 10_100_000_000, 'baseline': 2_000_000_000,
                        'clock': 2_200_000_000, 'progress': 10_200_000_000,
                        'now': 10_400_000_000} | test_input_changes
    test_temp_server._images.append((image, test_input_times['received']))
    test_temp_server._infos[2_100_000_000] = info
    test_temp_server._position = position, test_input_times['now']
    test_temp_server._clock = test_input_times['clock']
    test_temp_server._progress = test_input_times['progress']
    with patch('flight_agent_ros.capture_photo_server.time.monotonic_ns',
               side_effect=[test_input_times['now'], 15_000_000_000]), \
            patch.object(test_temp_server._condition, 'wait'), test_temp_server._condition:
        if expected_output_success:
            assert test_temp_server.wait_frame(10_000_000_000, test_input_times['baseline'])[0] is image
        else:
            with pytest.raises(CaptureError, match='FRAME_TIMEOUT'):
                test_temp_server.wait_frame(10_000_000_000, test_input_times['baseline'])


def test_temp_reset_and_busy(test_temp_server: CapturePhotoServer, test_input_messages: tuple) -> None:
    '''回退清空后不能自动恢复；忙时另一个请求立即失败。'''
    image, info, position = test_input_messages
    for test_input_kind, test_input_message in [('image', image), ('info', info), ('position', position)]:
        test_temp_server.receive(test_input_kind, test_input_message)
    test_input_clock = Clock()
    for test_input_sec in (2, 1, 3):
        test_input_clock.clock.sec = test_input_sec
        test_temp_server.receive_clock(test_input_clock)
    assert not test_temp_server._images and not test_temp_server._infos
    assert test_temp_server._position is None
    response = test_temp_server.capture(AtomicAction.Request(input='{}'), AtomicAction.Response())
    assert json.loads(response.output)['error_code'] == 'CLOCK_RESET'
    test_temp_server._busy.acquire()
    try:
        response = test_temp_server.capture(AtomicAction.Request(input='{}'), AtomicAction.Response())
        assert json.loads(response.output)['error_code'] == 'BUSY'
    finally:
        test_temp_server._busy.release()


@pytest.mark.parametrize('test_input_target', [
    'PngImage.frombytes', 'Path.write_text', 'PngImage.open', 'Path.rename',
])
def test_temp_save_failure(test_temp_server: CapturePhotoServer, test_input_messages: tuple,
                            test_input_target: str) -> None:
    '''仅替换文件故障和等帧过程，检查正式回调无成功引用并清理临时文件。'''
    image, info, position = test_input_messages
    test_temp_server._clock = 2_200_000_000
    test_temp_server._progress = time.monotonic_ns()
    with patch.object(test_temp_server, 'wait_frame', return_value=(image, info, {}, position)), \
            patch('flight_agent_ros.capture_photo_server.' + test_input_target,
                  side_effect=OSError('test_temp_disk_failure')):
        response = test_temp_server.capture(AtomicAction.Request(input='{}'), AtomicAction.Response())
    output = json.loads(response.output)
    assert not response.success and output['error_code'] == 'SAVE_FAILED'
    assert output['image_path'] is None and output['metadata_path'] is None
    assert not list(test_temp_server.output_dir.iterdir())


@pytest.mark.parametrize('test_input_case', ['missing', 'stamp', 'frame', 'empty'])
def test_temp_camera_pair(test_input_messages: tuple, test_input_case: str) -> None:
    '''缺参数、错时间或坐标系名称不对应时不能接受图像。'''
    image, info, _ = test_input_messages
    if test_input_case == 'missing':
        info = None
    elif test_input_case == 'stamp':
        info.header.stamp.nanosec += 1
    elif test_input_case == 'frame':
        info.header.frame_id = 'other'
    else:
        image.header.frame_id = ''
    assert not valid_frame(image, info)


@pytest.mark.parametrize('test_input_case', ['missing', 'old', 'xy', 'z', 'nan'])
def test_temp_position(test_temp_server: CapturePhotoServer, test_input_messages: tuple,
                         test_input_case: str) -> None:
    '''正式业务不接受缺失、过期或无效位置。'''
    image, info, position = test_input_messages
    test_temp_server._images.append((image, 10_100_000_000))
    test_temp_server._infos[2_100_000_000] = info
    test_temp_server._clock, test_temp_server._progress = 2_200_000_000, 10_200_000_000
    if test_input_case != 'missing':
        test_temp_server._position = position, (9_000_000_000 if test_input_case == 'old' else 10_100_000_000)
    if test_input_case in {'xy', 'z'}:
        setattr(position, test_input_case + '_valid', False)
    if test_input_case == 'nan':
        position.x = float('nan')
    with patch('flight_agent_ros.capture_photo_server.time.monotonic_ns', return_value=10_400_000_000), \
            test_temp_server._condition, pytest.raises(CaptureError, match='POSITION_UNAVAILABLE'):
        test_temp_server.wait_frame(10_000_000_000, 2_000_000_000)


def test_temp_startup_directory(tmp_path: Path) -> None:
    '''输出位置是普通文件时，节点注册服务前就必须失败。'''
    test_input_path = tmp_path/'test_temp_file'
    test_input_path.write_text('occupied')
    with pytest.raises(FileExistsError):
        CapturePhotoServer(test_input_path)


def test_temp_ros_capture(test_temp_server: CapturePhotoServer, test_input_messages: tuple) -> None:
    '''构造消息经真实 ROS 传输调用正式服务，验证保存、连续拍照、忙和无新图超时。'''
    image, info, position = test_input_messages
    test_temp_source = rclpy.create_node('test_temp_camera_source')
    test_temp_publishers = [test_temp_source.create_publisher(test_temp_type, test_temp_topic, qos_profile_sensor_data)
                           for test_temp_type, test_temp_topic in (
                               (Image, '/camera/image_raw'), (CameraInfo, '/camera/camera_info'),
                               (VehicleLocalPosition, '/fmu/out/vehicle_local_position'), (Clock, '/clock'))]
    test_temp_tick_cnt = 0

    def test_temp_publish() -> None:
        '''持续发出递增的构造消息，来源不冒充 Gazebo 或 PX4。'''
        nonlocal test_temp_tick_cnt
        test_temp_tick_cnt += 1
        test_input_clock = Clock()
        test_input_ns = 2_000_000_000 + test_temp_tick_cnt * 40_000_000
        test_input_clock.clock.sec, test_input_clock.clock.nanosec = divmod(test_input_ns, 10**9)
        image.header.stamp.sec, image.header.stamp.nanosec = divmod(test_input_ns, 10**9)
        info.header = deepcopy(image.header)
        for test_temp_publisher, test_input_message in zip(test_temp_publishers, (image, info, position, test_input_clock), strict=True):
            test_temp_publisher.publish(test_input_message)

    test_temp_timer = test_temp_source.create_timer(0.04, test_temp_publish)
    test_temp_client = test_temp_source.create_client(AtomicAction, 'capture_photo')
    test_temp_executor = MultiThreadedExecutor(num_threads=3)
    for test_temp_node in (test_temp_server, test_temp_source):
        test_temp_executor.add_node(test_temp_node)
    test_temp_thread = Thread(target=test_temp_executor.spin, daemon=True)
    test_temp_thread.start()
    try:
        assert test_temp_client.wait_for_service(timeout_sec=3)
        test_temp_started = time.monotonic()
        while not test_temp_server._images or not test_temp_server._infos or test_temp_server._position is None:
            assert time.monotonic() - test_temp_started < 5
            time.sleep(0.01)
        test_temp_outputs = []
        for _ in range(2):
            response = test_temp_client.call(AtomicAction.Request(input='{}', prev_output='{"x":999}'), timeout_sec=7)
            assert response is not None and response.success, response
            output = json.loads(response.output)
            metadata = json.loads(Path(output['metadata_path']).read_text())
            assert metadata['vehicle_position']['x'] == 1.
            assert metadata['vehicle_position']['timestamp_sample'] == 12340
            assert metadata['sha256'] == output['sha256'] == hashlib.sha256(Path(output['image_path']).read_bytes()).hexdigest()
            with PngImage.open(output['image_path']) as test_temp_saved:
                assert test_temp_saved.tobytes() == bytes(image.data)
            assert metadata['timing']['image_received_monotonic_ns'] > metadata['timing']['request_monotonic_ns']
            test_temp_outputs.append(output)
        assert test_temp_outputs[0]['photo_id'] != test_temp_outputs[1]['photo_id']
        assert hashlib.sha256(Path(test_temp_outputs[0]['image_path']).read_bytes()).hexdigest() == test_temp_outputs[0]['sha256']
        test_temp_timer.cancel()
        time.sleep(0.1)
        test_temp_future = test_temp_client.call_async(AtomicAction.Request(input='{}'))
        test_temp_started = time.monotonic()
        while not test_temp_server._busy.locked():
            assert time.monotonic() - test_temp_started < 2
            time.sleep(0.005)
        response = test_temp_client.call(AtomicAction.Request(input='{}'), timeout_sec=2)
        assert response is not None and json.loads(response.output)['error_code'] == 'BUSY'
        while not test_temp_future.done():
            assert time.monotonic() - test_temp_started < 6
            time.sleep(0.01)
        response = test_temp_future.result()
        assert response is not None and json.loads(response.output)['error_code'] == 'FRAME_TIMEOUT'
        assert len(list(test_temp_server.output_dir.iterdir())) == 2
    finally:
        test_temp_executor.shutdown(timeout_sec=7)
        test_temp_thread.join(timeout=2)
        test_temp_source.destroy_node()


def test_temp_reset_during_request(test_temp_server: CapturePhotoServer) -> None:
    '''正式请求等待期间发生回退，必须立即失败并保持锁定。'''
    test_temp_server._clock, test_temp_server._progress = 2_000_000_000, time.monotonic_ns()
    test_temp_results = []

    def test_temp_request() -> None:
        '''让正式回调实际等待，不替换业务等待函数。'''
        test_temp_results.append(test_temp_server.capture(AtomicAction.Request(input='{}'), AtomicAction.Response()))

    test_temp_thread = Thread(target=test_temp_request)
    test_temp_thread.start()
    test_temp_started = time.monotonic()
    while not test_temp_server._busy.locked():
        assert time.monotonic() - test_temp_started < 1
        time.sleep(0.005)
    test_input_clock = Clock()
    test_input_clock.clock.sec = 1
    test_temp_server.receive_clock(test_input_clock)
    test_temp_thread.join(timeout=1)
    assert not test_temp_thread.is_alive()
    assert json.loads(test_temp_results[0].output)['error_code'] == 'CLOCK_RESET'
    assert not list(test_temp_server.output_dir.iterdir())


def test_temp_reset_during_save(test_temp_server: CapturePhotoServer, test_input_messages: tuple) -> None:
    '''写完图片到最终交付前发生回退时，不能返回或保留成功目录。'''
    image, info, position = test_input_messages
    test_temp_server._clock, test_temp_server._progress = 2_000_000_000, time.monotonic_ns()
    test_temp_open = PngImage.open

    def test_temp_open_after_reset(test_input_path: Path) -> PngImage.Image:
        '''在重新读取 PNG 前输入回退时钟，保留实际文件读写。'''
        test_input_clock = Clock()
        test_input_clock.clock.sec = 1
        test_temp_server.receive_clock(test_input_clock)
        return test_temp_open(test_input_path)

    with patch.object(test_temp_server, 'wait_frame', return_value=(image, info, {}, position)), \
            patch('flight_agent_ros.capture_photo_server.PngImage.open', side_effect=test_temp_open_after_reset):
        response = test_temp_server.capture(AtomicAction.Request(input='{}'), AtomicAction.Response())
    assert not response.success and json.loads(response.output)['error_code'] == 'CLOCK_RESET'
    assert not list(test_temp_server.output_dir.iterdir())


def test_temp_png_changed_during_check(test_temp_server: CapturePhotoServer, test_input_messages: tuple) -> None:
    '''PNG 像素虽仍可解码，但文件字节变化后必须拒绝旧哈希并清理。'''
    image, info, position = test_input_messages
    test_temp_server._clock, test_temp_server._progress = 2_000_000_000, time.monotonic_ns()
    test_temp_open = PngImage.open

    def test_temp_open_and_append(test_input_path: Path) -> PngImage.Image:
        '''追加 PNG 解码器会忽略的尾部字节，以检查哈希复核。'''
        test_temp_saved = test_temp_open(test_input_path)
        test_input_path.write_bytes(test_input_path.read_bytes()+b'test_temp_changed')
        return test_temp_saved

    with patch.object(test_temp_server, 'wait_frame', return_value=(image, info, {}, position)), \
            patch('flight_agent_ros.capture_photo_server.PngImage.open', side_effect=test_temp_open_and_append):
        response = test_temp_server.capture(AtomicAction.Request(input='{}'), AtomicAction.Response())
    assert not response.success and json.loads(response.output)['error_code'] == 'SAVE_FAILED'
    assert not list(test_temp_server.output_dir.iterdir())
