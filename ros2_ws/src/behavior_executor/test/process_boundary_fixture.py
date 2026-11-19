'''独立进程验收的外部边界替身；不安装为生产入口。'''

from __future__ import annotations

import json
import os
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from rclpy.node import Node

from flight_agent.components.tracing import TraceReplay
from flight_agent.components.vehicle.flight_execution import MockFlightExecutionBackend
from flight_agent.contracts import ApprovedSkillCommand, SkillResult, WorldState


def test_temp_main() -> None:
    '''只替换状态、执行和限速来源，再运行安装后的原始 console script。'''
    test_temp_role, test_temp_executable, test_temp_directory = sys.argv[1:4]
    test_temp_run = Path(test_temp_directory)
    if test_temp_role == 'flight':
        from flight_agent_ros import flight_atomic_action_server as entry

        def test_temp_build_backend(**test_temp_arguments: object) -> MockFlightExecutionBackend:
            '''忽略实际 PX4 适配器参数；执行与读取均使用文件共享状态替身。'''
            return TestTempBackend(test_temp_read_state())

        entry.Px4Ros2FlightExecutionBackend = test_temp_build_backend
        entry.Px4ParameterAdapter = TestTempSpeedSource
    else:
        from observation_manager import observation_manager_node as entry
    entry.Px4WorldStateAdapter = TestTempStateSource
    (test_temp_run / f'test_temp_{test_temp_role}_source.json').write_text(json.dumps({
        'entry_source': entry.__file__, 'executable': test_temp_executable,
    }))
    sys.argv = [test_temp_executable, *sys.argv[4:]]
    runpy.run_path(test_temp_executable, run_name='__main__')


def test_temp_read_state() -> WorldState:
    '''读取原子替换的共享快照；新鲜时间戳是测试输入，不代表 PX4 实测。'''
    state = WorldState.model_validate_json(Path(os.environ['TEST_TEMP_STATE_PATH']).read_text())
    return state.model_copy(update={'received_at': datetime.now(UTC), 'state_age_ms': 0})


class TestTempStateSource(Node):
    def __init__(self) -> None:
        '''提供可加入实际 ROS executor 的状态来源替身。'''
        super().__init__('test_temp_state_source')

    def read_world_state(self) -> WorldState:
        '''沿用生产状态来源接口返回测试快照。'''
        return test_temp_read_state()


class TestTempBackend(MockFlightExecutionBackend):
    async def get_world_state(self) -> WorldState:
        '''为实际 Gate 提供同一份当前替身快照。'''
        return test_temp_read_state()

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''确认批准已记录，执行已有替身，再原子发布状态给观察进程。'''
        test_temp_path = Path(os.environ['TEST_TEMP_STATE_PATH'])
        decisions = tuple(TraceReplay(test_temp_path.parent / 'safety.jsonl')
                          .iter_safety_decision_records())
        assert decisions[-1].decision.decision_id == command.decision_id
        assert decisions[-1].decision.decision.value == 'APPROVE'
        result = await super().execute(command)
        test_temp_pending = test_temp_path.with_suffix('.pending')
        test_temp_pending.write_text(self._state.model_dump_json())
        test_temp_pending.replace(test_temp_path)
        return result


class TestTempSpeedSource:
    async def read_validated_speed_limit_reading(self, *, max_age_s: float):
        '''按真实限速接口提供固定值，不验证 MAVLink 来源及 max_age_s。'''
        return SimpleNamespace(value=8.0, received_at=datetime.now(UTC))


if __name__ == '__main__':
    test_temp_main()
