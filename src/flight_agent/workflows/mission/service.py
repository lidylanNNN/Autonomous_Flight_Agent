'''从用户语句贯通到 Safety 与飞行执行的最小任务主链。'''

from __future__ import annotations

from flight_agent.components.safety import SafetyExecutionGate
from flight_agent.components.skills.registry import get_skill_spec
from flight_agent.contracts import (
    AtomicActionProposal,
    FlightExecutionBackendProtocol,
    MissionContract,
    MissionPlan,
    MissionRequest,
    MissionRunResult,
    MissionRunStatus,
    PlanStep,
    SafetyDecision,
    SafetyDecisionType,
    SkillExecutionStatus,
    SkillName,
    SkillResult,
    VerificationResult,
    VerificationStatus,
    WorldState,
)


class MissionService:
    '''持有受控执行门，按用户语句驱动任务的当前可用部分。'''

    def __init__(
        self,
        contract_template: MissionContract,
        backend: FlightExecutionBackendProtocol,
        gate: SafetyExecutionGate,
    ) -> None:
        '''固定不可由规划来源放宽的任务约束及执行依赖。'''

        self._contract_template = contract_template
        self._backend = backend
        self._gate = gate

    async def run(self, request: MissionRequest) -> MissionRunResult:
        '''贯通语句、脚本计划、Safety、执行与占位验证。'''

        contract = self._build_contract(request)
        initial_state = await self._backend.get_world_state()
        plan = self._plan_placeholder(request, contract, initial_state)
        if plan is None:
            return MissionRunResult(
                mission_id=request.mission_id,
                status=MissionRunStatus.UNSUPPORTED,
                reason='PLANNER_NOT_IMPLEMENTED_FOR_INSTRUCTION',
            )

        decisions: list[SafetyDecision] = []
        results: list[SkillResult] = []
        verifications: list[VerificationResult] = []
        step_index = 0
        while step_index < len(plan.steps):
            step = plan.steps[step_index]
            state = await self._backend.get_world_state()
            attempt_id = f'{request.mission_id}-{step.step_id}'
            if plan.plan_revision:
                attempt_id += f'-revision-{plan.plan_revision}'
            proposal = AtomicActionProposal(
                proposal_id=attempt_id,
                mission_id=request.mission_id,
                plan_revision=plan.plan_revision,
                step_id=step.step_id,
                skill_name=step.skill_name.value,
                arguments=step.arguments,
                based_on_state_id=state.state_id,
            )
            decision, result = await self._gate.submit(
                proposal,
                contract.constraints,
                expected_mission_id=contract.mission_id,
                execution_id=f'{attempt_id}-execution',
            )
            decisions.append(decision)
            if decision.decision is not SafetyDecisionType.APPROVE:
                recovered_plan = None
                if decision.decision is SafetyDecisionType.REJECT:
                    recovered_plan = self._recover_placeholder(decision)
                if recovered_plan is not None:
                    self._validate_recovered_plan(recovered_plan, plan, contract)
                    plan = recovered_plan
                    step_index = 0
                    continue
                return MissionRunResult(
                    mission_id=request.mission_id,
                    status=MissionRunStatus.REJECTED,
                    plan=plan,
                    decisions=tuple(decisions),
                    skill_results=tuple(results),
                    verifications=tuple(verifications),
                    reason=decision.reason_codes[0].value,
                )
            if result is None:
                raise RuntimeError('approved command did not produce a SkillResult')
            results.append(result)
            verifications.append(self._verify_placeholder(result))
            if result.status is not SkillExecutionStatus.SUCCEEDED:
                recovered_plan = self._recover_placeholder(result)
                if recovered_plan is not None:
                    self._validate_recovered_plan(recovered_plan, plan, contract)
                    plan = recovered_plan
                    step_index = 0
                    continue
                return MissionRunResult(
                    mission_id=request.mission_id,
                    status=MissionRunStatus.FAILED,
                    plan=plan,
                    decisions=tuple(decisions),
                    skill_results=tuple(results),
                    verifications=tuple(verifications),
                    reason='RECOVERY_NOT_IMPLEMENTED',
                )
            step_index += 1

        return MissionRunResult(
            mission_id=request.mission_id,
            status=MissionRunStatus.UNVERIFIED,
            plan=plan,
            decisions=tuple(decisions),
            skill_results=tuple(results),
            verifications=tuple(verifications),
            reason='STATE_VERIFIER_NOT_IMPLEMENTED',
        )

    def _build_contract(self, request: MissionRequest) -> MissionContract:
        '''只将用户目标绑定到预先冻结的系统安全约束。'''

        if request.mission_id != self._contract_template.mission_id:
            raise ValueError('mission request and frozen contract IDs differ')
        return self._contract_template.model_copy(update={'objective': request.instruction})

    @staticmethod
    def _plan_placeholder(
        request: MissionRequest, contract: MissionContract, state: WorldState
    ) -> MissionPlan | None:
        '''仅识别一个冻结的测试语句，其他语句不产生 Proposal。'''

        if request.instruction.strip() != '起飞到10米':
            return None
        skill_name = SkillName.TAKEOFF
        return MissionPlan(
            mission_id=request.mission_id,
            plan_revision=0,
            contract_version=contract.contract_version,
            based_on_state_id=state.state_id,
            steps=(
                PlanStep(
                    step_id='step-1',
                    skill_name=skill_name,
                    arguments={'target_altitude_m': 10.0},
                    success_criteria={},
                    timeout_s=get_skill_spec(skill_name).timeout_s,
                ),
            ),
        )

    @staticmethod
    def _verify_placeholder(result: SkillResult) -> VerificationResult:
        '''保留状态验证节点，但不把 Backend 成功伪装成任务完成。'''

        return VerificationResult(
            verification_id=f'{result.execution_id}-verification',
            execution_id=result.execution_id,
            result=VerificationStatus.INCONCLUSIVE,
            observed_state_ids=(result.end_state_id,),
            failure_code='STATE_VERIFIER_NOT_IMPLEMENTED',
        )

    @staticmethod
    def _recover_placeholder(event: SafetyDecision | SkillResult) -> MissionPlan | None:
        '''保留拒绝和执行失败的恢复入口；当前不生成恢复计划。'''

        del event
        return None

    @staticmethod
    def _validate_recovered_plan(
        recovered: MissionPlan, previous: MissionPlan, contract: MissionContract
    ) -> None:
        '''A recovery revision cannot change the mission or its frozen contract.'''

        if (
            recovered.mission_id != previous.mission_id
            or recovered.contract_version != contract.contract_version
            or recovered.plan_revision != previous.plan_revision + 1
        ):
            raise ValueError('recovery plan must advance the same mission and contract')
