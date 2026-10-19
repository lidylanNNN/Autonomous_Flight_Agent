'''Safety Supervisor 首批装配测试。'''

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flight_agent.components.safety import SafetyExecutionGate, SafetySupervisor
from flight_agent.components.tracing import TraceRecorder, TraceReplay
from flight_agent.components.vehicle.flight_execution import MockFlightExecutionBackend
from flight_agent.contracts import (
    AtomicActionProposal,
    ControlAuthority,
    GlobalPosition,
    MissionConstraints,
    MissionContract,
    NedGeofence,
    Px4ParameterReadError,
    SafetyDecision,
    SafetyDecisionType,
    SafetyReasonCode,
    SkillExecutionStatus,
    SkillName,
    TakeoffArgs,
    Vector3,
    WorldState,
)

_NOW = datetime(2026, 10, 12, tzinfo=UTC)


class FakeSpeedSource:
    '''在单测中模拟已校验的飞控限速来源。'''

    def __init__(self, value: float = 8.0, error: Exception | None = None) -> None:
        '''配置读数或注入读取错误。'''

        self.value = value
        self.error = error
        self.calls: list[float] = []

    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        '''记录读取时设置的有效期并返回配置结果。'''

        self.calls.append(max_age_s)
        if self.error is not None:
            raise self.error
        return self.value


def make_contract() -> MissionContract:
    '''构造带任务硬约束的合同。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='安全完成任务',
        constraints=MissionConstraints(
            max_altitude_m=50.0,
            max_horizontal_speed_mps=8.0,
            max_mission_radius_m=100.0,
            min_battery_percent=25.0,
            max_state_age_ms=1_000,
            geofence=NedGeofence(
                north_min_m=-100.0,
                north_max_m=100.0,
                east_min_m=-100.0,
                east_max_m=100.0,
            ),
            allowed_skills=(SkillName.TAKEOFF, SkillName.GOTO),
        ),
        completion_criteria=('完成',),
        abort_criteria=('取消',),
    )


def make_state(**changes: object) -> WorldState:
    '''构造地面待起飞的健康飞行器状态。'''

    state = WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=_NOW,
        state_age_ms=0,
        position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        home_position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
        home_position_wgs84=GlobalPosition(
            latitude_deg=0.0, longitude_deg=0.0, altitude_amsl_m=0.0
        ),
        battery_percent=80.0,
        armed=False,
        landed=True,
        position_valid=True,
        home_valid=True,
        link_healthy=True,
        health_flags={
            'battery_connected': True,
            'battery_valid': True,
            'battery_fault_free': True,
            'failure_detector_clear': True,
            'preflight_checks_pass': True,
        },
    )
    return state.model_copy(update=changes)


def make_proposal(**changes: object) -> AtomicActionProposal:
    '''构造地面起飞提案。'''

    proposal = AtomicActionProposal(
        proposal_id='proposal-1',
        mission_id='mission-1',
        plan_revision=0,
        step_id='step-1',
        skill_name='takeoff',
        arguments={'target_altitude_m': 10.0},
        based_on_state_id='state-1',
    )
    return proposal.model_copy(update=changes)


def test_healthy_proposal_gets_stable_approval() -> None:
    '''验证全部检查通过后得到稳定的批准决定。'''

    source = FakeSpeedSource()
    supervisor = SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW)
    async def run_twice() -> tuple[SafetyDecision, SafetyDecision]:
        '''在同一输入上重复仲裁。'''

        first = await supervisor.evaluate(
            make_proposal(), make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
        )
        second = await supervisor.evaluate(
            make_proposal(), make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
        )
        return first, second

    first, second = asyncio.run(run_twice())
    assert first == second
    assert first.decision is SafetyDecisionType.APPROVE
    assert first.reason_codes == (SafetyReasonCode.CHECKS_PASSED,)
    assert source.calls == [2.0, 2.0]


def test_prepare_command_binds_approval_and_registry_timeout() -> None:
    '''Only a fresh direct approval becomes a typed command.'''

    decision, command = asyncio.run(
        SafetySupervisor(FakeSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW)
        .prepare_command(
            make_proposal(), make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
            execution_id='execution-1',
        )
    )

    assert decision.decision is SafetyDecisionType.APPROVE
    assert command is not None
    assert command.execution_id == 'execution-1'
    assert command.proposal_id == decision.proposal_id
    assert command.decision_id == decision.decision_id
    assert command.approved_state_id == decision.checked_state_id
    assert command.skill_name is SkillName.TAKEOFF
    assert isinstance(command.arguments, TakeoffArgs)
    assert command.arguments.target_altitude_m == 10.0
    assert command.timeout_s == 30.0


def test_prepare_command_never_builds_from_rejection() -> None:
    '''自动Safety拒绝时不得生成执行命令。'''

    decision, command = asyncio.run(
        SafetySupervisor(FakeSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW)
        .prepare_command(
            make_proposal(), make_contract().constraints, make_state(link_healthy=False),
            ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
            execution_id='execution-1',
        )
    )

    assert decision.decision is SafetyDecisionType.REJECT
    assert command is None


def test_prepare_command_uses_proposal_snapshot_during_async_read() -> None:
    '''Changing the caller's mutable argument dict cannot change the approved command.'''

    proposal = make_proposal()

    class MutatingSpeedSource(FakeSpeedSource):
        async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
            proposal.arguments['target_altitude_m'] = 20.0
            return await super().read_validated_speed_limit(max_age_s=max_age_s)

    decision, command = asyncio.run(
        SafetySupervisor(MutatingSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW)
        .prepare_command(
            proposal, make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
            execution_id='execution-1',
        )
    )

    assert decision.decision is SafetyDecisionType.APPROVE
    assert proposal.arguments['target_altitude_m'] == 20.0
    assert command is not None
    assert isinstance(command.arguments, TakeoffArgs)
    assert command.arguments.target_altitude_m == 10.0


