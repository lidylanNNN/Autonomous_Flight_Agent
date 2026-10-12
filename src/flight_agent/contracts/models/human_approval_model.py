'''Contracts for one proposal-bound human approval request.'''

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PositiveFloat, model_validator

from flight_agent.contracts.models.proposal_model import SkillProposal


class HumanApprovalStatus(StrEnum):
    PENDING = 'PENDING'
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'
    TIMED_OUT = 'TIMED_OUT'


class HumanApprovalRequest(BaseModel):
    '''冻结审批对象和截止时间所需的信息。'''

    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)

    approval_id: str = Field(min_length=1)
    proposal: SkillProposal
    requested_at: AwareDatetime
    timeout_s: PositiveFloat


class HumanApprovalResult(BaseModel):
    '''审批终态只证明人工决策，不代表 Safety 已放行。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    approval_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: HumanApprovalStatus

    @model_validator(mode='after')
    def must_be_terminal(self) -> HumanApprovalResult:
        if self.status is HumanApprovalStatus.PENDING:
            raise ValueError('approval result must be terminal')
        return self


def proposal_digest(proposal: SkillProposal) -> str:
    '''固定字段顺序后为完整Proposal生成审批绑定摘要。'''

    payload = json.dumps(
        proposal.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False
    )
    return sha256(payload.encode('utf-8')).hexdigest()
