'''通过实际 ROS 服务验证 PX4/Gazebo 拒绝、起降、定时悬停与局部坐标移动；显式使用 -m sitl 运行。'''

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from time import monotonic
from types import FrameType
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from rclpy.node import Node

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.sitl


@pytest.mark.parametrize(('test_input_case', 'expected_output_case'), [
    pytest.param(
        {'milestone': 'm4-7e',
         'description': 'Takeoff 6m exceeds system limit 5m; rejection only',
         'px4': {},
         'initial_state': {'position_valid': True,
                           'home_valid': True,
                           'landed': True,
                           'armed': False},
         'steps': [{'service': '/takeoff',
                    'input': {'target_altitude_m': 6.0},
                    'timeout_s': 10.0,
                    'record_name': '',
                    'ready': {}}]},
        {'topic_counts': {'/fmu/in/offboard_control_mode': 0, '/fmu/in/trajectory_setpoint': 0},
         'steps': [{'success': False,
                    'decision': 'REJECT',
                    'output': {'reason_codes': ['ALTITUDE_ABOVE_ENVELOPE'],
                               'error_code': None,
                               'execution_status': None,
                               'execution_id': None,
                               'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': False, 'landed': True},
                    'commands': []}]},
        id='7e-takeoff-above-limit',
    ),
    pytest.param(
        {'milestone': 'm4-7f',
         'description': 'Takeoff 3m then Land via actual services',
         'px4': {'speed_limit_mps': 8.0, 'heartbeat_hz': 10},
         'initial_state': {'position_valid': True,
                           'home_valid': True,
                           'landed': True,
                           'armed': False},
         'steps': [{'service': '/takeoff',
                    'input': {'target_altitude_m': 3.0},
                    'timeout_s': 100.0,
                    'record_name': 'takeoff',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199}}},
                   {'service': '/land',
                    'input': {},
                    'timeout_s': 100.0,
                    'record_name': 'land',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'AUTO_LOITER'}}]},
        {'topic_counts': {'/fmu/in/offboard_control_mode': 0, '/fmu/in/trajectory_setpoint': 0},
         'steps': [{'success': True,
                    'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'],
                               'error_code': None,
                               'execution_status': 'SUCCEEDED',
                               'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True,
                              'landed': False,
                              'position_ned_m.z': {'min': -3.5, 'max': -2.5}},
                    'commands': [22, 400]},
                   {'success': True,
                    'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'],
                               'error_code': None,
                               'execution_status': 'SUCCEEDED',
                               'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': False, 'landed': True},
                    'commands': [21]}]},
        id='7f-takeoff-land',
    ),
    pytest.param(
        {'milestone': 'm4-7g-1',
         'description': 'Takeoff 3m, Hold 5s in stable air, then Land via actual services',
         'px4': {'speed_limit_mps': 8.0, 'heartbeat_hz': 10},
         'initial_state': {'position_valid': True, 'home_valid': True,
                           'landed': True, 'armed': False},
         'steps': [{'service': '/takeoff', 'input': {'target_altitude_m': 3.0},
                    'timeout_s': 100.0, 'record_name': 'takeoff',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199}}},
                   {'service': '/hold', 'input': {'duration_s': 5.0},
                    'timeout_s': 40.0, 'record_name': 'hold',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'AUTO_LOITER', 'armed': True, 'landed': False,
                              'position_ned_m.z': {'min': -3.2, 'max': -2.8},
                              'velocity_ned_mps.x': {'min': -0.1, 'max': 0.1},
                              'velocity_ned_mps.y': {'min': -0.1, 'max': 0.1},
                              'velocity_ned_mps.z': {'min': -0.1, 'max': 0.1}}},
                   {'service': '/land', 'input': {}, 'timeout_s': 100.0,
                    'record_name': 'land',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'OFFBOARD'}}]},
        {'topic_counts': {'/fmu/in/offboard_control_mode': {'min': 50, 'max': 300},
                          '/fmu/in/trajectory_setpoint': {'min': 50, 'max': 300}},
         'steps': [{'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True, 'landed': False,
                              'position_ned_m.z': {'min': -3.5, 'max': -2.5}},
                    'commands': [22, 400]},
                   {'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True, 'landed': False, 'flight_mode': 'OFFBOARD'},
                    'commands': [176],
                    'hold_evidence': {'duration_s': 5.0, 'position_tolerance_m': 0.5,
                                      'max_sample_gap_s': 0.5}},
                   {'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': False, 'landed': True}, 'commands': [21]}]},
        id='7g-1-takeoff-hold-land',
    ),
    pytest.param(
        {'milestone': 'm4-7g-2',
         'description': 'Takeoff 3m, GoTo N3 E2 altitude 3m, then Land via actual services',
         'px4': {'speed_limit_mps': 8.0, 'heartbeat_hz': 10},
         'initial_state': {'position_valid': True, 'home_valid': True,
                           'landed': True, 'armed': False},
         'steps': [{'service': '/takeoff', 'input': {'target_altitude_m': 3.0},
                    'timeout_s': 100.0, 'record_name': 'takeoff',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199}}},
                   {'service': '/goto',
                    'input': {'north_m': 3.0, 'east_m': 2.0, 'altitude_m': 3.0,
                              'acceptance_radius_m': 0.5},
                    'timeout_s': 40.0, 'record_name': 'goto',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'AUTO_LOITER', 'armed': True, 'landed': False,
                              'position_ned_m.z': {'min': -3.2, 'max': -2.8},
                              'velocity_ned_mps.x': {'min': -0.1, 'max': 0.1},
                              'velocity_ned_mps.y': {'min': -0.1, 'max': 0.1},
                              'velocity_ned_mps.z': {'min': -0.1, 'max': 0.1}}},
                   {'service': '/land', 'input': {}, 'timeout_s': 100.0,
                    'record_name': 'land',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'OFFBOARD'}}]},
        {'topic_counts': {'/fmu/in/offboard_control_mode': {'min': 20, 'max': 600},
                          '/fmu/in/trajectory_setpoint': {'min': 20, 'max': 600}},
         'steps': [{'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True, 'landed': False,
                              'position_ned_m.z': {'min': -3.5, 'max': -2.5}},
                    'commands': [22, 400]},
                   {'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True, 'landed': False, 'flight_mode': 'OFFBOARD'},
                    'commands': [176],
                    'goto_evidence': {'target_ned_m': [3.0, 2.0, -3.0],
                                      'horizontal_tolerance_m': 0.5,
                                      'vertical_tolerance_m': 0.5,
                                      'min_displacement_m': 2.5, 'max_sample_gap_s': 0.5}},
                   {'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': False, 'landed': True}, 'commands': [21]}]},
        id='7g-2-takeoff-goto-land',
    ),
    pytest.param(
        {'milestone': 'm4-7g-3',
         'description': 'Takeoff 3m, GoTo N3 E2, then RTL to original Home and land',
         'px4': {'speed_limit_mps': 8.0, 'heartbeat_hz': 10,
                 'rtl_parameters': {'RTL_RETURN_ALT': 3.0, 'RTL_DESCEND_ALT': 2.0,
                                    'RTL_LAND_DELAY': 0.0, 'RTL_TYPE': 0, 'RTL_CONE_ANG': 0}},
         'initial_state': {'position_valid': True, 'home_valid': True,
                           'landed': True, 'armed': False},
         'steps': [{'service': '/takeoff', 'input': {'target_altitude_m': 3.0},
                    'timeout_s': 100.0, 'record_name': 'takeoff',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199}}},
                   {'service': '/goto',
                    'input': {'north_m': 3.0, 'east_m': 2.0, 'altitude_m': 3.0,
                              'acceptance_radius_m': 0.5},
                    'timeout_s': 40.0, 'record_name': 'goto',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'AUTO_LOITER', 'armed': True, 'landed': False,
                              'position_ned_m.z': {'min': -3.2, 'max': -2.8},
                              'velocity_ned_mps.x': {'min': -0.1, 'max': 0.1},
                              'velocity_ned_mps.y': {'min': -0.1, 'max': 0.1},
                              'velocity_ned_mps.z': {'min': -0.1, 'max': 0.1}}},
                   {'service': '/rtl', 'input': {}, 'timeout_s': 140.0,
                    'record_name': 'rtl',
                    'ready': {'state_age_ms': {'min': 0, 'max': 199},
                              'flight_mode': 'OFFBOARD', 'home_valid': True,
                              'armed': True, 'landed': False,
                              'position_ned_m.x': {'min': 2.5, 'max': 3.5},
                              'position_ned_m.y': {'min': 1.5, 'max': 2.5}}}]},
        {'topic_counts': {'/fmu/in/offboard_control_mode': {'min': 20, 'max': 600},
                          '/fmu/in/trajectory_setpoint': {'min': 20, 'max': 600}},
         'steps': [{'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True, 'landed': False,
                              'position_ned_m.z': {'min': -3.5, 'max': -2.5}},
                    'commands': [22, 400]},
                   {'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': True, 'landed': False, 'flight_mode': 'OFFBOARD'},
                    'commands': [176],
                    'goto_evidence': {'target_ned_m': [3.0, 2.0, -3.0],
                                      'horizontal_tolerance_m': 0.5,
                                      'vertical_tolerance_m': 0.5,
                                      'min_displacement_m': 2.5, 'max_sample_gap_s': 0.5}},
                   {'success': True, 'decision': 'APPROVE',
                    'output': {'reason_codes': ['CHECKS_PASSED'], 'error_code': None,
                               'execution_status': 'SUCCEEDED', 'failure_code': None,
                               'verification': 'INCONCLUSIVE'},
                    'state': {'armed': False, 'landed': True}, 'commands': [20],
                    'rtl_evidence': {'min_start_distance_m': 2.5,
                                     'max_home_error_m': 1.0, 'max_sample_gap_s': 0.5}}]},
        id='7g-3-takeoff-goto-rtl',
    ),
])
def test_action_sitl(
    test_input_case: dict[str, Any], expected_output_case: dict[str, Any], test_temp_sitl_run: Path,
) -> None:
    '''按 test_input_ 输入执行动作，以 expected_output_ 预期核对实际证据。'''
    import rclpy
    from px4_msgs.msg import (
        OffboardControlMode,
        TrajectorySetpoint,
        VehicleCommand,
        VehicleCommandAck,
        VehicleLocalPosition,
    )

    test_temp_messages: list[dict[str, Any]] = []
    test_temp_topics = {
        '/fmu/in/vehicle_command': (VehicleCommand, 'px4_vehicle_command_adapter'),
        '/fmu/in/offboard_control_mode': (OffboardControlMode, 'px4_offboard_setpoint_adapter'),
        '/fmu/in/trajectory_setpoint': (TrajectorySetpoint, 'px4_offboard_setpoint_adapter'),
    }
    rclpy.init()
    node = rclpy.create_node('sitl_action_client')
    try:
        _subscribe_messages(node, {
            **{topic: item[0] for topic, item in test_temp_topics.items()},
            '/fmu/out/vehicle_command_ack': VehicleCommandAck,
            '/fmu/out/vehicle_local_position': VehicleLocalPosition,
        }, test_temp_messages)
        state = _wait_state(node, test_temp_sitl_run,
                            lambda state: _matches(state, test_input_case['initial_state']))
        _save(test_temp_sitl_run / 'test_temp_state_before.json', state)
        test_temp_publishers = {
            topic: [info.node_name for info in node.get_publishers_info_by_topic(topic)]
            for topic in test_temp_topics
        }
        _save(test_temp_sitl_run / 'test_temp_publishers.json', test_temp_publishers)
        for topic, (_, publisher) in test_temp_topics.items():
            assert publisher in test_temp_publishers[topic]
        with (_px4_settings(test_temp_sitl_run, test_input_case['px4']),
              _px4_rtl_settings(test_temp_sitl_run, test_input_case['px4'].get('rtl_parameters', {}))):
            _spin_for(node, 2.0)
            test_temp_record_index = 0
            test_temp_execution_ids: list[str] = []
            for test_input_step, expected_output_step in zip(
                test_input_case['steps'], expected_output_case['steps'], strict=True,
            ):
                _wait_state(node, test_temp_sitl_run, lambda state, step=test_input_step: _matches(
                    state, step['ready']
                ), timeout_s=30.0)
                test_temp_start = len(test_temp_messages)
                success, output, state = _call_action(
                    node, test_temp_sitl_run, test_input_service=test_input_step['service'],
                    test_input_arguments=test_input_step['input'], test_input_record_name=test_input_step['record_name'],
                    test_input_timeout_s=test_input_step['timeout_s'],
                )
                assert success is expected_output_step['success'], output
                assert _matches(output, expected_output_step['output']), output
                assert _matches(state, expected_output_step['state']), state
                _spin_for(node, 2.0)
                test_temp_records = [json.loads(line) for line in
                                     (test_temp_sitl_run / 'safety.jsonl').read_text().splitlines()]
                test_temp_decision_record = test_temp_records[test_temp_record_index]
                assert test_temp_decision_record['type'] == 'safety_decision'
                decision = test_temp_decision_record['decision']
                proposal = test_temp_decision_record['proposal']
                assert decision['decision'] == expected_output_step['decision']
                assert decision['decision_id'] == output['decision_id']
                assert decision['proposal_id'] == proposal['proposal_id'] == output['proposal_id']
                assert proposal['skill_name'] == test_input_step['service'].lstrip('/')
                assert proposal['arguments'] == test_input_step['input']
                test_temp_record_index += 1
                if expected_output_step['decision'] == 'APPROVE':
                    test_temp_result_record = test_temp_records[test_temp_record_index]
                    assert test_temp_result_record['type'] == 'skill_result'
                    command, result = test_temp_result_record['command'], test_temp_result_record['result']
                    assert command['decision_id'] == output['decision_id']
                    assert command['proposal_id'] == result['proposal_id'] == output['proposal_id']
                    assert command['execution_id'] == result['execution_id'] == output['execution_id']
                    assert result['status'] == output['execution_status']
                    assert result['px4_ack'] == 'ACCEPTED'
                    test_temp_events = json.loads(
                        (test_temp_sitl_run / 'test_temp_execution_entries.json').read_text()
                    )
                    test_temp_event = next(item for item in test_temp_events if item['command'] == command)
                    assert decision['decision_id'] in test_temp_event['recorded_decisions']
                    test_temp_execution_ids.append(command['execution_id'])
                    test_temp_record_index += 1
                if 'hold_evidence' in expected_output_step:
                    _check_hold_evidence(
                        test_temp_sitl_run, test_temp_result_record,
                        test_temp_messages[test_temp_start:], expected_output_step['hold_evidence'],
                    )
                if 'goto_evidence' in expected_output_step:
                    _check_goto_evidence(
                        test_temp_sitl_run, test_temp_result_record,
                        test_temp_messages[test_temp_start:], expected_output_step['goto_evidence'],
                    )
                if 'rtl_evidence' in expected_output_step:
                    _check_rtl_evidence(
                        test_temp_sitl_run, test_temp_result_record,
                        test_temp_messages[test_temp_start:], expected_output_step['rtl_evidence'],
                    )
                test_temp_commands = [item for item in test_temp_messages[test_temp_start:]
                                      if item['topic'] == '/fmu/in/vehicle_command']
                assert [item['message']['command'] for item in test_temp_commands] == expected_output_step['commands']
                for item in test_temp_commands:
                    assert test_temp_decision_record['recorded_at'] < item['received_at']
                    assert any(ack['message']['command'] == item['message']['command']
                               and ack['message']['result'] == 0
                               and ack['received_at'] >= item['received_at']
                               for ack in test_temp_messages[test_temp_start:]
                               if ack['topic'] == '/fmu/out/vehicle_command_ack')
            assert _matches({
                topic: sum(item['topic'] == topic for item in test_temp_messages)
                for topic in expected_output_case['topic_counts']
            }, expected_output_case['topic_counts'])
            assert len(test_temp_records) == test_temp_record_index
            test_temp_counts = json.loads((test_temp_sitl_run / 'test_temp_backend_calls.json').read_text())
            assert test_temp_counts['state_reads'] >= 2 * len(test_input_case['steps'])
            if not test_temp_execution_ids:
                assert test_temp_counts['execute_calls'] == 0
                assert not any(item['topic'] in test_temp_topics for item in test_temp_messages)
            else:
                assert {item['command']['execution_id'] for item in test_temp_events} == set(test_temp_execution_ids)
            _save(test_temp_sitl_run / 'test_temp_result.json', {
                'passed': True, 'backend_calls': test_temp_counts, 'state': state,
                'command_count': sum(item['topic'] == '/fmu/in/vehicle_command'
                                     for item in test_temp_messages),
            })
    finally:
        _save(test_temp_sitl_run / 'test_temp_command_messages.json', test_temp_messages)
        node.destroy_node()
        rclpy.shutdown()


