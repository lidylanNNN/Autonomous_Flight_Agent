'''Tests for serialized native PX4 command plan submission.'''

from __future__ import annotations

import asyncio

from flight_agent_ros.adapters.px4_native_plan_executor import Px4NativePlanExecutor
from flight_agent_ros.adapters.px4_native_skill_mapper import (
    Px4NativeCommand,
    Px4NativeSkillPlan,
)
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    Px4CommandAck,
    Px4CommandAckStatus,
    VehicleCommandParameters,
)

from flight_agent.contracts import SkillName


class ScriptedCommandSubmitter:
    '''Return scripted ACKs while recording transport calls.'''

    def __init__(
        self, statuses: list[Px4CommandAckStatus], *, delay_s: float = 0.0
    ) -> None:
        self._statuses = statuses
        self._delay_s = delay_s
        self.submissions: list[tuple[int, float]] = []
        self.cancelled_execution_ids: list[str] = []

    async def submit_and_wait(
        self,
        *,
        execution_id: str,
        command_id: int,
        timeout_s: float,
        parameters: VehicleCommandParameters | None = None,
    ) -> Px4CommandAck:
        del parameters
        self.submissions.append((command_id, timeout_s))
        await asyncio.sleep(self._delay_s)
        status = self._statuses.pop(0)
        accepted = status is Px4CommandAckStatus.ACCEPTED
        return Px4CommandAck(
            execution_id=execution_id,
            command_id=command_id,
            status=status,
            ack_name='ACCEPTED' if accepted else 'DENIED',
            failure_code=None if accepted else 'PX4_ACK_DENIED',
        )

    async def cancel(self, execution_id: str) -> bool:
        self.cancelled_execution_ids.append(execution_id)
        return True


def make_plan() -> Px4NativeSkillPlan:
    '''Construct a two-command takeoff transport plan.'''

    return Px4NativeSkillPlan(
        execution_id='exec-takeoff',
        skill_name=SkillName.TAKEOFF,
        commands=(
            Px4NativeCommand(22, VehicleCommandParameters(param7=50.0)),
            Px4NativeCommand(400, VehicleCommandParameters(param1=1.0)),
        ),
    )


def test_commands_are_submitted_in_order_with_one_timeout_budget() -> None:
    '''两条命令按顺序提交，第二条只使用剩余超时预算。'''

    asyncio.run(_assert_commands_are_submitted_in_order())


async def _assert_commands_are_submitted_in_order() -> None:
    submitter = ScriptedCommandSubmitter(
        [Px4CommandAckStatus.ACCEPTED, Px4CommandAckStatus.ACCEPTED]
    )
    receipt = await Px4NativePlanExecutor(submitter).execute(make_plan(), timeout_s=1.0)

    assert [command_id for command_id, _ in submitter.submissions] == [22, 400]
    assert 0.0 < submitter.submissions[1][1] <= submitter.submissions[0][1] <= 1.0
    assert receipt.all_commands_accepted is True
    assert receipt.terminal_ack.command_id == 400


def test_rejected_ack_stops_the_remaining_plan() -> None:
    '''首条命令被拒绝后，不得继续发送解锁命令。'''

    async def scenario() -> None:
        submitter = ScriptedCommandSubmitter([Px4CommandAckStatus.REJECTED])
        receipt = await Px4NativePlanExecutor(submitter).execute(
            make_plan(), timeout_s=1.0
        )

        assert [command_id for command_id, _ in submitter.submissions] == [22]
        assert receipt.all_commands_accepted is False
        assert receipt.terminal_ack.failure_code == 'PX4_ACK_DENIED'

    asyncio.run(scenario())


def test_expired_shared_budget_stops_before_the_next_submission() -> None:
    '''首条命令耗尽总预算后，第二条命令生成本地超时证据。'''

    async def scenario() -> None:
        submitter = ScriptedCommandSubmitter(
            [Px4CommandAckStatus.ACCEPTED], delay_s=0.01
        )
        receipt = await Px4NativePlanExecutor(submitter).execute(
            make_plan(), timeout_s=0.001
        )

        assert [command_id for command_id, _ in submitter.submissions] == [22]
        assert receipt.terminal_ack.command_id == 400
        assert receipt.terminal_ack.status is Px4CommandAckStatus.TIMED_OUT
        assert receipt.terminal_ack.failure_code == 'SKILL_TIMEOUT_BUDGET_EXHAUSTED'

    asyncio.run(scenario())


def test_cancel_is_delegated_to_the_active_transport() -> None:
    '''计划取消请求由命令传输层处理。'''

    async def scenario() -> None:
        submitter = ScriptedCommandSubmitter([])
        executor = Px4NativePlanExecutor(submitter)

        assert await executor.cancel('exec-takeoff') is True
        assert submitter.cancelled_execution_ids == ['exec-takeoff']

    asyncio.run(scenario())
