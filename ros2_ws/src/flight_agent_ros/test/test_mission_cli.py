'''验证受控任务入口及显式保留的 M3 调试流程。'''

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from flight_agent_ros.entrypoints.mission_cli import (
    ScriptedFlightParameters,
    _parse_arguments,
    build_mission_service,
    run_scripted_px4_flight,
)

from flight_agent.components.tracing import TraceReplay
from flight_agent.contracts import (
    ApprovedSkillCommand,
    ControlAuthority,
    GlobalPosition,
    MissionConstraints,
    MissionContract,
    MissionRequest,
    MissionRunStatus,
    NedGeofence,
    SafetyDecisionType,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
    VerificationStatus,
    WorldState,
)


class RecordingFlightBackend:
    '''Record scripted commands and return configured terminal statuses.'''

    def __init__(self, statuses: list[SkillExecutionStatus]) -> None:
        '''Initialize the fake backend with one status per expected execution.'''

        self._statuses = statuses
        self.commands: list[ApprovedSkillCommand] = []

    async def get_world_state(self) -> WorldState:
        '''Return a new state identity for each script step.'''

        return WorldState(
            state_id=f'state-{len(self.commands)}',
            source_timestamp_us=1,
            received_at=datetime.now(UTC),
            state_age_ms=0,
            position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
            home_position_ned_m=Vector3(x=0.0, y=0.0, z=0.0),
            home_position_wgs84=GlobalPosition(
                latitude_deg=0.0, longitude_deg=0.0, altitude_amsl_m=0.0
            ),
            battery_percent=80.0,
            armed=bool(self.commands),
            landed=not self.commands,
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

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''Record a command and return its configured result.'''

        self.commands.append(command)
        status = self._statuses[len(self.commands) - 1]
        now = datetime.now(UTC)
        return SkillResult(
            execution_id=command.execution_id,
            proposal_id=command.proposal_id,
            skill_name=command.skill_name,
            status=status,
            started_at=now,
            ended_at=now,
            start_state_id=command.approved_state_id,
            end_state_id=f'end-{len(self.commands)}',
            failure_code=None if status is SkillExecutionStatus.SUCCEEDED else 'FAILED',
        )

    async def cancel(self, execution_id: str) -> None:
        '''Satisfy the backend protocol; cancellation is not used by this script.'''

        del execution_id


def test_script_runs_all_four_skills_in_order() -> None:
    '''A healthy scripted run emits exactly Takeoff, GoTo, Hold and RTL.'''

    backend = RecordingFlightBackend([SkillExecutionStatus.SUCCEEDED] * 4)

    results = asyncio.run(
        run_scripted_px4_flight(backend, ScriptedFlightParameters())
    )

    assert [command.skill_name for command in backend.commands] == [
        SkillName.TAKEOFF,
        SkillName.GOTO,
        SkillName.HOLD,
        SkillName.RTL,
    ]
    assert [command.approved_state_id for command in backend.commands] == [
        'state-0',
        'state-1',
        'state-2',
        'state-3',
    ]
    assert len(results) == 4


def test_script_stops_after_first_failed_skill() -> None:
    '''A failed Skill prevents later scripted commands from being submitted.'''

    backend = RecordingFlightBackend(
        [SkillExecutionStatus.SUCCEEDED, SkillExecutionStatus.TIMED_OUT]
    )

    results = asyncio.run(
        run_scripted_px4_flight(backend, ScriptedFlightParameters())
    )

    assert [command.skill_name for command in backend.commands] == [
        SkillName.TAKEOFF,
        SkillName.GOTO,
    ]
    assert results[-1].status is SkillExecutionStatus.TIMED_OUT


class FixedSpeedSource:
    '''为入口装配测试提供确定性的飞控限速读数。'''

    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        '''返回测试合同允许的速度。'''

        assert max_age_s > 0.0
        return 8.0


def make_contract() -> MissionContract:
    '''为语句入口提供不可由 Planner 放宽的测试约束。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='待绑定语句',
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


def test_entrypoint_factory_connects_utterance_to_gate_and_backend(tmp_path: Path) -> None:
    '''正式入口的装配函数串通语句、Safety 与执行结果。'''

    backend = RecordingFlightBackend([SkillExecutionStatus.SUCCEEDED])
    path = tmp_path / 'mission.jsonl'
    service = build_mission_service(
        backend,
        make_contract(),
        trace_path=path,
        authority_reader=lambda: ControlAuthority.AGENT_ALLOWED,
        speed_limit_source=FixedSpeedSource(),
    )

    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.UNVERIFIED
    assert report.verifications[0].result is VerificationStatus.INCONCLUSIVE
    assert len(backend.commands) == 1
    assert backend.commands[0].decision_id == report.decisions[0].decision_id
    assert next(TraceReplay(path).iter_safety_decision_records()).decision == report.decisions[0]


def test_entrypoint_factory_rejects_unknown_authority(tmp_path: Path) -> None:
    '''缺少可信控制权来源时，入口不得派发命令。'''

    backend = RecordingFlightBackend([SkillExecutionStatus.SUCCEEDED])
    service = build_mission_service(
        backend,
        make_contract(),
        trace_path=tmp_path / 'mission.jsonl',
        authority_reader=lambda: ControlAuthority.UNKNOWN,
        speed_limit_source=FixedSpeedSource(),
    )

    report = asyncio.run(service.run(MissionRequest(
        mission_id='mission-1', instruction='起飞到10米'
    )))

    assert report.status is MissionRunStatus.REJECTED
    assert report.decisions[0].decision is SafetyDecisionType.REJECT
    assert backend.commands == []


def test_cli_requires_mission_input_or_explicit_legacy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''命令行不再默认执行 M3 直连脚本。'''

    monkeypatch.setattr(sys, 'argv', ['mission_cli'])
    with pytest.raises(SystemExit):
        _parse_arguments()

    monkeypatch.setattr(sys, 'argv', ['mission_cli', '--legacy-m3-direct'])
    assert _parse_arguments().legacy_m3_direct is True

    monkeypatch.setattr(sys, 'argv', [
        'mission_cli', '--instruction', '起飞到10米',
        '--contract-json', 'contract.json', '--takeoff-altitude-m=15',
    ])
    with pytest.raises(SystemExit):
        _parse_arguments()