def _check_hold_evidence(
    test_temp_run: Path, test_temp_result_record: dict[str, Any],
    test_temp_messages: list[dict[str, Any]], expected_output_hold: dict[str, float],
) -> None:
    '''从真实位置 Trace 与发送记录核对最后一个完整 Hold 窗口，不以响应成功代替。'''
    from math import hypot

    result = test_temp_result_record['result']
    test_temp_begin = datetime.fromisoformat(result['started_at']).timestamp()
    test_temp_end = datetime.fromisoformat(result['ended_at']).timestamp()
    expected_output_duration = expected_output_hold['duration_s']
    expected_output_gap = expected_output_hold['max_sample_gap_s']
    assert test_temp_end - test_temp_begin >= expected_output_duration
    test_temp_setpoints = [item for item in test_temp_messages
                           if item['topic'] == '/fmu/in/trajectory_setpoint'
                           and test_temp_begin <= datetime.fromisoformat(item['received_at'])
                           .timestamp() <= test_temp_end]
    assert test_temp_setpoints
    test_temp_heartbeats = [item for item in test_temp_messages
                            if item['topic'] == '/fmu/in/offboard_control_mode'
                            and test_temp_begin <= datetime.fromisoformat(item['received_at'])
                            .timestamp() <= test_temp_end]
    assert test_temp_heartbeats and all(
        item['message']['position'] for item in test_temp_heartbeats
    )
    test_temp_target = test_temp_setpoints[0]['message']['position']
    assert all(item['message']['position'] == test_temp_target for item in test_temp_setpoints)
    test_temp_window = test_temp_end - expected_output_duration
    test_temp_states = [json.loads(line) for line in
                        (test_temp_run / 'world.jsonl').read_text().splitlines()]
    test_temp_states = [item for item in test_temp_states
                        if test_temp_window <= datetime.fromisoformat(
                            item['recorded_at']).timestamp() <= test_temp_end]
    assert test_temp_states
    for item in test_temp_states:
        assert _matches(item['state'], {
            'armed': True, 'landed': False, 'position_valid': True,
            'flight_mode': 'OFFBOARD', 'failsafe_active': False,
            'health_flags.local_position_fresh': True,
        }), item
    # 原始位置在独立客户端接收，避免把 server 的 Trace 定时器间隔当成位置采样间隔。
    test_temp_samples = [item for item in test_temp_messages
                         if item['topic'] == '/fmu/out/vehicle_local_position'
                         and test_temp_window - expected_output_gap <= datetime.fromisoformat(
                             item['received_at']).timestamp() <= test_temp_end]
    test_temp_times = [datetime.fromisoformat(item['received_at']).timestamp()
                       for item in test_temp_samples]
    test_temp_left = [i for i, value in enumerate(test_temp_times) if value <= test_temp_window]
    assert test_temp_left, 'Hold 窗口缺少起点位置'
    test_temp_samples = test_temp_samples[test_temp_left[-1]:]
    test_temp_times = test_temp_times[test_temp_left[-1]:]
    assert test_temp_end - test_temp_times[-1] <= expected_output_gap
    assert all(0 < after - before <= expected_output_gap
               for before, after in pairwise(test_temp_times))
    test_temp_horizontal_errors, test_temp_vertical_errors = [], []
    test_temp_source_times = []
    for item in test_temp_samples:
        position = item['message']
        assert position['xy_valid'] and position['z_valid'], position
        test_temp_source_times.append(position['timestamp_sample'])
        test_temp_horizontal_errors.append(hypot(position['x'] - test_temp_target[0],
                                                  position['y'] - test_temp_target[1]))
        test_temp_vertical_errors.append(abs(position['z'] - test_temp_target[2]))
    assert all(0 < after - before <= expected_output_gap * 1_000_000
               for before, after in pairwise(test_temp_source_times))
    _save(test_temp_run / 'test_temp_hold_evidence.json', {
        'execution_id': result['execution_id'], 'target_ned_m': test_temp_target,
        'execution_elapsed_s': test_temp_end - test_temp_begin,
        'window_duration_s': expected_output_duration, 'sample_count': len(test_temp_samples),
        'first_sample_at': test_temp_samples[0]['received_at'],
        'last_sample_at': test_temp_samples[-1]['received_at'],
        'max_sample_gap_s': max(after - before for before, after in
                                pairwise(test_temp_times)),
        'max_horizontal_error_m': max(test_temp_horizontal_errors),
        'max_vertical_error_m': max(test_temp_vertical_errors),
    })
    assert max(test_temp_horizontal_errors) <= expected_output_hold['position_tolerance_m']
    assert max(test_temp_vertical_errors) <= expected_output_hold['position_tolerance_m']



