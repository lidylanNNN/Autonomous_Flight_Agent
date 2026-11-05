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


def test_takeoff_above_limit_rejects_without_dispatch(test_temp_sitl_run: Path) -> None:
    '''真实状态下拒绝超高度请求，并同时检查后端执行和飞控命令为零。'''
    import rclpy
    from flight_agent_ros.adapters.px4_parameter_adapter import Px4ParameterAdapter
    from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleCommand
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from rosllm_srvs.srv import AtomicAction

    test_temp_messages: list[dict[str, object]] = []
    test_temp_topics = {
        '/fmu/in/vehicle_command': (VehicleCommand, 'px4_vehicle_command_adapter'),
        '/fmu/in/offboard_control_mode': (OffboardControlMode, 'px4_offboard_setpoint_adapter'),
        '/fmu/in/trajectory_setpoint': (TrajectorySetpoint, 'px4_offboard_setpoint_adapter'),
    }
    rclpy.init()
    node = rclpy.create_node('m4_7e_rejection_client')
    try:
        qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT)
        for topic, (message_type, _) in test_temp_topics.items():
            node.create_subscription(
                message_type,
                topic,
                lambda msg, topic=topic: test_temp_messages.append(
                    {
                        'topic': topic,
                        'timestamp': int(msg.timestamp),
                    }
                ),
                qos,
            )
        client = node.create_client(AtomicAction, '/takeoff')
        test_temp_deadline = monotonic() + 100
        while monotonic() < test_temp_deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            state = _latest_state(test_temp_sitl_run)
            if (
                client.service_is_ready()
                and state.get('position_valid')
                and state.get('landed')
                and state.get('home_valid')
            ):
                break
        else:
            pytest.fail(f'PX4 地面状态或服务未就绪；日志：{test_temp_sitl_run}')
        assert state['armed'] is False
        _save(test_temp_sitl_run / 'test_temp_state_before.json', state)
        test_temp_publishers = {
            topic: [info.node_name for info in node.get_publishers_info_by_topic(topic)]
            for topic in test_temp_topics
        }
        _save(test_temp_sitl_run / 'test_temp_publishers.json', test_temp_publishers)
        for topic, (_, publisher) in test_temp_topics.items():
            assert publisher in test_temp_publishers[topic]
        speed = asyncio.run(Px4ParameterAdapter().read_validated_speed_limit(max_age_s=2.0))
        _save(test_temp_sitl_run / 'test_temp_px4_speed.json', {'MPC_XY_VEL_MAX': speed})
        test_temp_until = monotonic() + 2
        while monotonic() < test_temp_until:
            rclpy.spin_once(node, timeout_sec=0.1)
        request = AtomicAction.Request(input='{"target_altitude_m":6.0}', prev_output='')
        _save(
            test_temp_sitl_run / 'test_temp_request.json',
            {'service': '/takeoff', 'input': request.input},
        )
        test_temp_started = monotonic()
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=10)
        assert future.done(), 'ROS 动作请求超时'
        response = future.result()
        assert response is not None
        output = json.loads(response.output)
        _save(
            test_temp_sitl_run / 'test_temp_response.json',
            {
                'success': response.success,
                'output': output,
                'elapsed_s': monotonic() - test_temp_started,
            },
        )
        test_temp_until = monotonic() + 2
        while monotonic() < test_temp_until:
            rclpy.spin_once(node, timeout_sec=0.1)
        test_temp_counts = json.loads(
            (test_temp_sitl_run / 'test_temp_backend_calls.json').read_text()
        )
        test_temp_records = [
            json.loads(line)
            for line in (test_temp_sitl_run / 'safety.jsonl').read_text().splitlines()
        ]
        assert response.success is False
        assert output['reason_codes'] == ['ALTITUDE_ABOVE_ENVELOPE']
        assert output['error_code'] is None and output['execution_id'] is None
        assert test_temp_counts['state_reads'] >= 2 and test_temp_counts['execute_calls'] == 0
        assert test_temp_messages == []
        (test_temp_record,) = test_temp_records
        assert test_temp_record['decision']['decision'] == 'REJECT'
        assert test_temp_record['decision']['decision_id'] == output['decision_id']
        assert test_temp_record['proposal']['proposal_id'] == output['proposal_id']
        _save(
            test_temp_sitl_run / 'test_temp_result.json',
            {
                'passed': True,
                'backend_calls': test_temp_counts,
                'command_count': len(test_temp_messages),
            },
        )
    finally:
        _save(test_temp_sitl_run / 'test_temp_command_messages.json', test_temp_messages)
        node.destroy_node()
        rclpy.shutdown()


