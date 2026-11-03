'''验证独立 M3 调试入口的参数、装配和退出清理；不启动 PX4。'''

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from flight_agent_ros.entrypoints import m3_scripted_flight
from flight_agent_ros.entrypoints.m3_scripted_flight import (
    ScriptedFlightParameters,
    run_scripted_px4_flight,
)

from flight_agent.contracts import (
    ApprovedSkillCommand,
    GlobalPosition,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    Vector3,
    WorldState,
)


@pytest.mark.parametrize('options', [
    ['--instruction', '起飞到10米'], ['--contract-json', 'contract.json'], ['--legacy-m3-direct'],
])
def test_debug_entry_rejects_old_mission_options(
    monkeypatch: pytest.MonkeyPatch, options: list[str],
) -> None:
    '''独立调试入口不再接受旧任务选项或模式切换参数。'''
    monkeypatch.setattr(sys, 'argv', ['m3_scripted_flight', *options])
    with pytest.raises(SystemExit) as error:
        m3_scripted_flight._parse_arguments()
    assert error.value.code == 2


@pytest.mark.parametrize('succeeded', [True, False])
def test_debug_main_wires_backend_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, succeeded: bool,
) -> None:
    '''经真实 main 验证参数传递、Backend 装配及成功和失败后的资源清理。'''
    monkeypatch.setattr(sys, 'argv', [
        'm3_scripted_flight', '--takeoff-altitude-m', '12', '--hold-duration-s', '4',
        '--startup-timeout-s', '9', '--set-home-to-current',
    ])
    ros = Mock()
    monkeypatch.setattr(m3_scripted_flight, 'rclpy', ros)
    factories = {}
    for name in (
        'Px4WorldStateAdapter', 'Px4VehicleCommandAdapter', 'Px4OffboardSetpointAdapter',
        'SingleThreadedExecutor', 'Px4CommandPlanExecutor', 'Px4Ros2FlightExecutionBackend',
    ):
        factories[name] = Mock()
        monkeypatch.setattr(m3_scripted_flight, name, factories[name])
    status = SkillExecutionStatus.SUCCEEDED if succeeded else SkillExecutionStatus.TIMED_OUT
    run = AsyncMock(return_value=[SimpleNamespace(status=status)])
    monkeypatch.setattr(m3_scripted_flight, '_run_when_vehicle_ready', run)
    if succeeded:
        m3_scripted_flight.main()
    else:
        with pytest.raises(SystemExit) as error:
            m3_scripted_flight.main()
        assert error.value.code == 1
    run.assert_awaited_once()
    backend, world, command, parameters = run.call_args.args
    assert backend is factories['Px4Ros2FlightExecutionBackend'].return_value
    assert world is factories['Px4WorldStateAdapter'].return_value
    assert command is factories['Px4VehicleCommandAdapter'].return_value
    assert parameters.takeoff_altitude_m == 12.0
    assert parameters.hold_duration_s == 4.0
    assert run.call_args.kwargs == {'startup_timeout_s': 9.0, 'set_home_to_current': True}
    factories['Px4Ros2FlightExecutionBackend'].assert_called_once_with(
        state_reader=world.read_world_state,
        plan_executor=factories['Px4CommandPlanExecutor'].return_value,
        offboard_adapter=factories['Px4OffboardSetpointAdapter'].return_value,
    )
    ros.init.assert_called_once()
    ros.shutdown.assert_called_once()
    factories['SingleThreadedExecutor'].return_value.shutdown.assert_called_once()
    for name in ('Px4WorldStateAdapter', 'Px4VehicleCommandAdapter', 'Px4OffboardSetpointAdapter'):
        factories[name].return_value.destroy_node.assert_called_once()


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