def _check_goto_evidence(
    test_temp_run: Path, test_temp_result_record: dict[str, Any],
    test_temp_messages: list[dict[str, Any]], expected_output_goto: dict[str, Any],
) -> None:
    '''核对 GoTo 的目标映射、独立位置采样、实际位移及执行结束时的终点误差。'''
    from math import hypot

    result = test_temp_result_record['result']
    test_temp_begin = datetime.fromisoformat(result['started_at']).timestamp()
    test_temp_end = datetime.fromisoformat(result['ended_at']).timestamp()
    expected_output_target = expected_output_goto['target_ned_m']
    expected_output_gap = expected_output_goto['max_sample_gap_s']
    test_temp_setpoints = [item for item in test_temp_messages
                           if item['topic'] == '/fmu/in/trajectory_setpoint'
                           and test_temp_begin <= datetime.fromisoformat(
                               item['received_at']).timestamp() <= test_temp_end]
    assert test_temp_setpoints
    assert all(item['message']['position'] == expected_output_target
               for item in test_temp_setpoints)
    test_temp_samples = [item for item in test_temp_messages
                         if item['topic'] == '/fmu/out/vehicle_local_position'
                         and test_temp_begin <= datetime.fromisoformat(
                             item['received_at']).timestamp() <= test_temp_end]
    assert len(test_temp_samples) >= 2
    test_temp_times = [datetime.fromisoformat(item['received_at']).timestamp()
                       for item in test_temp_samples]
    assert test_temp_times[0] - test_temp_begin <= expected_output_gap
    assert test_temp_end - test_temp_times[-1] <= expected_output_gap
    assert all(0 < after - before <= expected_output_gap
               for before, after in pairwise(test_temp_times))
    test_temp_source_times = [item['message']['timestamp_sample'] for item in test_temp_samples]
    assert all(0 < after - before <= expected_output_gap * 1_000_000
               for before, after in pairwise(test_temp_source_times))
    assert all(item['message']['xy_valid'] and item['message']['z_valid']
               for item in test_temp_samples)
    test_temp_first = test_temp_samples[0]['message']
    test_temp_last = test_temp_samples[-1]['message']
    test_temp_displacement = hypot(test_temp_last['x'] - test_temp_first['x'],
                                   test_temp_last['y'] - test_temp_first['y'])
    test_temp_horizontal_error = hypot(test_temp_last['x'] - expected_output_target[0],
                                       test_temp_last['y'] - expected_output_target[1])
    test_temp_vertical_error = abs(test_temp_last['z'] - expected_output_target[2])
    _save(test_temp_run / 'test_temp_goto_evidence.json', {
        'execution_id': result['execution_id'], 'target_ned_m': expected_output_target,
        'execution_elapsed_s': test_temp_end - test_temp_begin,
        'sample_count': len(test_temp_samples),
        'first_sample_at': test_temp_samples[0]['received_at'],
        'last_sample_at': test_temp_samples[-1]['received_at'],
        'start_ned_m': [test_temp_first[key] for key in ('x', 'y', 'z')],
        'end_ned_m': [test_temp_last[key] for key in ('x', 'y', 'z')],
        'horizontal_displacement_m': test_temp_displacement,
        'horizontal_error_m': test_temp_horizontal_error,
        'vertical_error_m': test_temp_vertical_error,
        'max_sample_gap_s': max(after - before for before, after in pairwise(test_temp_times)),
    })
    assert test_temp_displacement >= expected_output_goto['min_displacement_m']
    assert test_temp_horizontal_error <= expected_output_goto['horizontal_tolerance_m']
    assert test_temp_vertical_error <= expected_output_goto['vertical_tolerance_m']



