'''Flight execution boundary shared by Mock and PX4 adapters.'''

from __future__ import annotations

from typing import Protocol, runtime_checkable

from flight_agent.contracts.models.skill_model import ApprovedSkillCommand, SkillResult
from flight_agent.contracts.models.world_state_model import WorldState


@runtime_checkable
class FlightExecutionBackendProtocol(Protocol):
    '''飞行执行后端必须满足的异步方法形状。'''

    async def get_world_state(self) -> WorldState:
        '''返回飞行执行后端当前的强类型状态快照。'''

        ...

    async def execute(self, command: ApprovedSkillCommand) -> SkillResult:
        '''执行一条经过批准的 Skill 命令并返回终态结果。'''

        ...

    async def cancel(self, execution_id: str) -> None:
        '''请求取消指定的在途执行。'''

        ...
