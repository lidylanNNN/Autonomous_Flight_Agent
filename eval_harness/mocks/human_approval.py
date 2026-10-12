'''Deterministic human approval replacement for evaluation and tests.'''

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from flight_agent.components.safety.human_approval import (
    HumanApprovalEvent,
    HumanApprovalRegistry,
)
from flight_agent.contracts import HumanApprovalRequest, HumanApprovalResult, HumanApprovalStatus


class MockHumanApproval:
    '''Feed scripted terminal events; no real human input or scheduler.'''

    def __init__(
        self,
        outcomes: Mapping[str, HumanApprovalStatus] | None = None,
    ) -> None:
        self._outcomes = dict(outcomes or {})
        if any(
            not isinstance(status, HumanApprovalStatus) or status not in {
                HumanApprovalStatus.APPROVED,
                HumanApprovalStatus.REJECTED,
                HumanApprovalStatus.TIMED_OUT,
            }
            for status in self._outcomes.values()
        ):
            raise ValueError('mock outcomes must be terminal')
        self._registry = HumanApprovalRegistry()

    async def request_approval(self, request: HumanApprovalRequest) -> HumanApprovalResult:
        machine = self._registry.submit(request)
        outcome = self._outcomes.get(machine.proposal_id, HumanApprovalStatus.TIMED_OUT)
        if outcome is HumanApprovalStatus.TIMED_OUT:
            return machine.transition(
                HumanApprovalEvent.TIMEOUT,
                now=request.requested_at + timedelta(seconds=request.timeout_s),
            )
        event = (
            HumanApprovalEvent.APPROVE
            if outcome is HumanApprovalStatus.APPROVED
            else HumanApprovalEvent.REJECT
        )
        return machine.transition(event, now=request.requested_at, proposal_id=machine.proposal_id)