def _check_rtl_evidence(
    test_temp_run: Path, test_temp_result_record: dict[str, Any],
    test_temp_messages: list[dict[str, Any]], expected_output_rtl: dict[str, float],
) -> None:
    '''以请求时冻结的 Home 核对异地返航、RTL 模式及着陆上锁证据。'''
    from math import hypot

    request = json.loads((test_temp_run / 'test_temp_rtl_request.json').read_text())
    test_temp_home = request['state']['home_position_ned_m']
    assert request['state']['home_valid']
    result = test_temp_result_record['result']
    test_temp_begin = datetime.fromisoformat(result['started_at']).timestamp()
    test_temp_end = datetime.fromisoformat(result['ended_at']).timestamp()
    expected_output_gap = expected_output_rtl['max_sample_gap_s']
    test_temp_samples = [item for item in test_temp_messages
                         if item['topic'] == '/fmu/out/vehicle_local_position'
                         and test_temp_begin - expected_output_gap <= datetime.fromisoformat(
                             item['received_at']).timestamp() <= test_temp_end]
    test_temp_left = [i for i, item in enumerate(test_temp_samples)
                      if datetime.fromisoformat(item['received_at']).timestamp() <= test_temp_begin]
    assert test_temp_left, 'RTL 缺少执行开始前的位置'
    test_temp_samples = test_temp_samples[test_temp_left[-1]:]
    assert len(test_temp_samples) >= 2
    test_temp_times = [datetime.fromisoformat(item['received_at']).timestamp()
                       for item in test_temp_samples]
    assert test_temp_end - test_temp_times[-1] <= expected_output_gap
    assert all(0 < after - before <= expected_output_gap
               for before, after in pairwise(test_temp_times))
    test_temp_source_times = [item['message']['timestamp_sample'] for item in test_temp_samples]
    assert all(0 < after - before <= expected_output_gap * 1_000_000
               for before, after in pairwise(test_temp_source_times))
    assert all(item['message']['xy_valid'] and item['message']['z_valid']
               for item in test_temp_samples)
    test_temp_first, test_temp_last = (test_temp_samples[i]['message'] for i in (0, -1))
    test_temp_start_distance = hypot(test_temp_first['x'] - test_temp_home['x'],
                                     test_temp_first['y'] - test_temp_home['y'])
    test_temp_home_error = hypot(test_temp_last['x'] - test_temp_home['x'],
                                 test_temp_last['y'] - test_temp_home['y'])
    test_temp_states = [json.loads(line) for line in
                        (test_temp_run / 'world.jsonl').read_text().splitlines()]
    test_temp_states = [item['state'] for item in test_temp_states
                        if test_temp_begin <= datetime.fromisoformat(
                            item['recorded_at']).timestamp() <= test_temp_end]
    assert any(state['flight_mode'] == 'AUTO_RTL' and state['armed']
               and state['landed'] is False for state in test_temp_states)
    assert test_temp_states and not any(state['failsafe_active'] for state in test_temp_states)
    state = json.loads((test_temp_run / 'test_temp_rtl_response.json').read_text())['state']
    assert state['landed'] is True and state['armed'] is False
    _save(test_temp_run / 'test_temp_rtl_evidence.json', {
        'execution_id': result['execution_id'], 'home_ned_m': test_temp_home,
        'start_ned_m': [test_temp_first[key] for key in ('x', 'y', 'z')],
        'end_ned_m': [test_temp_last[key] for key in ('x', 'y', 'z')],
        'start_distance_m': test_temp_start_distance, 'home_error_m': test_temp_home_error,
        'execution_elapsed_s': test_temp_end - test_temp_begin,
        'sample_count': len(test_temp_samples),
        'max_sample_gap_s': max(after - before for before, after in pairwise(test_temp_times)),
        'flight_modes': sorted({state['flight_mode'] for state in test_temp_states}),
    })
    assert test_temp_start_distance >= expected_output_rtl['min_start_distance_m']
    assert test_temp_home_error <= expected_output_rtl['max_home_error_m']



