'''Execute PX4 command plans without claiming Agent Flight Skill completion.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from flight_agent_ros.adapters.px4_command_plan_mapper import Px4CommandPlan
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    Px4CommandAck,
    Px4CommandAckStatus,
    VehicleCommandParameters,
)


class _VehicleCommandSubmitter(Protocol):
    '''PX4 command transport shape consumed by the command-plan executor.'''

    async def submit_and_wait(
        self,
        *,
        execution_id: str,
        command_id: int,
        timeout_s: float,
        parameters: VehicleCommandParameters | None = None,
    ) -> Px4CommandAck: ...

    async def cancel(self, execution_id: str) -> bool: ...


@dataclass(frozen=True)
class Px4CommandPlanReceipt:
    '''Transport evidence collected while submitting one PX4 command plan.'''

    execution_id: str
    acknowledgements: tuple[Px4CommandAck, ...]

    @property
    def terminal_ack(self) -> Px4CommandAck:
        '''Return the ACK that completed or stopped command submission.'''

        return self.acknowledgements[-1]

    @property
    def all_commands_accepted(self) -> bool:
        '''Report transport acceptance without claiming flight completion.'''

        return all(
            ack.status is Px4CommandAckStatus.ACCEPTED
            for ack in self.acknowledgements
        )


class Px4CommandPlanExecutor:
    '''Submit PX4 commands serially under one Agent Flight Skill timeout budget.'''

    def __init__(self, command_submitter: _VehicleCommandSubmitter) -> None:
        '''Bind the executor to one serialized PX4 command transport.'''

        self._command_submitter = command_submitter

    async def execute(
        self, plan: Px4CommandPlan, *, timeout_s: float
    ) -> Px4CommandPlanReceipt:
        '''Submit commands in order and stop at the first non-accepted ACK.'''

        if timeout_s <= 0.0:
            raise ValueError('timeout_s must be positive')
        if not plan.commands:
            raise ValueError('PX4 command plan must include at least one command')

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        acknowledgements: list[Px4CommandAck] = []
        for command in plan.commands:
            remaining_s = deadline - loop.time()
            if remaining_s <= 0.0:
                ack = Px4CommandAck(
                    execution_id=plan.execution_id,
                    command_id=command.command_id,
                    status=Px4CommandAckStatus.TIMED_OUT,
                    ack_name=None,
                    failure_code='SKILL_TIMEOUT_BUDGET_EXHAUSTED',
                )
            else:
                ack = await self._command_submitter.submit_and_wait(
                    execution_id=plan.execution_id,
                    command_id=command.command_id,
                    timeout_s=remaining_s,
                    parameters=command.parameters,
                )
            acknowledgements.append(ack)
            if ack.status is not Px4CommandAckStatus.ACCEPTED:
                break

        return Px4CommandPlanReceipt(
            execution_id=plan.execution_id,
            acknowledgements=tuple(acknowledgements),
        )

    async def cancel(self, execution_id: str) -> bool:
        '''Cancel the transport wait currently associated with an execution.'''

        return await self._command_submitter.cancel(execution_id)
