'''Exercise the hold ROS service through its production gate assembly.'''

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread

import pytest
import rclpy
from flight_agent_ros.flight_atomic_action_server import (
    build_flight_atomic_action_server,
    main,
)
from flight_agent_ros.safety_config import load_safety_constraints
from rclpy.executors import MultiThreadedExecutor
from rosllm_srvs.srv import AtomicAction

from flight_agent.contracts import (
    ApprovedSkillCommand,
    ControlAuthority,
    GlobalPosition,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
    WorldState,
)


def action_arguments(service_name: str) -> dict[str, float]:
    '''为每个已接入动作提供独立的合法测试参数。'''
    return {
        'hold': {'duration_s': 1.0},
        'takeoff': {'target_altitude_m': 10.0},
        'rtl': {},
        'land': {},
        'goto': {
            'north_m': 1.0, 'east_m': 2.0,
            'altitude_m': 10.0, 'acceptance_radius_m': 1.0,
        },
    }[service_name]


class RecordingBackend:
    def __init__(self) -> None:
        self.executions = 0
        self.state = WorldState(
            state_id='state-hold',
            source_timestamp_us=1,
            received_at=datetime.now(UTC),
            state_age_ms=0,
            position_ned_m=Vector3(x=0.0, y=0.0, z=-2.0),
            battery_percent=80.0,
            armed=True,
            landed=False,
            position_valid=True,
            link_healthy=True,
            health_flags={
                'battery_connected': True,
                'battery_valid': True,
                'battery_fault_free': True,
                'failure_detector_clear': True,
                'preflight_checks_pass': True,
            },
        )

    async def get_world_state(self) -> WorldState:
        return self.state

    async def execute(self, _command: object) -> None:
        self.executions += 1
        raise AssertionError('UNKNOWN authority must not dispatch')


class UnusedSpeedSource:
    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        del max_age_s
        raise AssertionError('UNKNOWN authority must not request speed approval')


