'''M4 deterministic safety decision contract models.'''

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictFrozenModel(BaseModel):
    '''禁止额外字段并冻结Safety契约。'''

    model_config = ConfigDict(
        extra='forbid', frozen=True, allow_inf_nan=False, str_strip_whitespace=True
    )


class ControlAuthority(StrEnum):
    '''当前允许控制飞行器的最高优先级主体。'''

    AGENT_ALLOWED = 'agent_allowed'
    HUMAN_CONTROL_ACTIVE = 'human_control_active'
    RECOVERY_ACTIVE = 'recovery_active'
    PX4_FAILSAFE_ACTIVE = 'px4_failsafe_active'


class SafetyDecisionType(StrEnum):
    '''Safety Supervisor对Proposal的最终仲裁类型。'''

    APPROVE = 'APPROVE'
    REJECT = 'REJECT'
    HUMAN_APPROVAL = 'HUMAN_APPROVAL'


class SafetyReasonCode(StrEnum):
    '''M4确定性检查使用的稳定机器可读原因码。'''

    CHECKS_PASSED = 'CHECKS_PASSED'
    INVALID_SCHEMA = 'INVALID_SCHEMA'
    STALE_STATE = 'STALE_STATE'
    STATE_ID_MISMATCH = 'STATE_ID_MISMATCH'
    SKILL_NOT_ALLOWED = 'SKILL_NOT_ALLOWED'
    AUTHORITY_NOT_GRANTED = 'AUTHORITY_NOT_GRANTED'
    INVALID_VEHICLE_STATE = 'INVALID_VEHICLE_STATE'
    INVALID_COMMAND_SEQUENCE = 'INVALID_COMMAND_SEQUENCE'
    WAYPOINT_OUTSIDE_GEOFENCE = 'WAYPOINT_OUTSIDE_GEOFENCE'
    ROUTE_OUTSIDE_GEOFENCE = 'ROUTE_OUTSIDE_GEOFENCE'
    ALTITUDE_ABOVE_ENVELOPE = 'ALTITUDE_ABOVE_ENVELOPE'
    SPEED_ABOVE_ENVELOPE = 'SPEED_ABOVE_ENVELOPE'
    MISSION_RADIUS_EXCEEDED = 'MISSION_RADIUS_EXCEEDED'
    INSUFFICIENT_BATTERY = 'INSUFFICIENT_BATTERY'
    SYSTEM_UNHEALTHY = 'SYSTEM_UNHEALTHY'
    HOME_REFERENCE_UNAVAILABLE = 'HOME_REFERENCE_UNAVAILABLE'
    HUMAN_APPROVAL_REQUIRED = 'HUMAN_APPROVAL_REQUIRED'
    HUMAN_APPROVAL_REJECTED = 'HUMAN_APPROVAL_REJECTED'
    HUMAN_APPROVAL_TIMEOUT = 'HUMAN_APPROVAL_TIMEOUT'


class SafetyDecision(_StrictFrozenModel):
    '''记录一次不可绕过的确定性Safety仲裁结果。'''

    decision_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    decision: SafetyDecisionType
    reason_codes: tuple[SafetyReasonCode, ...] = Field(min_length=1)
    checked_state_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)

    @model_validator(mode='after')
    def reasons_match_decision(self) -> SafetyDecision:
        '''限制三类Decision可携带的Reason Code组合。'''

        reasons = set(self.reason_codes)
        if len(reasons) != len(self.reason_codes):
            raise ValueError('reason_codes must not contain duplicates')
        if self.decision is SafetyDecisionType.APPROVE:
            if self.reason_codes != (SafetyReasonCode.CHECKS_PASSED,):
                raise ValueError('APPROVE requires only CHECKS_PASSED')
        elif self.decision is SafetyDecisionType.HUMAN_APPROVAL:
            if self.reason_codes != (SafetyReasonCode.HUMAN_APPROVAL_REQUIRED,):
                raise ValueError('HUMAN_APPROVAL requires only HUMAN_APPROVAL_REQUIRED')
        elif reasons & {
            SafetyReasonCode.CHECKS_PASSED,
            SafetyReasonCode.HUMAN_APPROVAL_REQUIRED,
        }:
            raise ValueError('REJECT requires rejection reason codes')
        return self
