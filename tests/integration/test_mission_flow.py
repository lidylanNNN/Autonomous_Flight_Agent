'''从用户语句到受控执行结果的软件主链集成测试。'''

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from flight_agent.components.safety import SafetyExecutionGate, SafetySupervisor
from flight_agent.components.tracing import TraceRecorder, TraceReplay
from flight_agent.components.vehicle.flight_execution import (
    MockExecutionOutcome,
    MockFlightExecutionBackend,
)
from flight_agent.contracts import (
    ControlAuthority,
    GlobalPosition,
    MissionConstraints,
    MissionContract,
    MissionPlan,
    MissionRequest,
    MissionRunStatus,
    NedGeofence,
    PlanStep,
    SafetyDecision,
    SafetyDecisionType,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
    VerificationStatus,
    WorldState,
)
from flight_agent.workflows.mission import MissionService


class FixedSpeedSource:
    '''测试中的 PX4 参数替身，仅提供已校验的固定速度上限。'''

    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        '''返回低于任务安全上限的速度证据。'''

        assert max_age_s > 0.0
        return 8.0


def make_contract() -> MissionContract:
    '''生成不允许用户语句放宽的测试任务契约。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='待绑定用户目标',
        constraints=MissionConstraints(
            max_altitude_m=20.0,
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
            allowed_skills=(SkillName.TAKEOFF,),
        ),
        completion_criteria=('起飞完成',),
        abort_criteria=('安全拒绝',),
    )


def make_state() -> WorldState:
    '''生成可用于脚本起飞的地面状态。'''

    return WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=datetime.now(UTC),
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


def make_service(
    path: Path,
    *,
    authority: ControlAuthority = ControlAuthority.AGENT_ALLOWED,
    outcomes: dict[str, MockExecutionOutcome] | None = None,
) -> tuple[MissionService, MockFlightExecutionBackend]:
    '''从与生产相同的对象装配路径构造软件链路。'''

    backend = MockFlightExecutionBackend(make_state(), outcomes=outcomes)
    gate = SafetyExecutionGate(
        SafetySupervisor(FixedSpeedSource(), policy_version='m4-skeleton'),
        backend,
        TraceRecorder(path),
        lambda: authority,
    )
    return MissionService(make_contract(), backend, gate), backend


def revised_takeoff_plan() -> MissionPlan:
    '''Recovery 测试用的新 revision，仍须重新经过 Gate。'''

    return MissionPlan(
        mission_id='mission-1',
        plan_revision=1,
        contract_version=1,
        based_on_state_id='state-1',
        steps=(PlanStep(
            step_id='step-2',
            skill_name=SkillName.TAKEOFF,
            arguments={'target_altitude_m': 10.0},
            timeout_s=30.0,
        ),),
    )


def test_supported_utterance_reaches_backend_without_claiming_mission_success(
    tmp_path: Path,
) -> None:
    '''批准的语句经真实 Gate 派发，独立验证占位保持未完成状态。'''

    trace_path = tmp_path / 'trace.jsonl'
    service, backend = make_service(trace_path)

    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.UNVERIFIED
    assert report.plan is not None
    assert report.plan.steps[0].skill_name is SkillName.TAKEOFF
    assert len(report.decisions) == len(report.skill_results) == len(report.verifications) == 1
    assert report.decisions[0].decision is SafetyDecisionType.APPROVE
    assert report.skill_results[0].status is SkillExecutionStatus.SUCCEEDED
    assert report.verifications[0].result is VerificationStatus.INCONCLUSIVE
    assert (asyncio.run(backend.get_world_state())).landed is False
    assert next(TraceReplay(trace_path).iter_safety_decision_records()).decision == report.decisions[0]


def test_unknown_utterance_never_dispatches(tmp_path: Path) -> None:
    '''占位 Planner 不把未知语言映射成任何飞行动作。'''

    service, backend = make_service(tmp_path / 'trace.jsonl')

    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='飞到那个地方'
    )))

    assert report.status is MissionRunStatus.UNSUPPORTED
    assert report.decisions == ()
    assert report.skill_results == ()
    assert (asyncio.run(backend.get_world_state())).state_id == 'state-1'


def test_untrusted_authority_reaches_recovery_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    '''Safety 拒绝把完整原因交给恢复占位，不得派发命令。'''

    service, backend = make_service(
        tmp_path / 'trace.jsonl', authority=ControlAuthority.HUMAN_CONTROL_ACTIVE
    )
    recovery_events: list[SafetyDecision | SkillResult] = []

    def record_recovery(event: SafetyDecision | SkillResult) -> None:
        recovery_events.append(event)

    monkeypatch.setattr(service, '_recover_placeholder', record_recovery)

    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.REJECTED
    assert report.decisions[0].decision is SafetyDecisionType.REJECT
    assert recovery_events == [report.decisions[0]]
    assert report.skill_results == ()
    assert (asyncio.run(backend.get_world_state())).state_id == 'state-1'


def test_failed_backend_reaches_recovery_without_claiming_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    '''飞行失败也进入同一恢复入口，但占位不报告成功。'''

    service, _ = make_service(
        tmp_path / 'trace.jsonl',
        outcomes={'mission-1-step-1-execution': MockExecutionOutcome.NO_PROGRESS},
    )
    recovery_events: list[SafetyDecision | SkillResult] = []

    def record_recovery(event: SafetyDecision | SkillResult) -> None:
        recovery_events.append(event)

    monkeypatch.setattr(service, '_recover_placeholder', record_recovery)

    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.FAILED
    assert report.reason == 'RECOVERY_NOT_IMPLEMENTED'
    assert recovery_events == [report.skill_results[0]]
    assert report.verifications[0].result is VerificationStatus.INCONCLUSIVE


def test_recovered_safety_rejection_rechecks_revised_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    '''恢复后继续 run，第二版计划仍要通过真实 Gate。'''

    authority = [ControlAuthority.HUMAN_CONTROL_ACTIVE]
    backend = MockFlightExecutionBackend(make_state())
    gate = SafetyExecutionGate(
        SafetySupervisor(FixedSpeedSource(), policy_version='m4-skeleton'),
        backend,
        TraceRecorder(tmp_path / 'trace.jsonl'),
        lambda: authority[0],
    )
    service = MissionService(make_contract(), backend, gate)

    def recover(event: SafetyDecision | SkillResult) -> MissionPlan:
        assert isinstance(event, SafetyDecision)
        authority[0] = ControlAuthority.AGENT_ALLOWED
        return revised_takeoff_plan()

    monkeypatch.setattr(service, '_recover_placeholder', recover)
    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.UNVERIFIED
    assert report.plan is not None and report.plan.plan_revision == 1
    assert [item.decision for item in report.decisions] == [
        SafetyDecisionType.REJECT, SafetyDecisionType.APPROVE,
    ]
    assert len(report.skill_results) == 1
    assert report.skill_results[0].execution_id == 'mission-1-step-2-revision-1-execution'


def test_recovered_execution_failure_retries_revised_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    '''首次失败不终止 run，修正计划再过 Safety 并执行。'''

    service, _ = make_service(
        tmp_path / 'trace.jsonl',
        outcomes={'mission-1-step-1-execution': MockExecutionOutcome.NO_PROGRESS},
    )

    def recover(event: SafetyDecision | SkillResult) -> MissionPlan:
        assert isinstance(event, SkillResult)
        assert event.status is not SkillExecutionStatus.SUCCEEDED
        return revised_takeoff_plan()

    monkeypatch.setattr(service, '_recover_placeholder', recover)
    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.UNVERIFIED
    assert report.plan is not None and report.plan.plan_revision == 1
    assert [item.decision for item in report.decisions] == [
        SafetyDecisionType.APPROVE, SafetyDecisionType.APPROVE,
    ]
    assert [item.status for item in report.skill_results] == [
        SkillExecutionStatus.FAILED, SkillExecutionStatus.SUCCEEDED,
    ]
    assert len(report.verifications) == 2


def test_mismatched_contract_identity_is_rejected(tmp_path: Path) -> None:
    '''用户任务不能借用另一份已冻结任务的契约。'''

    service, _ = make_service(tmp_path / 'trace.jsonl')

    with pytest.raises(ValueError, match='IDs differ'):
        asyncio.run(service.run(MissionRequest(
            mission_id='other-mission', instruction='起飞到10米'
        )))