def _matches(actual: dict[str, Any], expected_output_fields: dict[str, Any]) -> bool:
    '''按字段路径比较精确值或闭区间；缺少字段时不满足条件。'''
    for expected_output_path, expected_output_value in expected_output_fields.items():
        found_value: Any = actual
        for test_temp_key in expected_output_path.split('.'):
            if not isinstance(found_value, dict) or test_temp_key not in found_value:
                return False
            found_value = found_value[test_temp_key]
        if isinstance(expected_output_value, dict) and set(expected_output_value) == {'min', 'max'}:
            if not isinstance(found_value, (int, float)) or isinstance(found_value, bool):
                return False
            if not expected_output_value['min'] <= found_value <= expected_output_value['max']:
                return False
        elif found_value != expected_output_value:
            return False
    return True


@contextmanager
def _px4_settings(test_temp_run: Path, test_input_settings: dict[str, Any]) -> Iterator[None]:
    '''按场景临时设置 SITL 限速和心跳，保留独立读数，退出时恢复。'''
    from flight_agent_ros.adapters.px4_parameter_adapter import Px4ParameterAdapter

    px4_root = Path(os.environ.get('PX4_ROOT', str(Path.home() / 'PX4-Autopilot')))
    px4_bin = px4_root / 'build/px4_sitl_default/bin'
    px4_rootfs = px4_root / 'build/px4_sitl_default/rootfs'
    speed_before = asyncio.run(Px4ParameterAdapter().read_validated_speed_limit(max_age_s=2.0))
    try:
        if 'heartbeat_hz' in test_input_settings:
            subprocess.check_output(
                [str(px4_bin / 'px4-mavlink'), 'stream', '-u', '14580', '-s',
                 'HEARTBEAT', '-r', str(test_input_settings['heartbeat_hz'])], cwd=px4_rootfs,
            )
        if 'speed_limit_mps' in test_input_settings:
            subprocess.check_output(
                [str(px4_bin / 'px4-param'), 'set', 'MPC_XY_VEL_MAX',
                 str(test_input_settings['speed_limit_mps'])], cwd=px4_rootfs,
            )
        speed_limit = asyncio.run(Px4ParameterAdapter().read_validated_speed_limit(max_age_s=2.0))
        _save(test_temp_run / 'test_temp_px4_speed.json', {
            'before': speed_before, 'MPC_XY_VEL_MAX': speed_limit,
            'settings': test_input_settings,
        })
        if 'speed_limit_mps' in test_input_settings:
            assert speed_limit == test_input_settings['speed_limit_mps']
        yield
    finally:
        try:
            if 'heartbeat_hz' in test_input_settings:
                subprocess.check_output(
                    [str(px4_bin / 'px4-mavlink'), 'stream', '-u', '14580',
                     '-s', 'HEARTBEAT', '-r', '1'], cwd=px4_rootfs,
                )
        finally:
            if 'speed_limit_mps' in test_input_settings:
                subprocess.check_output(
                    [str(px4_bin / 'px4-param'), 'set', 'MPC_XY_VEL_MAX', str(speed_before)],
                    cwd=px4_rootfs,
                )
                restored_speed_limit = asyncio.run(Px4ParameterAdapter().read_validated_speed_limit(max_age_s=2.0))
                _save(test_temp_run / 'test_temp_parameter_restore.json', {'MPC_XY_VEL_MAX': restored_speed_limit})
                assert restored_speed_limit == speed_before



