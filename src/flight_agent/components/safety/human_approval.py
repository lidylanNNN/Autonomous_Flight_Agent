'''Proposal-bound approval transitions and request registry.'''

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from flight_agent.contracts import (
    HumanApprovalRequest,
    HumanApprovalResult,
    HumanApprovalStatus,
)
from flight_agent.contracts.models.human_approval_model import proposal_digest


class HumanApprovalEvent(StrEnum):
    APPROVE = 'APPROVE'
    REJECT = 'REJECT'
    TIMEOUT = 'TIMEOUT'


class HumanApprovalStateMachine:
    '''One request: current state plus guarded event-to-state transitions.'''

    def __init__(self, request: HumanApprovalRequest) -> None:
        self._request = HumanApprovalRequest.model_validate(request.model_dump(mode='python'))
        self._proposal_digest = proposal_digest(self._request.proposal)
        self._deadline = self._request.requested_at + timedelta(seconds=self._request.timeout_s)
        self._status = HumanApprovalStatus.PENDING

    @property
    def status(self) -> HumanApprovalStatus:
        return self._status

    @property
    def approval_id(self) -> str:
        return self._request.approval_id

    @property
    def proposal_id(self) -> str:
        return self._request.proposal.proposal_id

    def transition(
        self,
        event: HumanApprovalEvent,
        *,
        now: datetime,
        proposal_id: str | None = None,
    ) -> HumanApprovalResult:
        '''Process one event; a decision at or after the deadline becomes timeout.'''

        if self._status is not HumanApprovalStatus.PENDING:
            raise ValueError('approval already completed')
        if not isinstance(event, HumanApprovalEvent):
            raise TypeError('unsupported approval event')
        if now.tzinfo is None or now < self._request.requested_at:
            raise ValueError('approval time must be aware and not before request')

        if event is HumanApprovalEvent.TIMEOUT:
            if now < self._deadline:
                raise ValueError('approval has not timed out')
            next_status = HumanApprovalStatus.TIMED_OUT
        elif event in (HumanApprovalEvent.APPROVE, HumanApprovalEvent.REJECT):
            if proposal_id != self.proposal_id:
                raise ValueError('approval proposal does not match request')
            if now >= self._deadline:
                next_status = HumanApprovalStatus.TIMED_OUT
            elif event is HumanApprovalEvent.APPROVE:
                next_status = HumanApprovalStatus.APPROVED
            else:
                next_status = HumanApprovalStatus.REJECTED
        else:
            raise ValueError('unsupported approval event')

        self._status = next_status
        return HumanApprovalResult(
            approval_id=self.approval_id,
            proposal_id=self.proposal_id,
            proposal_digest=self._proposal_digest,
            status=self._status,
        )


class HumanApprovalRegistry:
    '''Keep independent approval machines and prevent duplicate request identities.'''

    def __init__(self) -> None:
        self._machines: dict[str, HumanApprovalStateMachine] = {}
        self._proposal_ids: set[str] = set()

    def submit(self, request: HumanApprovalRequest) -> HumanApprovalStateMachine:
        if request.approval_id in self._machines or request.proposal.proposal_id in self._proposal_ids:
            raise ValueError('approval or proposal already submitted')
        machine = HumanApprovalStateMachine(request)
        self._machines[machine.approval_id] = machine
        self._proposal_ids.add(machine.proposal_id)
        return machine

    def get(self, approval_id: str) -> HumanApprovalStateMachine:
        return self._machines[approval_id]
