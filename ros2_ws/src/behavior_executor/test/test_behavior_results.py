'''验证正式 ROS 执行器至有人入口的结果交付；相机、检测和飞行为动作替身。'''
from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest
import rclpy
from agent_comm.ros_agent_srv import RosEnv
from behavior_executor.sequence_executor import SequenceExecutor
from rclpy.executors import MultiThreadedExecutor
from rosllm_srvs.srv import AtomicAction, ExecuteBehavior, Observation

from flight_agent.components.tracing import TraceRecorder


def test_temp_results_survive_tail_action_and_later_failure(
    tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    '''两组媒体后接 RTL，重复行为失败时保留已有成果且不覆盖前轮文件。'''
    rclpy.init(args=['--ros-args', '-p', f'behavior_result_dir:={tmp_path / "results"}'])
    sequence = SequenceExecutor(action_timeout_s=1)
    test_temp_actions = rclpy.create_node('test_temp_result_actions')
    node = rclpy.create_node('test_temp_result_reader')
    env = RosEnv(node, TraceRecorder(tmp_path / 'agent.jsonl'))
    test_temp_calls: list[tuple[str, str]] = []
    test_temp_capture_cnt = 0
    test_temp_fail_inspect = False

    def test_temp_capture(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''生成每次不同的媒体引用，不读取相机。'''
        nonlocal test_temp_capture_cnt
        test_temp_capture_cnt += 1
        test_temp_calls.append(('capture_photo', request.prev_output))
        response.success = True
        response.output = json.dumps({'photo_id': str(test_temp_capture_cnt),
            'image_path': str(tmp_path / f'{test_temp_capture_cnt}.png')})
        return response

    def test_temp_inspect(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''保留收到的媒体引用并返回可区分的检测结果。'''
        test_temp_calls.append(('inspect_image', request.prev_output))
        response.success = not test_temp_fail_inspect
        response.output = json.dumps(dict(json.loads(request.prev_output),
            detection_cnt=0, error_code='DETECTION_TIMEOUT' if test_temp_fail_inspect else None))
        return response

    def rtl(request: AtomicAction.Request, response: AtomicAction.Response) -> AtomicAction.Response:
        '''仅返回飞行替身结果，不执行 PX4 返航。'''
        test_temp_calls.append(('rtl', request.prev_output))
        response.success, response.output = True, 'rtl-evidence'
        return response

    def test_temp_observe(_request: Observation.Request, response: Observation.Response) -> Observation.Response:
        '''提供固定观察；不验证 PX4 状态读取。'''
        response.success, response.source, response.observation = True, 'test_temp_state', 'ground'
        return response

    for name, callback in [('capture_photo', test_temp_capture),
                           ('inspect_image', test_temp_inspect), ('rtl', rtl)]:
        test_temp_actions.create_service(AtomicAction, name, callback)
    test_temp_actions.create_service(Observation, 'get_observation', test_temp_observe)
    executor = MultiThreadedExecutor(num_threads=4)
    for test_temp_node in (sequence, test_temp_actions):
        executor.add_node(test_temp_node)
    test_temp_thread = Thread(target=executor.spin, daemon=True)
    test_temp_thread.start()
    test_input_behavior = '\n'.join(json.dumps({'name': name, 'input': '{}'}) for name in
        ['capture_photo', 'inspect_image', 'capture_photo', 'inspect_image', 'rtl'])
    try:
        env.step(test_input_behavior)
        path = next((tmp_path / 'results').glob('*.json'))
        test_temp_first_bytes = path.read_bytes()
        report = json.loads(test_temp_first_bytes)
        assert report['success'] and len(report['steps']) == 5
        assert report['steps'][-1]['output'] == 'rtl-evidence'
        for index in (0, 2):
            output = json.loads(report['steps'][index]['output'])
            assert json.loads(report['steps'][index + 1]['output'])['photo_id'] == output['photo_id']
            assert test_temp_calls[index + 1][1] == report['steps'][index]['output']
        assert json.loads(report['steps'][0]['output'])['photo_id'] != json.loads(
            report['steps'][2]['output'])['photo_id']
        test_temp_calls.clear()
        test_temp_fail_inspect = True
        env.step(test_input_behavior)
        assert [name for name, _ in test_temp_calls] == ['capture_photo', 'inspect_image']
        assert path.read_bytes() == test_temp_first_bytes
        test_temp_paths = set((tmp_path / 'results').glob('*.json')) - {path}
        assert len(test_temp_paths) == 1
        report = json.loads(test_temp_paths.pop().read_text())
        assert not report['success'] and len(report['steps']) == 2
        assert report['steps'][0]['success'] and not report['steps'][1]['success']
        test_temp_output = capfd.readouterr()
        test_temp_terminal = test_temp_output.out + test_temp_output.err
        assert str(path) in test_temp_terminal and 'DETECTION_TIMEOUT' in test_temp_terminal
        assert str(tmp_path / '1.png') in test_temp_terminal
    finally:
        executor.shutdown()
        test_temp_thread.join(timeout=5)
        for test_temp_node in (node, test_temp_actions, sequence):
            test_temp_node.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('test_input_fault', ['invalid', 'version', 'step', 'mkdir', 'write'])
def test_temp_delivery_fault_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_input_fault: str,
) -> None:
    '''无效响应和保存故障不报告文件交付，写入失败清理不完整文件。'''
    node = Mock()
    node.declare_parameter.return_value.value = str(tmp_path / 'results')
    env = RosEnv(node, Mock())
    response = ExecuteBehavior.Response(success=True, message=json.dumps({
        'schema_version': 1, 'summary': 'done', 'steps': [],
    }))
    if test_input_fault == 'invalid':
        response.message = 'old plain summary'
    elif test_input_fault == 'version':
        response.message = '{"schema_version":2,"summary":"done","steps":[]}'
    elif test_input_fault == 'step':
        response.message = '{"schema_version":1,"summary":"done","steps":[{}]}'
    elif test_input_fault == 'mkdir':
        env._result_dir.parent.mkdir(exist_ok=True)
        env._result_dir.write_text('blocking file')
    else:
        def test_temp_fail_write(*_args: object, **_kwargs: object) -> None:
            '''在文件创建后注入写入失败。'''
            raise OSError('injected write failure')
        monkeypatch.setattr('agent_comm.ros_agent_srv.json.dump', test_temp_fail_write)
    env._deliver_result(response, '动作序列执行成功')
    test_temp_errors = str(node.get_logger.return_value.error.call_args_list)
    assert ('INVALID_BEHAVIOR_RESULT' if test_input_fault in {'invalid', 'version', 'step'}
            else 'BEHAVIOR_RESULT_SAVE_FAILED') in test_temp_errors
    assert '行为结果文件：' not in str(node.get_logger.return_value.info.call_args_list)
    assert response.success
    assert not list(tmp_path.rglob('*.json'))
