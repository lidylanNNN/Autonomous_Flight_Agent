'''检查正式检测服务的边界；构造图片与故障不代表飞行场景证据。'''
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import rclpy
from flight_agent_ros import inspect_image_server as module
from PIL import Image
from rosllm_srvs.srv import AtomicAction


@pytest.fixture
def test_temp_server(tmp_path: Path) -> Iterator[module.InspectImageServer]:
    '''创建真实检测节点，真实工具初始化失败则测试失败。'''
    rclpy.init()
    server = module.InspectImageServer(tmp_path)
    try:
        yield server
    finally:
        server.destroy_node()
        rclpy.shutdown()


@pytest.fixture
def test_input_reference(tmp_path: Path) -> dict:
    '''构造 T03a 布局的纯色图片，仅用于检查读取和空结果行为。'''
    photo_id = '00000000-0000-4000-8000-000000000001'
    directory = tmp_path/photo_id
    directory.mkdir()
    image_path, metadata_path = directory/'image.png', directory/'metadata.json'
    Image.new('RGB', (1280, 960)).save(image_path)
    reference = {'source': 'capture_photo_server', 'error_code': None, 'photo_id': photo_id,
                 'image_path': str(image_path), 'metadata_path': str(metadata_path),
                 'sha256': hashlib.sha256(image_path.read_bytes()).hexdigest()}
    metadata = {test_temp_key: reference[test_temp_key] for test_temp_key in
                ('photo_id', 'image_path', 'sha256')}
    metadata.update(schema_version=1, image={'width': 1280, 'height': 960})
    metadata_path.write_text(json.dumps(metadata))
    return reference


@pytest.mark.parametrize('test_input_text,expected_output_error', [
    ('{}', 'INVALID_INPUT'), ('[]', 'INVALID_INPUT'), ('{', 'INVALID_INPUT'),
    ('{"target_class":"car"}', 'INVALID_INPUT'),
    ('{"target_class":"person","path":"x"}', 'INVALID_INPUT'),
    ('{"target_class":"person"}', 'INVALID_PREV_OUTPUT'),
])
def test_temp_reject_input(test_temp_server: module.InspectImageServer, test_input_text: str,
                           expected_output_error: str) -> None:
    '''非法参数或缺失前项输出不能启动检测，也不能产生空列表冒充未检出。'''
    with patch.object(test_temp_server, 'run_worker') as test_temp_worker:
        response = test_temp_server.inspect(AtomicAction.Request(input=test_input_text),
                                            AtomicAction.Response())
    output = json.loads(response.output)
    assert not response.success and output['error_code'] == expected_output_error
    assert output['detections'] is None and output['photo_id'] is None
    test_temp_worker.assert_not_called()


@pytest.mark.parametrize('test_input_case', ['source', 'failed', 'uuid', 'hash', 'relative', 'array'])
def test_temp_reject_reference(test_input_reference: dict, test_input_case: str) -> None:
    '''拒绝其他动作、失败引用和格式错误，不搜索历史照片。'''
    test_input_changes = {'source': {'source': 'other'}, 'failed': {'error_code': 'SAVE_FAILED'},
                          'uuid': {'photo_id': 'x'}, 'hash': {'sha256': 'g'*64},
                          'relative': {'image_path': 'image.png'}, 'array': {}}
    test_input_reference.update(test_input_changes[test_input_case])
    with pytest.raises(module.InspectError, match='INVALID_PREV_OUTPUT'):
        module.parse_reference('[]' if test_input_case == 'array' else json.dumps(test_input_reference))


@pytest.mark.parametrize('test_input_case', ['missing', 'hash', 'metadata', 'bad_png', 'outside'])
def test_temp_reject_media(tmp_path: Path, test_input_reference: dict, test_input_case: str) -> None:
    '''正式读取函数拒绝坏图与不对应引用，并在调用检测器之前失败。'''
    image_path = Path(test_input_reference['image_path'])
    if test_input_case == 'missing':
        test_input_reference['image_path'] += '.missing'
    elif test_input_case == 'hash':
        test_input_reference['sha256'] = '0'*64
    elif test_input_case == 'metadata':
        Path(test_input_reference['metadata_path']).write_text('{}')
    elif test_input_case == 'outside':
        test_input_reference['image_path'] = str(tmp_path.parent/'image.png')
    else:
        image_path.write_bytes(b'test_input_not_png')
        test_input_reference['sha256'] = hashlib.sha256(image_path.read_bytes()).hexdigest()
        metadata = json.loads(Path(test_input_reference['metadata_path']).read_text())
        metadata['sha256'] = test_input_reference['sha256']
        Path(test_input_reference['metadata_path']).write_text(json.dumps(metadata))
    with patch.object(module, 'create_detector') as test_temp_detector, \
            pytest.raises(module.InspectError, match='MEDIA_INVALID'):
        module.inspect_photo(tmp_path, test_input_reference)
    test_temp_detector.assert_not_called()


