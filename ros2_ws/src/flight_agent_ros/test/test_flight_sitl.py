'''通过实际 ROS 服务验证 PX4/Gazebo 拒绝；显式使用 -m sitl 运行。'''

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import FrameType

import pytest

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.sitl


def test_takeoff_above_limit_rejects_without_dispatch(sitl_run: Path) -> None:
    '''真实状态下拒绝超高度请求，并同时检查后端执行和飞控命令为零。'''
    import rclpy
    from flight_agent_ros.adapters.px4_parameter_adapter import Px4ParameterAdapter
    from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleCommand
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from rosllm_srvs.srv import AtomicAction

    messages: list[dict[str, object]] = []
    topics = {
        '/fmu/in/vehicle_command': (VehicleCommand, 'px4_vehicle_command_adapter'),
        '/fmu/in/offboard_control_mode': (OffboardControlMode, 'px4_offboard_setpoint_adapter'),
        '/fmu/in/trajectory_setpoint': (TrajectorySetpoint, 'px4_offboard_setpoint_adapter'),
    }
    rclpy.init()
    node = rclpy.create_node('m4_7e_rejection_client')
    try:
        qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT)
        for topic, (message_type, _) in topics.items():
            node.create_subscription(message_type, topic, lambda msg, topic=topic: messages.append({
                'topic': topic, 'timestamp': int(msg.timestamp),
            }), qos)
        client = node.create_client(AtomicAction, '/takeoff')
        deadline = monotonic() + 100
        while monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            state = _latest_state(sitl_run)
            if (client.service_is_ready() and state.get('position_valid')
                    and state.get('landed') and state.get('home_valid')):
                break
        else:
            pytest.fail(f'PX4 地面状态或服务未就绪；日志：{sitl_run}')
        assert state['armed'] is False
        _save(sitl_run / 'state_before.json', state)
        publishers = {topic: [info.node_name for info in node.get_publishers_info_by_topic(topic)]
                      for topic in topics}
        _save(sitl_run / 'publishers.json', publishers)
        for topic, (_, publisher) in topics.items():
            assert publisher in publishers[topic]
        speed = asyncio.run(Px4ParameterAdapter().read_validated_speed_limit(max_age_s=2.0))
        _save(sitl_run / 'px4_speed.json', {'MPC_XY_VEL_MAX': speed})
        until = monotonic() + 2
        while monotonic() < until:
            rclpy.spin_once(node, timeout_sec=0.1)
        request = AtomicAction.Request(input='{"target_altitude_m":6.0}', prev_output='')
        _save(sitl_run / 'request.json', {'service': '/takeoff', 'input': request.input})
        started = monotonic()
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=10)
        assert future.done(), 'ROS 动作请求超时'
        response = future.result()
        assert response is not None
        output = json.loads(response.output)
        _save(sitl_run / 'response.json', {
            'success': response.success, 'output': output, 'elapsed_s': monotonic() - started,
        })
        until = monotonic() + 2
        while monotonic() < until:
            rclpy.spin_once(node, timeout_sec=0.1)
        counts = json.loads((sitl_run / 'backend_calls.json').read_text())
        records = [json.loads(line) for line in (sitl_run / 'safety.jsonl').read_text().splitlines()]
        assert response.success is False
        assert output['reason_codes'] == ['ALTITUDE_ABOVE_ENVELOPE']
        assert output['error_code'] is None and output['execution_id'] is None
        assert counts['state_reads'] >= 2 and counts['execute_calls'] == 0
        assert messages == []
        record, = records
        assert record['decision']['decision'] == 'REJECT'
        assert record['decision']['decision_id'] == output['decision_id']
        assert record['proposal']['proposal_id'] == output['proposal_id']
        _save(sitl_run / 'result.json', {'passed': True, 'backend_calls': counts,
                                        'command_count': len(messages)})
    finally:
        _save(sitl_run / 'command_messages.json', messages)
        node.destroy_node()
        rclpy.shutdown()


