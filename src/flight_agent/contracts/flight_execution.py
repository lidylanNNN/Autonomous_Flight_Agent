'''Flight execution boundary shared by Mock and PX4 adapters.'''

from __future__ import annotations

from typing import Protocol, runtime_checkable

from flight_agent.contracts.skill import ApprovedSkillCommand, SkillResult
from flight_agent.contracts.world_state import WorldState


@runtime_checkable
class FlightExecutionInterface(Protocol):
    '''隔离飞行技能执行层与 ROS2/PX4 消息类型的异步接口。'''

    async def get_world_state(self) -> WorldState:
        '''返回飞行执行后端当前的强类型状态快照。'''

        ...

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''执行一条经过批准的 Skill 命令并返回终态结果。'''

        ...

    async def cancel(self, execution_id: str) -> None:
        '''请求取消指定的在途执行。'''

        ...