def test_temp_empty_and_busy(test_temp_server: module.InspectImageServer,
                             test_input_reference: dict) -> None:
    '''真实子进程检测纯色图返回成功空列表；忙时不启动第二次检测。'''
    request = AtomicAction.Request(input='{"target_class":"person"}',
                                   prev_output=json.dumps(test_input_reference))
    response = test_temp_server.inspect(request, AtomicAction.Response())
    output = json.loads(response.output)
    assert response.success and output['detections'] == [] and output['detection_cnt'] == 0
    test_temp_server._busy.acquire()
    try:
        response = test_temp_server.inspect(request, AtomicAction.Response())
        output = json.loads(response.output)
        assert not response.success and output['error_code'] == 'BUSY'
        assert output['detections'] is None
    finally:
        test_temp_server._busy.release()


def test_temp_timeout_reaps(test_temp_server: module.InspectImageServer,
                            test_input_reference: dict) -> None:
    '''只替换子进程命令为真实休眠进程，正式期限逻辑必须终止并回收它。'''
    test_temp_popen = subprocess.Popen

    def test_temp_launch(*test_input_args: object, **test_input_kwargs: object) -> subprocess.Popen:
        '''保留真实管道和进程，只将检测替换为超过预算的休眠。'''
        return test_temp_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                **test_input_kwargs)

    request = AtomicAction.Request(input='{"target_class":"person"}',
                                   prev_output=json.dumps(test_input_reference))
    with patch.object(module.subprocess, 'Popen', side_effect=test_temp_launch), \
            patch.object(module, '_BUDGET_S', 0.1):
        response = test_temp_server.inspect(request, AtomicAction.Response())
    output = json.loads(response.output)
    assert not response.success and output['error_code'] == 'DETECTION_TIMEOUT'
    assert output['detections'] is None and output['photo_id'] is None
    assert test_temp_server._worker.poll() is not None
    assert test_temp_server.inspect(request, AtomicAction.Response()).success


def test_temp_unavailable(test_temp_server: module.InspectImageServer,
                           test_input_reference: dict) -> None:
    '''子进程启动失败须明确返回不可用，不伪装成未检出。'''
    with patch.object(module.subprocess, 'Popen', side_effect=FileNotFoundError):
        response = test_temp_server.inspect(AtomicAction.Request(input='{"target_class":"person"}',
            prev_output=json.dumps(test_input_reference)), AtomicAction.Response())
    output = json.loads(response.output)
    assert not response.success and output['error_code'] == 'DETECTOR_UNAVAILABLE'
    assert output['detections'] is None and output['sha256'] is None


@pytest.mark.parametrize('test_input_boxes,test_input_weights', [
    ([[0, 0, 1, 1]], []), ([[0, 0, 1, 1]], [float('nan')]), ([[-1, 0, 1, 1]], [1.]),
])
def test_temp_detector_result(tmp_path: Path, test_input_reference: dict,
                               test_input_boxes: list, test_input_weights: list) -> None:
    '''只替换检测器返回值，正式图片核对和结果检查拒绝无效检测。'''
    from unittest.mock import Mock
    test_temp_detector = Mock()
    test_temp_detector.detectMultiScale.return_value = test_input_boxes, test_input_weights
    with patch.object(module, 'create_detector', return_value=(test_temp_detector, {})), \
            pytest.raises(module.InspectError, match='DETECTION_FAILED'):
        module.inspect_photo(tmp_path, test_input_reference)


def test_temp_media_failure_response(test_temp_server: module.InspectImageServer,
                                      test_input_reference: dict) -> None:
    '''真实子进程核对坏哈希后，正式回调不交付部分引用或空检测列表。'''
    test_input_reference['sha256'] = '0'*64
    response = test_temp_server.inspect(AtomicAction.Request(input='{"target_class":"person"}',
        prev_output=json.dumps(test_input_reference)), AtomicAction.Response())
    output = json.loads(response.output)
    assert not response.success and output['error_code'] == 'MEDIA_INVALID'
    assert output['detections'] is None and output['photo_id'] is None
