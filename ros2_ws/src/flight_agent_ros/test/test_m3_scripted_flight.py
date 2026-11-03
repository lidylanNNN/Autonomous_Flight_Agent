'''验证独立 M3 调试入口的参数、装配和退出清理；不启动 PX4。'''

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from flight_agent_ros.entrypoints import m3_scripted_flight, mission_cli

from flight_agent.contracts import SkillExecutionStatus


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


def test_old_debug_option_delegates_to_independent_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    '''迁移期间旧调试参数调用新实现，不再运行旧入口的 ROS 装配。'''
    run = Mock()
    monkeypatch.setattr(mission_cli, 'run_m3_debug', run)
    monkeypatch.setattr(sys, 'argv', ['mission_cli', '--legacy-m3-direct', '--hold-duration-s', '4'])
    mission_cli.main()
    run.assert_called_once()
    assert run.call_args.args[0].hold_duration_s == 4.0


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