def test_execution_gate_traces_approval_before_dispatch(tmp_path: Path) -> None:
    path = tmp_path / 'trace.jsonl'
    backend = MockFlightExecutionBackend(make_state())
    gate = SafetyExecutionGate(
        SafetySupervisor(FakeSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW),
        backend, TraceRecorder(path), lambda: ControlAuthority.AGENT_ALLOWED,
    )

    decision, result = asyncio.run(
        gate.submit(make_proposal(), make_contract().constraints,
                    expected_mission_id='mission-1', execution_id='execution-1')
    )

    assert decision.decision is SafetyDecisionType.APPROVE
    assert result is not None
    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.execution_id == 'execution-1'
    records = tuple(TraceReplay(path).iter_safety_decision_records())
    assert len(records) == 1
    assert records[0].decision == decision


def test_execution_gate_never_dispatches_rejection(tmp_path: Path) -> None:
    '''外部控制权接管时Gate必须记录拒绝且不派发。'''

    backend = MockFlightExecutionBackend(make_state())
    path = tmp_path / 'trace.jsonl'
    gate = SafetyExecutionGate(
        SafetySupervisor(FakeSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW),
        backend, TraceRecorder(path),
        lambda: ControlAuthority.HUMAN_CONTROL_ACTIVE,
    )

    decision, result = asyncio.run(
        gate.submit(make_proposal(), make_contract().constraints,
                    expected_mission_id='mission-1', execution_id='execution-1')
    )

    assert decision.decision is SafetyDecisionType.REJECT
    assert result is None
    assert asyncio.run(backend.get_world_state()).state_id == 'state-1'
    assert next(TraceReplay(path).iter_safety_decision_records()).decision == decision


def test_execution_gate_rejects_untrusted_mission_identity(tmp_path: Path) -> None:
    '''提案声明的任务 ID 不能代替可信调用方提供的归属 ID。'''

    backend = MockFlightExecutionBackend(make_state())
    gate = SafetyExecutionGate(
        SafetySupervisor(FakeSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW),
        backend, TraceRecorder(tmp_path / 'trace.jsonl'),
        lambda: ControlAuthority.AGENT_ALLOWED,
    )

    decision, result = asyncio.run(
        gate.submit(
            make_proposal(), make_contract().constraints,
            expected_mission_id='mission-2', execution_id='execution-1',
        )
    )

    assert decision.decision is SafetyDecisionType.REJECT
    assert decision.reason_codes == (SafetyReasonCode.INVALID_SCHEMA,)
    assert result is None
    assert asyncio.run(backend.get_world_state()).state_id == 'state-1'


def test_execution_gate_fails_closed_when_trace_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MockFlightExecutionBackend(make_state())
    recorder = TraceRecorder(tmp_path / 'trace.jsonl')

    def fail_trace(proposal: AtomicActionProposal, decision: SafetyDecision) -> None:
        raise OSError('trace unavailable')

    monkeypatch.setattr(recorder, 'record_safety_decision', fail_trace)
    gate = SafetyExecutionGate(
        SafetySupervisor(FakeSpeedSource(), policy_version='m4-v1', clock=lambda: _NOW),
        backend, recorder, lambda: ControlAuthority.AGENT_ALLOWED,
    )

    with pytest.raises(OSError, match='trace unavailable'):
        asyncio.run(gate.submit(make_proposal(), make_contract().constraints,
                                expected_mission_id='mission-1', execution_id='execution-1'))
    assert asyncio.run(backend.get_world_state()).state_id == 'state-1'


