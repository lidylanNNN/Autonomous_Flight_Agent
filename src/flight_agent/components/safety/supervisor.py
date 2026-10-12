'''确定性任务提案仲裁，不负责执行命令。'''

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256

from flight_agent.components.safety.authority import check_control_authority
from flight_agent.components.safety.battery import check_battery
from flight_agent.components.safety.envelope import (
    check_controller_speed_limit,
    check_flight_envelope,
    check_mission_radius,
)
from flight_agent.components.safety.geofence import check_geofence
from flight_agent.components.safety.health import check_system_health
from flight_agent.components.safety.home import check_home_reference
from flight_agent.components.safety.schema import check_proposal_schema
from flight_agent.components.safety.sequence import check_command_sequence
from flight_agent.components.safety.state import check_proposal_state
from flight_agent.components.safety.vehicle_state import check_vehicle_state
from flight_agent.contracts import (
    ControlAuthority,
    ControllerSpeedLimitProtocol,
    MissionContract,
    Px4ParameterReadError,
    SafetyDecision,
    SafetyDecisionType,
    SafetyReasonCode,
    SkillName,
    SkillProposal,
    WorldState,
    parse_skill_arguments,
)

_PARAMETER_MAX_AGE_S = 2.0
_LOGGER = logging.getLogger(__name__)


class SafetySupervisor:
    '''组合任务安全检查，读取当前PX4限速并返回唯一仲裁结果。'''

    def __init__(
        self,
        speed_limit_source: ControllerSpeedLimitProtocol,
        *,
        policy_version: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        '''固定限速来源、策略版本及可注入的时钟。'''

        if not policy_version:
            raise ValueError('policy_version must not be empty')
        self._speed_limit_source = speed_limit_source
        self._policy_version = policy_version
        self._clock = clock or (lambda: datetime.now(UTC))

    async def evaluate(
        self,
        proposal: SkillProposal,
        contract: MissionContract,
        state: WorldState,
        authority: ControlAuthority,
    ) -> SafetyDecision:
        '''按冻结顺序检查所有可计算条件，失败时禁止后续放行。'''

        constraints = contract.constraints
        schema_reasons = check_proposal_schema(proposal, contract)
        reasons: list[SafetyReasonCode] = list(schema_reasons)
        reasons.extend(check_proposal_state(proposal, contract, state, now=self._clock()))
        reasons.extend(check_control_authority(authority))

        skill_name: SkillName | None = None
        if not schema_reasons:
            skill_name = SkillName(proposal.skill_name)
            arguments = parse_skill_arguments(skill_name, proposal.arguments)
            reasons.extend(check_vehicle_state(skill_name, state))
            reasons.extend(check_command_sequence(skill_name, state))
            reasons.extend(check_geofence(skill_name, arguments, state, constraints.geofence))
            reasons.extend(check_flight_envelope(skill_name, arguments, constraints))
            reasons.extend(check_mission_radius(skill_name, arguments, state, constraints))
            reasons.extend(check_battery(state, constraints))
            reasons.extend(check_system_health(skill_name, state))
            reasons.extend(check_home_reference(skill_name, state, constraints))

            if not reasons:
                try:
                    speed_limit = await self._speed_limit_source.read_validated_speed_limit(
                        max_age_s=_PARAMETER_MAX_AGE_S
                    )
                except Px4ParameterReadError as error:
                    _LOGGER.warning('PX4 speed limit evidence unavailable: %s', error, exc_info=True)
                    reasons.append(SafetyReasonCode.SYSTEM_UNHEALTHY)
                else:
                    reasons.extend(check_controller_speed_limit(speed_limit, constraints))
                late_state_reasons = check_proposal_state(
                    proposal, contract, state, now=self._clock()
                )
                reasons[:0] = late_state_reasons

        ordered_reasons = tuple(dict.fromkeys(reasons))
        if ordered_reasons:
            decision_type = SafetyDecisionType.REJECT
        elif skill_name in constraints.human_approval_skills:
            decision_type = SafetyDecisionType.HUMAN_APPROVAL
            ordered_reasons = (SafetyReasonCode.HUMAN_APPROVAL_REQUIRED,)
        else:
            decision_type = SafetyDecisionType.APPROVE
            ordered_reasons = (SafetyReasonCode.CHECKS_PASSED,)

        identity = json.dumps(
            {
                'proposal': proposal.model_dump(mode='json'),
                'contract': contract.model_dump(mode='json'),
                'state': state.model_dump(mode='json'),
                'authority': authority.value,
                'decision': decision_type.value,
                'reasons': [reason.value for reason in ordered_reasons],
                'policy_version': self._policy_version,
            },
            sort_keys=True,
            separators=(',', ':'),
            allow_nan=False,
        )
        return SafetyDecision(
            decision_id=sha256(identity.encode('utf-8')).hexdigest(),
            proposal_id=proposal.proposal_id,
            decision=decision_type,
            reason_codes=ordered_reasons,
            checked_state_id=state.state_id,
            policy_version=self._policy_version,
        )