@contextmanager
def _px4_rtl_settings(
    test_temp_run: Path, test_input_parameters: dict[str, float],
) -> Iterator[None]:
    '''临时设置低空 RTL 参数，逐项读回并在退出时恢复原值，失败也保留记录。'''
    px4_root = Path(os.environ.get('PX4_ROOT', str(Path.home() / 'PX4-Autopilot')))
    test_temp_command = str(px4_root / 'build/px4_sitl_default/bin/px4-param')
    test_temp_rootfs = px4_root / 'build/px4_sitl_default/rootfs'
    test_temp_readings: dict[str, dict[str, float]] = {}

    def test_temp_read(test_input_name: str) -> float:
        '''通过 PX4 参数命令读取当前值。'''
        return float(subprocess.check_output(
            [test_temp_command, 'show', '-q', test_input_name], cwd=test_temp_rootfs, text=True,
        ).strip())

    def test_temp_set(test_input_name: str, test_input_value: float) -> None:
        '''设置并读回，避免仅以命令返回成功作为配置生效证据。'''
        subprocess.check_output(
            [test_temp_command, 'set', test_input_name, str(test_input_value)], cwd=test_temp_rootfs,
        )
        assert test_temp_read(test_input_name) == test_input_value

    def test_temp_restore(test_input_name: str, test_input_value: float) -> None:
        '''恢复单个参数并保存实际回读值。'''
        test_temp_set(test_input_name, test_input_value)
        test_temp_readings[test_input_name]['restored'] = test_temp_read(test_input_name)

    try:
        with ExitStack() as test_temp_stack:
            for test_input_name, test_input_value in test_input_parameters.items():
                test_temp_before = test_temp_read(test_input_name)
                test_temp_readings[test_input_name] = {'before': test_temp_before}
                test_temp_stack.callback(test_temp_restore, test_input_name, test_temp_before)
                test_temp_set(test_input_name, test_input_value)
                test_temp_readings[test_input_name]['during'] = test_temp_read(test_input_name)
            yield
    finally:
        if test_input_parameters:
            _save(test_temp_run / 'test_temp_rtl_parameters.json', test_temp_readings)



