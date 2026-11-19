'''Agent-side pre-dispatch safety gate for flight skills.'''

from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from uuid import uuid4

from flight_agent.components.safety.supervisor import SafetySupervisor
from flight_agent.components.skills.registry import get_skill_spec
from flight_agent.components.tracing import TraceRecorder
from flight_agent.contracts import (
    ApprovedSkillCommand,
    AtomicActionProposal,
    ControlAuthority,
    FlightExecutionBackendProtocol,
    MissionConstraints,
    SafetyDecision,
    SafetyDecisionType,
    SkillName,
    SkillResult,
    WorldState,
    parse_skill_arguments,
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
    ) -> tuple[SafetyDecision, SkillResult | None]:
        '''先记录决策，仅在批准且 Trace 成功后创建执行身份并派发。'''

        return await self._submit(proposal.model_copy(deep=True), constraints)

    async def submit_action(
        self, skill_name: SkillName, arguments: dict[str, object],
        proposal_id: str, constraints: MissionConstraints,
    ) -> tuple[SafetyDecision, SkillResult | None]:
        '''等待参数后以检查所用状态创建生产提案，不改写外部绑定提案。'''
        arguments_snapshot = deepcopy(arguments)

        def make_proposal(state: WorldState) -> AtomicActionProposal:
            return AtomicActionProposal(proposal_id=proposal_id, skill_name=skill_name.value,
                arguments=arguments_snapshot, based_on_state_id=state.state_id)

        return await self._submit(make_proposal, constraints)

    async def _submit(
        self, proposal: AtomicActionProposal | Callable[[WorldState], AtomicActionProposal],
        constraints: MissionConstraints,
    ) -> tuple[SafetyDecision, SkillResult | None]:
        constraints_snapshot = constraints.model_copy(deep=True)
        speed_limit_reading = deepcopy(await self._supervisor.read_speed_limit())
        state = (await self._backend.get_world_state()).model_copy(deep=True)
        proposal_snapshot = proposal(state) if callable(proposal) else proposal
        decision = await self._supervisor.evaluate(
            proposal_snapshot,
            constraints_snapshot,
            state,
            self._authority_reader(),
            speed_limit_reading=speed_limit_reading,
        )
        self._recorder.record_safety_decision(proposal_snapshot, decision)
        if decision.decision is not SafetyDecisionType.APPROVE:
            return decision, None
        skill_name = SkillName(proposal_snapshot.skill_name)
        command = ApprovedSkillCommand(
            execution_id=f'exec-{uuid4().hex}',
            proposal_id=proposal_snapshot.proposal_id,
            decision_id=decision.decision_id,
            skill_name=skill_name,
            arguments=parse_skill_arguments(skill_name, proposal_snapshot.arguments),
            timeout_s=get_skill_spec(skill_name).timeout_s,
            approved_state_id=decision.checked_state_id,
        )
        try:
            result = await self._backend.execute(command)
        except Exception as error:
            try:
                self._recorder.record_skill_execution_error(command, error)
            except Exception as trace_error:  # noqa: BLE001 - preserve the original backend error
                diagnostic = (
                    'SKILL_EXECUTION_ERROR_TRACE_WRITE_FAILED: '
                    f'execution_id={command.execution_id}; {trace_error}'
                )
                error.add_note(diagnostic)
                logging.getLogger(__name__).error(diagnostic)
            raise
        try:
            self._recorder.record_skill_result(command, result)
        except Exception as error:
            raise RuntimeError(
                f'SKILL_RESULT_TRACE_WRITE_FAILED: execution_id={command.execution_id}; '
                f'backend returned status={result.status.value}; '
                f'action may have executed; do not retry: {error}'
            ) from error
        return decision, result
