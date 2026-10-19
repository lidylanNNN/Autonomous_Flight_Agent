'''M4 human approval state machine and mock tests.'''

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from eval_harness.mocks.human_approval import MockHumanApproval
from flight_agent.components.safety.human_approval import (
    HumanApprovalEvent,
    HumanApprovalRegistry,
    HumanApprovalStateMachine,
)
from flight_agent.contracts import (
    AtomicActionProposal,
    HumanApprovalProtocol,
    HumanApprovalRequest,
    HumanApprovalResult,
    HumanApprovalStatus,
)
from flight_agent.contracts.models.human_approval_model import proposal_digest


def make_request(
    *, approval_id: str = 'approval-1', proposal_id: str = 'proposal-1'
) -> HumanApprovalRequest:
    return HumanApprovalRequest(
        approval_id=approval_id,
        proposal=AtomicActionProposal(
            proposal_id=proposal_id,
            mission_id='mission-1',
            plan_revision=0,
            step_id='step-1',
            skill_name='takeoff',
            arguments={'target_altitude_m': 10.0},
            based_on_state_id='state-1',
        ),
        requested_at=datetime(2026, 10, 10, tzinfo=UTC),
        timeout_s=3.0,
    )


@pytest.mark.parametrize(
    'outcome',
    [HumanApprovalStatus.APPROVED, HumanApprovalStatus.REJECTED, HumanApprovalStatus.TIMED_OUT],
)
def test_mock_returns_scripted_terminal_result(outcome: HumanApprovalStatus) -> None:
    request = make_request()
    mock = MockHumanApproval({'proposal-1': outcome})

    result = asyncio.run(mock.request_approval(request))

    assert isinstance(mock, HumanApprovalProtocol)
    assert result.approval_id == 'approval-1'
    assert result.proposal_id == 'proposal-1'
    assert result.proposal_digest == proposal_digest(request.proposal)
    assert result.status is outcome


def test_mock_defaults_to_timeout_not_approval() -> None:
    result = asyncio.run(MockHumanApproval().request_approval(make_request()))

    assert result.status is HumanApprovalStatus.TIMED_OUT


def test_approval_is_bound_to_full_proposal_contents() -> None:
    request = make_request()
    result = asyncio.run(
        MockHumanApproval({'proposal-1': HumanApprovalStatus.APPROVED}).request_approval(request)
    )
    changed = request.proposal.model_copy(update={'arguments': {'target_altitude_m': 20.0}})

    assert result.proposal_digest != proposal_digest(changed)


def test_state_machine_rejects_wrong_proposal_without_changing_pending_state() -> None:
    request = make_request()
    machine = HumanApprovalStateMachine(request)

    with pytest.raises(ValueError, match='does not match'):
        machine.transition(
            HumanApprovalEvent.APPROVE, proposal_id='proposal-2', now=request.requested_at,
        )

    assert machine.status is HumanApprovalStatus.PENDING


def test_late_approval_becomes_timeout() -> None:
    request = make_request()
    machine = HumanApprovalStateMachine(request)

    result = machine.transition(
        HumanApprovalEvent.APPROVE, proposal_id=request.proposal.proposal_id,
        now=request.requested_at + timedelta(seconds=request.timeout_s),
    )

    assert result.status is HumanApprovalStatus.TIMED_OUT


def test_cannot_expire_before_deadline_or_resolve_twice() -> None:
    request = make_request()
    machine = HumanApprovalStateMachine(request)

    with pytest.raises(ValueError, match='not timed out'):
        machine.transition(HumanApprovalEvent.TIMEOUT, now=request.requested_at)
    assert machine.status is HumanApprovalStatus.PENDING

    machine.transition(
        HumanApprovalEvent.REJECT, proposal_id=request.proposal.proposal_id,
        now=request.requested_at,
    )
    with pytest.raises(ValueError, match='already completed'):
        machine.transition(
            HumanApprovalEvent.APPROVE, proposal_id=request.proposal.proposal_id,
            now=request.requested_at,
        )


def test_repeated_approval_or_proposal_id_is_rejected() -> None:
    registry = HumanApprovalRegistry()
    registry.submit(make_request())

    with pytest.raises(ValueError, match='already submitted'):
        registry.submit(make_request())
    with pytest.raises(ValueError, match='already submitted'):
        registry.submit(make_request(approval_id='approval-2'))
    with pytest.raises(ValueError, match='already submitted'):
        registry.submit(make_request(proposal_id='proposal-2'))


def test_mutating_original_request_does_not_change_approval_snapshot() -> None:
    request = make_request()
    original_digest = proposal_digest(request.proposal)
    registry = HumanApprovalRegistry()
    machine = registry.submit(request)
    request.proposal.arguments['target_altitude_m'] = 20.0

    result = registry.get(request.approval_id).transition(
        HumanApprovalEvent.APPROVE, proposal_id=machine.proposal_id,
        now=request.requested_at,
    )

    assert result.proposal_digest == original_digest
    assert result.proposal_digest != proposal_digest(request.proposal)


def test_invalid_approval_inputs_fail_closed() -> None:
    request = make_request()
    machine = HumanApprovalStateMachine(request)

    with pytest.raises(TypeError, match='unsupported'):
        machine.transition('UNKNOWN', now=request.requested_at)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match='unsupported'):
        machine.transition('APPROVE', now=request.requested_at)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='aware'):
        machine.transition(
            HumanApprovalEvent.APPROVE, proposal_id=request.proposal.proposal_id,
            now=request.requested_at.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError, match='terminal'):
        HumanApprovalResult(
            approval_id='approval-1', proposal_id='proposal-1',
            proposal_digest=proposal_digest(request.proposal),
            status=HumanApprovalStatus.PENDING,
        )


def test_mock_rejects_invalid_scripted_outcome() -> None:
    with pytest.raises(ValueError, match='terminal'):
        MockHumanApproval({'proposal-1': HumanApprovalStatus.PENDING})
    with pytest.raises(ValueError, match='terminal'):
        MockHumanApproval({'proposal-1': 'APPROVED'})  # type: ignore[dict-item]


def test_independent_requests_have_independent_states() -> None:
    registry = HumanApprovalRegistry()
    first = registry.submit(make_request())
    second = registry.submit(make_request(approval_id='approval-2', proposal_id='proposal-2'))

    first.transition(HumanApprovalEvent.REJECT, proposal_id=first.proposal_id,
                     now=make_request().requested_at)

    assert first.status is HumanApprovalStatus.REJECTED
    assert second.status is HumanApprovalStatus.PENDING


def test_decision_just_before_deadline_is_accepted() -> None:
    request = make_request()
    machine = HumanApprovalStateMachine(request)

    result = machine.transition(
        HumanApprovalEvent.APPROVE, proposal_id=machine.proposal_id,
        now=request.requested_at + timedelta(seconds=request.timeout_s - 0.001),
    )

    assert result.status is HumanApprovalStatus.APPROVED