def _subscribe_messages(
    node: Node, test_temp_topics: dict[str, type],
    test_temp_messages: list[dict[str, object]],
) -> None:
    '''按传入 Topic 和消息类型订阅，记录完整消息与接收时间。'''
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from rosidl_runtime_py.convert import message_to_ordereddict

    qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT)
    for topic, message_type in test_temp_topics.items():
        node.create_subscription(
            message_type, topic,
            lambda msg, topic=topic: test_temp_messages.append({
                'topic': topic, 'received_at': datetime.now(UTC).isoformat(),
                'message': message_to_ordereddict(msg),
            }), qos,
        )


def _wait_state(
    node: Node, test_temp_run: Path,
    test_temp_ready: Callable[[dict[str, Any]], bool], *, timeout_s: float = 100.0,
) -> dict[str, Any]:
    '''处理 ROS 回调，返回满足条件的最新状态，超时则使测试失败。'''
    import rclpy

    test_temp_deadline = monotonic() + timeout_s
    while monotonic() < test_temp_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        state = _latest_state(test_temp_run)
        if test_temp_ready(state):
            return state
    pytest.fail(f'等待状态条件超时；日志：{test_temp_run}')


def _spin_for(node: Node, duration_s: float) -> None:
    '''在指定观察窗口内处理 ROS 消息。'''
    import rclpy

    test_temp_until = monotonic() + duration_s
    while monotonic() < test_temp_until:
        rclpy.spin_once(node, timeout_sec=0.1)


