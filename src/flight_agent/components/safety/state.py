'''Deterministic proposal WorldState consistency checks.'''

from datetime import datetime

from flight_agent.contracts import (
    AtomicActionProposal,
    MissionConstraints,
    SafetyReasonCode,
    WorldState,
)


def check_proposal_state(
    proposal: AtomicActionProposal,
    constraints: MissionConstraints,
    state: WorldState,
    *,
    now: datetime | None = None,
) -> tuple[SafetyReasonCode, ...]:
    '''按固定顺序检查状态新鲜度和Proposal引用的状态版本。'''

    reasons: list[SafetyReasonCode] = []
    if not state.is_fresh(constraints.max_state_age_ms, now):
        reasons.append(SafetyReasonCode.STALE_STATE)
    if proposal.based_on_state_id != state.state_id:
        reasons.append(SafetyReasonCode.STATE_ID_MISMATCH)
    return tuple(reasons)
