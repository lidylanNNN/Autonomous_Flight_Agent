'''从干净安装产物验证五个独立进程；模型固定，外部飞行边界使用替身。'''

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import pytest
import rclpy
from ament_index_python.packages import get_package_prefix
from rosllm_srvs.srv import LLM, AtomicAction, ExecuteBehavior, Observation

from flight_agent.components.tracing import TraceReplay
from flight_agent.contracts import GlobalPosition, Vector3, WorldState


@pytest.mark.skipif(not os.environ.get('TEST_TEMP_CLEAN_INSTALL'), reason='需显式指定干净安装目录')
def test_temp_installed_entrypoints_complete_human_round(tmp_path: Path) -> None:
    '''验证安装入口、跨进程服务发现、人工轮次、Gate Trace 和观察更新。'''
    test_temp_root = Path(__file__).resolve().parents[4]
    test_temp_install = Path(os.environ['TEST_TEMP_CLEAN_INSTALL']).resolve()
    test_temp_sources = {
        name: importlib.util.find_spec(name).origin
        for name in ('agent_comm', 'behavior_executor', 'observation_manager', 'flight_agent_ros')
    }
    assert all(Path(path).is_relative_to(test_temp_install) for path in test_temp_sources.values())
    test_temp_run = tmp_path
    state = WorldState(
        state_id='test-airborne', source_timestamp_us=100, received_at=datetime.now(UTC),
        state_age_ms=0, position_ned_m=Vector3(x=1, y=1, z=-2),
        velocity_ned_mps=Vector3(x=0, y=0, z=0), battery_percent=80,
        armed=True, landed=False, flight_mode='AUTO_LOITER', position_valid=True,
        home_valid=True, home_position_ned_m=Vector3(x=0, y=0, z=0),
        home_position_wgs84=GlobalPosition(latitude_deg=47, longitude_deg=8, altitude_amsl_m=500),
        link_healthy=True, health_flags={
            'battery_connected': True, 'battery_valid': True, 'battery_fault_free': True,
            'failure_detector_clear': True, 'preflight_checks_pass': True,
        },
    )
    (test_temp_run / 'test_temp_state.json').write_text(state.model_dump_json())
    (test_temp_run / 'safety.json').write_bytes(
        (test_temp_root / 'configs/flight_safety.example.json').read_bytes()
    )
    test_temp_env = os.environ.copy()
    test_temp_env.pop('FLIGHT_LLM_API_KEY', None)
    test_temp_env.update(TEST_TEMP_STATE_PATH=str(test_temp_run / 'test_temp_state.json'),
                         ROS_LOG_DIR=str(test_temp_run / 'test_temp_ros_logs'))
    test_temp_processes: dict[str, subprocess.Popen[str]] = {}
    test_temp_commands = {}
    test_temp_wrapper = Path(__file__).with_name('process_boundary_fixture.py')
    rclpy.init()
    node = rclpy.create_node('test_temp_process_probe')
    try:
        with ExitStack() as test_temp_stack:
            for test_temp_role, test_temp_package, test_temp_entry, test_temp_arguments in [
                ('llm', 'agent_comm', 'llm_node', ['--ros-args', '-p', 'mx_fixed_behavior:=true']),
                ('observation', 'observation_manager', 'observation_manager_node', []),
                ('sequence', 'behavior_executor', 'sequence_executor', []),
                ('flight', 'flight_agent_ros', 'flight_atomic_action_server', [
                    '--safety-json', str(test_temp_run / 'safety.json'),
                    '--trace-path', str(test_temp_run / 'safety.jsonl'),
                ]),
                ('agent', 'agent_comm', 'ros_agent_srv', [
                    '--ros-args', '-p', f'agent_trace_path:={test_temp_run / "agent.jsonl"}',
                ]),
            ]:
                test_temp_prefix = Path(get_package_prefix(test_temp_package))
                assert test_temp_prefix.parent == test_temp_install
                test_temp_executable = test_temp_prefix / 'lib' / test_temp_package / test_temp_entry
                assert test_temp_executable.is_file()
                test_temp_command = [str(test_temp_executable), *test_temp_arguments]
                if test_temp_role in {'flight', 'observation'}:
                    test_temp_command = [
                        '/usr/bin/python3', str(test_temp_wrapper), test_temp_role,
                        str(test_temp_executable), str(test_temp_run), *test_temp_arguments,
                    ]
                test_temp_commands[test_temp_role] = test_temp_command
                test_temp_log = test_temp_stack.enter_context(
                    (test_temp_run / f'test_temp_{test_temp_role}.log').open('w')
                )
                test_temp_processes[test_temp_role] = subprocess.Popen(
                    test_temp_command, env=test_temp_env, stdin=subprocess.PIPE,
                    stdout=test_temp_log, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                )
                test_temp_stack.callback(test_temp_stop, test_temp_processes[test_temp_role])
                if test_temp_role == 'flight':
                    for test_input_type, test_input_service in [
                        (LLM, 'call_llm'), (Observation, 'get_observation'),
                        (ExecuteBehavior, 'execute_behavior'),
                        *[(AtomicAction, name) for name in ('hold', 'takeoff', 'goto', 'rtl', 'land')],
                    ]:
                        client = node.create_client(test_input_type, test_input_service)
                        assert client.wait_for_service(timeout_sec=10), test_input_service
                        node.destroy_client(client)
            test_temp_agent = test_temp_processes['agent']
            test_temp_agent.communicate('执行固定两步\n', timeout=20)
            assert test_temp_agent.returncode == 0
            assert len({item.pid for item in test_temp_processes.values()}) == 5
            assert all(item.poll() is None for role, item in test_temp_processes.items()
                       if role != 'agent')
            observations = tuple(TraceReplay(test_temp_run / 'agent.jsonl').iter_observation_records())
            reply, = TraceReplay(test_temp_run / 'agent.jsonl').iter_llm_response_records()
            assert reply.success and reply.info == 'MX_FIXED_BEHAVIOR: hold,rtl'
            assert len(observations) == 2 and all(item.success for item in observations)
            assert observations[0].observation in reply.prompt
            assert 'HumanInput: 执行固定两步' in reply.prompt
            assert json.loads(observations[1].observation.splitlines()[-1])['flight_mode'] == 'AUTO_RTL'
            results = tuple(TraceReplay(test_temp_run / 'safety.jsonl').iter_skill_result_records())
            assert [item.command.skill_name.value for item in results] == ['hold', 'rtl']
            assert all(item.result.status.value == 'SUCCEEDED' for item in results)
            test_temp_terminal = (test_temp_run / 'test_temp_agent.log').read_text()
            assert test_temp_terminal.count('任务> ') == 2
            assert '动作序列执行成功' in test_temp_terminal and 'INCONCLUSIVE' in test_temp_terminal
            for test_temp_role in ('flight', 'observation'):
                test_temp_source = json.loads(
                    (test_temp_run / f'test_temp_{test_temp_role}_source.json').read_text()
                )
                assert Path(test_temp_source['entry_source']).is_relative_to(test_temp_install)
    finally:
        node.destroy_node()
        rclpy.shutdown()
        (test_temp_run / 'test_temp_manifest.json').write_text(json.dumps({
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'boundary_sha256': hashlib.sha256(test_temp_wrapper.read_bytes()).hexdigest(),
            'environment_sha256': hashlib.sha256(
                (test_temp_root / 'manifests/environment.yaml').read_bytes()
            ).hexdigest(),
            'safety_sha256': hashlib.sha256((test_temp_run / 'safety.json').read_bytes()).hexdigest(),
            'sources': test_temp_sources,
            'install': str(test_temp_install), 'commands': test_temp_commands,
            'processes': {role: {'pid': item.pid, 'returncode': item.returncode}
                          for role, item in test_temp_processes.items()},
            'substitutions': ['fixed_llm', 'file_world_state', 'mock_backend', 'fixed_speed_limit'],
            'ros_domain_id': os.environ.get('ROS_DOMAIN_ID'),
        }, indent=2))
        print(f'Evidence: {test_temp_run}')


def test_temp_stop(test_temp_process: subprocess.Popen[str]) -> None:
    '''只清理本用例启动的进程组，并回收 stdin 与退出状态。'''
    if test_temp_process.poll() is None:
        os.killpg(test_temp_process.pid, signal.SIGINT)
        try:
            test_temp_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(test_temp_process.pid, signal.SIGKILL)
            test_temp_process.wait(timeout=5)
    if test_temp_process.stdin is not None:
        test_temp_process.stdin.close()


test_temp_stop.__test__ = False