def _call_action(
    node: Node, test_temp_run: Path, *, test_input_service: str, test_input_arguments: dict[str, Any],
    test_input_record_name: str, test_input_timeout_s: float,
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    '''按服务名和输入调用实际动作，保存请求、响应与状态，不预设成功或拒绝。'''
    import rclpy
    from rosllm_srvs.srv import AtomicAction

    client = node.create_client(AtomicAction, test_input_service)
    test_temp_prefix = f'test_temp_{test_input_record_name}_' if test_input_record_name else 'test_temp_'
    try:
        assert client.wait_for_service(timeout_sec=10.0), f'{test_input_service} 服务未就绪'
        request = AtomicAction.Request(input=json.dumps(test_input_arguments), prev_output='')
        _save(test_temp_run / f'{test_temp_prefix}request.json', {
            'service': test_input_service, 'input': request.input, 'prev_output': request.prev_output,
            'state': _latest_state(test_temp_run),
        })
        test_temp_started = monotonic()
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=test_input_timeout_s)
        assert future.done(), f'{test_input_service} ROS 请求超时'
        response = future.result()
        assert response is not None
        output = json.loads(response.output)
        state = _latest_state(test_temp_run)
        _save(test_temp_run / f'{test_temp_prefix}response.json', {
            'success': response.success, 'output': output, 'state': state,
            'elapsed_s': monotonic() - test_temp_started,
        })
        return response.success, output, state
    finally:
        node.destroy_client(client)


@pytest.fixture
def test_temp_sitl_run(
    test_input_case: dict[str, Any], expected_output_case: dict[str, Any],
) -> Iterator[Path]:
    '''启动实际仿真和 server，保留证据，并在用例失败时也清理本轮进程。'''
    test_temp_run = (ROOT / 'artifacts' / test_input_case['milestone']
                     / datetime.now(UTC).strftime('pytest-%Y%m%dT%H%M%S%fZ'))
    test_temp_run.mkdir(parents=True)
    safety_config = json.loads((ROOT / 'configs/flight_safety.example.json').read_text())
    safety_config.update(max_altitude_m=5.0, max_mission_radius_m=10.0)
    safety_config['geofence'].update(
        north_min_m=-10.0, north_max_m=10.0, east_min_m=-10.0, east_max_m=10.0
    )
    safety_config.update(test_input_case.get('safety', {}))
    _save(test_temp_run / 'safety.json', safety_config)
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
            'scenario': test_input_case['description'],
            'case': {'test_input_case': test_input_case, 'expected_output_case': expected_output_case},
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


def _save(test_temp_path: Path, value: object) -> None:
    '''保存可核对的运行证据。'''
    test_temp_path.write_text(json.dumps(value, indent=2) + '\n')


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
    test_temp_events: list[dict[str, object]] = []
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
        if (event == 'return' and test_temp_codes.get(frame.f_code) == 'state_reads'
                and test_temp_counts['state_reads'] % 2 == 0):
            _save(
                test_temp_run / 'test_temp_backend_calls.json', test_temp_counts | test_temp_sources
            )
        if event == 'call' and frame.f_code in test_temp_codes:
            test_temp_key = test_temp_codes[frame.f_code]
            test_temp_counts[test_temp_key] += 1
            if test_temp_key == 'execute_calls':
                _save(
                    test_temp_run / 'test_temp_backend_calls.json', test_temp_counts | test_temp_sources
                )
                command = frame.f_locals['command'].model_dump(mode='json')
                if not any(item['command'] == command for item in test_temp_events):
                    test_temp_events.append({
                        'command': command,
                        'recorded_decisions': [json.loads(line)['decision']['decision_id']
                                               for line in (test_temp_run / 'safety.jsonl')
                                               .read_text().splitlines()
                                               if json.loads(line)['type'] == 'safety_decision'],
                    })
                    _save(test_temp_run / 'test_temp_execution_entries.json', test_temp_events)

    _save(test_temp_run / 'test_temp_backend_calls.json', test_temp_counts | test_temp_sources)
    sys.argv = [
        'flight_atomic_action_server',
        '--safety-json',
        str(test_temp_run / 'safety.json'),
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