def test_invalid_schema_and_stale_state_never_read_px4_parameter() -> None:
    '''验证无效参数及过期状态按顺序拒绝，且不访问飞控参数。'''

    source = FakeSpeedSource()
    decision = asyncio.run(
        SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
            make_proposal(arguments={'target_altitude_m': -1.0}),
            make_contract().constraints,
            make_state(received_at=_NOW - timedelta(seconds=3)),
            ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
        )
    )
    assert decision.decision is SafetyDecisionType.REJECT
    assert decision.reason_codes == (
        SafetyReasonCode.INVALID_SCHEMA,
        SafetyReasonCode.STALE_STATE,
    )
    assert source.calls == []


@pytest.mark.parametrize(
    ('value', 'error', 'reason'),
    [
        (12.0, None, SafetyReasonCode.SPEED_ABOVE_ENVELOPE),
        (0.0, None, SafetyReasonCode.SYSTEM_UNHEALTHY),
        (
            8.0,
            Px4ParameterReadError('PX4 unavailable'),
            SafetyReasonCode.SYSTEM_UNHEALTHY,
        ),
    ],
)
def test_unusable_px4_speed_evidence_rejects(
    value: float, error: Exception | None, reason: SafetyReasonCode
) -> None:
    '''验证限速超任务约束、值无效或读取失败均拒绝。'''

    source = FakeSpeedSource(value=value, error=error)
    decision = asyncio.run(
        SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
            make_proposal(), make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
        )
    )
    assert decision.decision is SafetyDecisionType.REJECT
    assert decision.reason_codes == (reason,)


def test_state_becoming_stale_during_parameter_read_rejects() -> None:
    '''验证异步限速读取完成后重新检查原状态的时效。'''

    current = [_NOW]

    class AdvancingSource(FakeSpeedSource):
        '''模拟参数读取期间时钟向前推进。'''

        async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
            '''在返回读数前推进时钟。'''

            current[0] += timedelta(seconds=2)
            return await super().read_validated_speed_limit(max_age_s=max_age_s)

    decision = asyncio.run(
        SafetySupervisor(
            AdvancingSource(), policy_version='m4-v1', clock=lambda: current[0]
        ).evaluate(make_proposal(), make_contract().constraints, make_state(),
                   ControlAuthority.AGENT_ALLOWED, expected_mission_id='mission-1')
    )
    assert decision.reason_codes == (SafetyReasonCode.STALE_STATE,)


def test_px4_read_failure_keeps_original_diagnostic(caplog: pytest.LogCaptureFixture) -> None:
    '''验证已知飞控读取故障拒绝放行且日志保留原始原因。'''

    source = FakeSpeedSource(error=Px4ParameterReadError('heartbeat timed out'))
    decision = asyncio.run(
        SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
            make_proposal(), make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
            expected_mission_id='mission-1',
        )
    )
    assert decision.reason_codes == (SafetyReasonCode.SYSTEM_UNHEALTHY,)
    assert 'heartbeat timed out' in caplog.text


def test_programming_error_is_not_disguised_as_px4_failure() -> None:
    '''验证非协议异常原样传播，避免掩盖代码缺陷。'''

    source = FakeSpeedSource(error=AttributeError('misspelled method'))
    with pytest.raises(AttributeError, match='misspelled method'):
        asyncio.run(
            SafetySupervisor(source, policy_version='m4-v1', clock=lambda: _NOW).evaluate(
                make_proposal(), make_contract().constraints, make_state(), ControlAuthority.AGENT_ALLOWED,
                expected_mission_id='mission-1',
            )
        )


def test_late_stale_state_precedes_speed_rejection() -> None:
    '''验证异步读取后出现多种错误时仍遵守检查顺序。'''

    current = [_NOW]

    class SlowSpeedSource(FakeSpeedSource):
        '''模拟超限参数在状态变旧后才返回。'''

        async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
            '''推进时钟并返回超限速度。'''

            current[0] += timedelta(seconds=2)
            return await super().read_validated_speed_limit(max_age_s=max_age_s)

    decision = asyncio.run(
        SafetySupervisor(
            SlowSpeedSource(value=12.0), policy_version='m4-v1', clock=lambda: current[0]
        ).evaluate(make_proposal(), make_contract().constraints, make_state(),
                   ControlAuthority.AGENT_ALLOWED, expected_mission_id='mission-1')
    )
    assert decision.reason_codes == (
        SafetyReasonCode.STALE_STATE,
        SafetyReasonCode.SPEED_ABOVE_ENVELOPE,
    )