@pytest.fixture
def sitl_run() -> Iterator[Path]:
    '''启动实际仿真和 server，保留证据，并在用例失败时也清理本轮进程。'''
    run = ROOT / 'artifacts/m4-7e' / datetime.now(UTC).strftime('pytest-%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    settings = json.loads((ROOT / 'configs/flight_safety.example.json').read_text())
    settings.update(max_altitude_m=5.0, max_mission_radius_m=10.0)
    settings['geofence'].update(north_min_m=-10.0, north_max_m=10.0,
                               east_min_m=-10.0, east_max_m=10.0)
    _save(run / 'safety.json', settings)
    env = os.environ.copy()
    env.update(FLIGHT_AGENT_TRACE_PATH=str(run / 'world.jsonl'), GZ_PARTITION=run.name)
    _save(run / 'manifest.json', {
        'code': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'diff_sha256': hashlib.sha256(subprocess.check_output(['git', 'diff'], cwd=ROOT)).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'px4': subprocess.check_output(['git', '-C', env.get('PX4_ROOT', str(Path.home() /
            'PX4-Autopilot')), 'rev-parse', 'HEAD'], text=True).strip(),
        'python': sys.version, 'ros_distro': env.get('ROS_DISTRO'),
        'authorization': 'V1 controlled SITL assumes operator authorization',
        'scenario': 'Takeoff 6m exceeds system limit 5m; rejection only',
    })
    print(f'\nSITL evidence: {run}', flush=True)
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for name, command in [
            ('simulation', [str(ROOT / 'scripts/ros2_px4/start_simulation.sh')]),
            ('server', [sys.executable, str(Path(__file__).resolve()), str(run)]),
        ]:
            with (run / f'{name}.log').open('wb') as log:
                processes.append(subprocess.Popen(
                    command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True,
                ))
        yield run
    finally:
        for process in reversed(processes):
            _stop(process)
        _save(run / 'processes.json', [{'pid': p.pid, 'returncode': p.returncode} for p in processes])
        _save(run / 'sha256.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in run.iterdir() if p.is_file()})


def _save(path: Path, value: object) -> None:
    '''保存可核对的运行证据。'''
    path.write_text(json.dumps(value, indent=2) + '\n')


def _latest_state(run: Path) -> dict[str, object]:
    '''读取 server 保存的最新状态，忽略正在写入的半行。'''
    path = run / 'world.jsonl'
    if path.exists():
        for line in reversed(path.read_text().splitlines()):
            try:
                return json.loads(line)['state']
            except json.JSONDecodeError:
                continue
    return {}


def _stop(process: subprocess.Popen[bytes]) -> None:
    '''只停止本用例创建的进程组，超时后升级退出信号。'''
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.wait(timeout=7)
                break
            except subprocess.TimeoutExpired:
                continue
        process.wait(timeout=7)
    finally:
        if process.stdin is not None:
            process.stdin.close()


def _server(run: Path) -> None:
    '''观察真实后端函数进入次数，然后调用实际 main，不替换对象或函数。'''
    from flight_agent_ros import flight_atomic_action_server as entry
    from flight_agent_ros.adapters.flight_execution_backend import Px4Ros2FlightExecutionBackend

    counts = {'state_reads': 0, 'execute_calls': 0}
    sources = {'server_source': entry.__file__,
        'backend_source': sys.modules[Px4Ros2FlightExecutionBackend.__module__].__file__,
    }
    codes = {Px4Ros2FlightExecutionBackend.get_world_state.__code__: 'state_reads',
             Px4Ros2FlightExecutionBackend.execute.__code__: 'execute_calls'}

    def test_temp_record_backend_call(frame: FrameType, event: str, arg: object) -> None:
        '''只记录目标函数调用，不改变执行结果。'''
        if event == 'call' and frame.f_code in codes:
            key = codes[frame.f_code]
            counts[key] += 1
            _save(run / 'backend_calls.json', counts | sources)

    _save(run / 'backend_calls.json', counts | sources)
    sys.argv = ['flight_atomic_action_server', '--safety-json', str(run / 'safety.json'),
                '--trace-path', str(run / 'safety.jsonl')]
    sys.setprofile(test_temp_record_backend_call)
    threading.setprofile(test_temp_record_backend_call)
    try:
        entry.main()
    finally:
        threading.setprofile(None)
        sys.setprofile(None)
        _save(run / 'backend_calls.json', counts | sources)


if __name__ == '__main__':
    _server(Path(sys.argv[1]))