@pytest.fixture
def test_temp_sitl_run() -> Iterator[Path]:
    '''启动实际仿真和 server，保留证据，并在用例失败时也清理本轮进程。'''
    test_temp_run = ROOT / 'artifacts/m4-7e' / datetime.now(UTC).strftime('pytest-%Y%m%dT%H%M%S%fZ')
    test_temp_run.mkdir(parents=True)
    test_temp_settings = json.loads((ROOT / 'configs/flight_safety.example.json').read_text())
    test_temp_settings.update(max_altitude_m=5.0, max_mission_radius_m=10.0)
    test_temp_settings['geofence'].update(
        north_min_m=-10.0, north_max_m=10.0, east_min_m=-10.0, east_max_m=10.0
    )
    _save(test_temp_run / 'test_temp_safety.json', test_temp_settings)
    test_temp_env = os.environ.copy()
    test_temp_env.update(
        FLIGHT_AGENT_TRACE_PATH=str(test_temp_run / 'world.jsonl'), GZ_PARTITION=test_temp_run.name
    )
    _save(
        test_temp_run / 'test_temp_manifest.json',
        {
            'code': subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True
            ).strip(),
            'diff_sha256': hashlib.sha256(
                subprocess.check_output(['git', 'diff'], cwd=ROOT)
            ).hexdigest(),
            'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'px4': subprocess.check_output(
                [
                    'git',
                    '-C',
                    test_temp_env.get('PX4_ROOT', str(Path.home() / 'PX4-Autopilot')),
                    'rev-parse',
                    'HEAD',
                ],
                text=True,
            ).strip(),
            'python': sys.version,
            'ros_distro': test_temp_env.get('ROS_DISTRO'),
            'authorization': 'V1 controlled SITL assumes operator authorization',
            'scenario': 'Takeoff 6m exceeds system limit 5m; rejection only',
        },
    )
    print(f'\nSITL evidence: {test_temp_run}', flush=True)
    test_temp_processes: list[subprocess.Popen[bytes]] = []
    try:
        for test_temp_name, test_temp_command in [
            ('simulation', [str(ROOT / 'scripts/ros2_px4/start_simulation.sh')]),
            ('server', [sys.executable, str(Path(__file__).resolve()), str(test_temp_run)]),
        ]:
            with (test_temp_run / f'test_temp_{test_temp_name}.log').open('wb') as test_temp_log:
                test_temp_processes.append(
                    subprocess.Popen(
                        test_temp_command,
                        cwd=ROOT,
                        env=test_temp_env,
                        stdin=subprocess.PIPE,
                        stdout=test_temp_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
        yield test_temp_run
    finally:
        for test_temp_process in reversed(test_temp_processes):
            _stop(test_temp_process)
        _save(
            test_temp_run / 'test_temp_processes.json',
            [
                {'pid': test_temp_process.pid, 'returncode': test_temp_process.returncode}
                for test_temp_process in test_temp_processes
            ],
        )
        _save(
            test_temp_run / 'test_temp_sha256.json',
            {
                test_temp_path.name: hashlib.sha256(test_temp_path.read_bytes()).hexdigest()
                for test_temp_path in test_temp_run.iterdir()
                if test_temp_path.is_file()
            },
        )


def _save(test_temp_path: Path, test_temp_value: object) -> None:
    '''保存可核对的运行证据。'''
    test_temp_path.write_text(json.dumps(test_temp_value, indent=2) + '\n')


def _latest_state(test_temp_run: Path) -> dict[str, object]:
    '''读取 server 保存的最新状态，忽略正在写入的半行。'''
    test_temp_path = test_temp_run / 'world.jsonl'
    if test_temp_path.exists():
        for line in reversed(test_temp_path.read_text().splitlines()):
            try:
                return json.loads(line)['state']
            except json.JSONDecodeError:
                continue
    return {}


def _stop(test_temp_process: subprocess.Popen[bytes]) -> None:
    '''只停止本用例创建的进程组，超时后升级退出信号。'''
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(test_temp_process.pid, sig)
            except ProcessLookupError:
                break
            try:
                test_temp_process.wait(timeout=7)
                break
            except subprocess.TimeoutExpired:
                continue
        test_temp_process.wait(timeout=7)
    finally:
        if test_temp_process.stdin is not None:
            test_temp_process.stdin.close()


def _server(test_temp_run: Path) -> None:
    '''观察真实后端函数进入次数，然后调用实际 main，不替换对象或函数。'''
    from flight_agent_ros import flight_atomic_action_server as entry
    from flight_agent_ros.adapters.flight_execution_backend import Px4Ros2FlightExecutionBackend

    test_temp_counts = {'state_reads': 0, 'execute_calls': 0}
    test_temp_sources = {
        'server_source': entry.__file__,
        'backend_source': sys.modules[Px4Ros2FlightExecutionBackend.__module__].__file__,
    }
    test_temp_codes = {
        Px4Ros2FlightExecutionBackend.get_world_state.__code__: 'state_reads',
        Px4Ros2FlightExecutionBackend.execute.__code__: 'execute_calls',
    }

    def test_temp_record_backend_call(frame: FrameType, event: str, arg: object) -> None:
        '''只记录目标函数调用，不改变执行结果。'''
        if event == 'call' and frame.f_code in test_temp_codes:
            test_temp_key = test_temp_codes[frame.f_code]
            test_temp_counts[test_temp_key] += 1
            _save(
                test_temp_run / 'test_temp_backend_calls.json', test_temp_counts | test_temp_sources
            )

    _save(test_temp_run / 'test_temp_backend_calls.json', test_temp_counts | test_temp_sources)
    sys.argv = [
        'flight_atomic_action_server',
        '--safety-json',
        str(test_temp_run / 'test_temp_safety.json'),
        '--trace-path',
        str(test_temp_run / 'safety.jsonl'),
    ]
    sys.setprofile(test_temp_record_backend_call)
    threading.setprofile(test_temp_record_backend_call)
    try:
        entry.main()
    finally:
        threading.setprofile(None)
        sys.setprofile(None)
        _save(test_temp_run / 'test_temp_backend_calls.json', test_temp_counts | test_temp_sources)


if __name__ == '__main__':
    _server(Path(sys.argv[1]))
