'''Agent-side pre-dispatch safety gate for flight skills.'''

from __future__ import annotations

from collections.abc import Callable

from flight_agent.components.safety.supervisor import SafetySupervisor
from flight_agent.components.tracing import TraceRecorder
from flight_agent.contracts import (
    AtomicActionProposal,
    ControlAuthority,
    FlightExecutionBackendProtocol,
    MissionConstraints,
    SafetyDecision,
    SkillResult,
)


class SafetyExecutionGate:
    '''Read trusted state and authority, trace the decision, then dispatch if approved.'''

    def __init__(
        self,
        supervisor: SafetySupervisor,
        backend: FlightExecutionBackendProtocol,
        recorder: TraceRecorder,
        authority_reader: Callable[[], ControlAuthority],
    ) -> None:
        self._supervisor = supervisor
        self._backend = backend
        self._recorder = recorder
        self._authority_reader = authority_reader

    async def submit(
        self,
        proposal: AtomicActionProposal,
        constraints: MissionConstraints,
        *,
        expected_mission_id: str,
        execution_id: str,
    ) -> tuple[SafetyDecision, SkillResult | None]:
        '''Return the decision without dispatch unless Safety approved the proposal.'''

        proposal_snapshot = proposal.model_copy(deep=True)
        state = await self._backend.get_world_state()
        decision, command = await self._supervisor.prepare_command(
            proposal_snapshot,
            constraints,
            state,
            self._authority_reader(),
            expected_mission_id=expected_mission_id,
            execution_id=execution_id,
        )
        self._recorder.record_safety_decision(proposal_snapshot, decision)
        if command is None:
            return decision, None
        return decision, await self._backend.execute(command)