@pytest.mark.parametrize('service_name', ['hold', 'takeoff', 'goto', 'rtl', 'land'])
def test_action_ros_service_rejects_unknown_authority_and_records_trace(
    tmp_path: Path, service_name: str,
) -> None:
    '''从真实 ROS 服务验证权限拒绝、参数拒绝与 Trace，禁止派发。'''
    example = Path(__file__).resolve().parents[4] / 'configs/flight_safety.example.json'
    constraints = load_safety_constraints(example)
    backend = RecordingBackend()
    trace_path = tmp_path / 'safety.jsonl'

    rclpy.init()
    server = build_flight_atomic_action_server(
        constraints,
        backend,
        trace_path=trace_path,
        authority_reader=lambda: ControlAuthority.UNKNOWN,
        speed_limit_source=UnusedSpeedSource(),
    )
    client_node = rclpy.create_node('flight_hold_client')
    client = client_node.create_client(AtomicAction, service_name)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(server)
    executor.add_node(client_node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert client.wait_for_service(timeout_sec=2.0)
        result = client.call(
            AtomicAction.Request(
                input=json.dumps(action_arguments(service_name)),
                prev_output='prior-result',
            ),
            timeout_sec=2.0,
        )
        assert result is not None
        assert result.success is False
        output = json.loads(result.output)
        assert output['source'] == 'flight_atomic_action_server'
        assert output['proposal_id']
        assert output['decision_id']
        assert output['execution_id'] is None
        assert 'AUTHORITY_NOT_GRANTED' in output['reason_codes']
        assert output['execution_status'] is None
        assert output['error_code'] is None
        assert output['verification'] == 'INCONCLUSIVE'
        assert backend.executions == 0

        records = [json.loads(line) for line in trace_path.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]['decision']['decision_id'] == output['decision_id']
        assert records[0]['proposal']['proposal_id'] == output['proposal_id']

        invalid_fields = client.call(
            AtomicAction.Request(
                input=json.dumps(action_arguments(service_name) | {'unexpected': True}), prev_output=''
            ),
            timeout_sec=2.0,
        )
        assert invalid_fields is not None
        assert invalid_fields.success is False
        assert 'INVALID_SCHEMA' in json.loads(invalid_fields.output)['reason_codes']
        assert len(trace_path.read_text().splitlines()) == 2

        invalid_inputs = (
            [('{}', 'INVALID_INPUT'), ('[]', 'INVALID_INPUT'), ('{', 'INVALID_INPUT')]
            if service_name == 'hold' else [
                ('{}', None), ('{"target_altitude_m":0}', None),
                ('{"target_altitude_m":-1}', None), ('{"target_altitude_m":"bad"}', None),
                ('{"target_altitude_m":10,"skill_name":"land"}', None),
                ('[]', 'INVALID_INPUT'), ('{', 'INVALID_INPUT'),
            ]
        )
        if service_name == 'goto':
            arguments = action_arguments(service_name)
            invalid_inputs = [('{}', None), ('[]', 'INVALID_INPUT'), ('{', 'INVALID_INPUT')]
            for field in arguments:
                invalid_inputs.append((json.dumps({k: v for k, v in arguments.items() if k != field}), None))
            for changes in [
                {'altitude_m': 0}, {'altitude_m': -1},
                {'acceptance_radius_m': 0}, {'acceptance_radius_m': -1},
                {'north_m': 'bad'}, {'skill_name': 'land'},
            ]:
                invalid_inputs.append((json.dumps(arguments | changes), None))
        if service_name in {'rtl', 'land'}:
            invalid_inputs = [
                ('{"target_altitude_m":10}', None), ('{"skill_name":"land"}', None),
                ('[]', 'INVALID_INPUT'), ('{', 'INVALID_INPUT'),
            ]
        trace_count = 2
        for payload, error_code in invalid_inputs:
            invalid = client.call(
                AtomicAction.Request(input=payload, prev_output=''), timeout_sec=2.0,
            )
            assert invalid is not None and not invalid.success
            invalid_output = json.loads(invalid.output)
            assert invalid_output['error_code'] == error_code
            assert invalid_output['execution_id'] is None
            if error_code is None:
                assert 'INVALID_SCHEMA' in invalid_output['reason_codes']
                trace_count += 1
            else:
                assert invalid_output['decision_id'] is None
            assert len(trace_path.read_text().splitlines()) == trace_count
        if service_name == 'goto':
            rejected = client.call(AtomicAction.Request(
                input=json.dumps(action_arguments(service_name) | {'north_m': 200.0}),
                prev_output='',
            ), timeout_sec=2.0)
            assert rejected is not None and not rejected.success
            rejection = json.loads(rejected.output)
            assert 'WAYPOINT_OUTSIDE_GEOFENCE' in rejection['reason_codes']
            assert rejection['execution_id'] is None
            assert len(trace_path.read_text().splitlines()) == trace_count + 1
        assert backend.executions == 0
    finally:
        executor.shutdown()
        thread.join(timeout=5.0)
        client_node.destroy_node()
        server.destroy_node()
        rclpy.shutdown()


def test_invalid_safety_json_prevents_ros_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, 'argv', [
        'flight_atomic_action_server', '--safety-json', str(tmp_path / 'missing.json')
    ])
    with pytest.raises(ValueError, match='cannot read safety constraints JSON'):
        main()
    assert not rclpy.ok()


@pytest.mark.parametrize(('service_name', 'outcome'), [
    (name, outcome)
    for name in ['hold', 'takeoff', 'goto', 'rtl', 'land']
    for outcome in ['success', 'execution_failure', 'trace_failure']
] + [('rtl', 'home_unavailable'), ('land', 'already_landed'), ('land', 'invalid_position')])
def test_action_execution_identity_requires_recorded_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service_name: str, outcome: str,
) -> None:
    '''通过真实 ROS 服务验证批准后的 ID 返回，以及 Trace 失败时零分配零派发。'''
    from unittest.mock import AsyncMock, Mock

    from flight_agent.components.tracing import TraceRecorder
    from flight_agent.components.vehicle.flight_execution import MockFlightExecutionBackend

    class FixedSpeedSource:
        async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
            '''仅为测试提供可信限速读数。'''
            return 8.0

    trace_fails = outcome == 'trace_failure'
    rejection_reason = {
        'home_unavailable': 'HOME_REFERENCE_UNAVAILABLE',
        'already_landed': 'INVALID_COMMAND_SEQUENCE',
        'invalid_position': 'INVALID_VEHICLE_STATE',
    }.get(outcome)
    example = Path(__file__).resolve().parents[4] / 'configs/flight_safety.example.json'
    state = RecordingBackend().state.model_copy(update={
        'home_valid': outcome != 'home_unavailable',
        'armed': service_name != 'takeoff',
        'landed': service_name == 'takeoff' or outcome == 'already_landed',
        'position_valid': outcome != 'invalid_position',
        'home_position_ned_m': Vector3(x=0.0, y=0.0, z=0.0),
        'position_ned_m': Vector3(x=0.0, y=0.0, z=0.0 if service_name == 'takeoff' else -2.0),
        'home_position_wgs84': GlobalPosition(latitude_deg=47.0, longitude_deg=8.0, altitude_amsl_m=500.0),
    })
    backend = MockFlightExecutionBackend(state)
    original_execute = backend.execute

    async def execute_result(command: ApprovedSkillCommand) -> SkillResult:
        '''保留模拟执行，仅在指定场景返回失败结果。'''
        result = await original_execute(command)
        if outcome == 'execution_failure':
            return result.model_copy(update={
                'status': SkillExecutionStatus.FAILED, 'failure_code': 'TEST_EXECUTION_FAILURE',
            })
        return result

    execute = AsyncMock(side_effect=execute_result)
    monkeypatch.setattr(backend, 'execute', execute)
    if trace_fails:
        monkeypatch.setattr(TraceRecorder, 'record_safety_decision', Mock(side_effect=OSError('trace unavailable')))
    if trace_fails or rejection_reason is not None:
        allocate = Mock(side_effect=AssertionError('must not allocate'))
        monkeypatch.setattr('flight_agent.components.safety.execution_gate.uuid4', allocate)
    trace_path = tmp_path / 'safety.jsonl'
    rclpy.init()
    server = build_flight_atomic_action_server(
        load_safety_constraints(example), backend, trace_path=trace_path,
        authority_reader=lambda: ControlAuthority.AGENT_ALLOWED,
        speed_limit_source=FixedSpeedSource(),
    )
    client_node = rclpy.create_node('hold_identity_client')
    client = client_node.create_client(AtomicAction, service_name)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(server)
    executor.add_node(client_node)
    thread = Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        assert client.wait_for_service(timeout_sec=2.0)
        response = client.call(
            AtomicAction.Request(
                input=json.dumps(action_arguments(service_name)),
                prev_output='{"skill_name":"land","target_altitude_m":999}',
            ), timeout_sec=2.0,
        )
        assert response is not None
        output = json.loads(response.output)
        if trace_fails or rejection_reason is not None:
            assert not response.success
            assert output['error_code'] == ('SERVICE_ERROR' if trace_fails else None)
            if rejection_reason is not None:
                assert rejection_reason in output['reason_codes']
                record = json.loads(trace_path.read_text().strip())
                assert record['decision']['decision_id'] == output['decision_id']
            assert output['execution_id'] is None
            allocate.assert_not_called()
            execute.assert_not_called()
        else:
            assert response.success is (outcome == 'success')
            assert output['error_code'] is None
            assert output['execution_status'] == ('SUCCEEDED' if outcome == 'success' else 'FAILED')
            assert output['failure_code'] == (None if outcome == 'success' else 'TEST_EXECUTION_FAILURE')
            execute.assert_awaited_once()
            command = execute.call_args.args[0]
            assert command.skill_name is SkillName(service_name)
            assert command.arguments.model_dump() == action_arguments(service_name)
            assert output['execution_id'] == command.execution_id
            assert output['decision_id'] == command.decision_id
            assert output['proposal_id'] == command.proposal_id
            record = json.loads(trace_path.read_text().strip())
            assert record['decision']['decision_id'] == command.decision_id
    finally:
        executor.shutdown()
        thread.join(timeout=5.0)
        client_node.destroy_node()
        server.destroy_node()
        rclpy.shutdown()
