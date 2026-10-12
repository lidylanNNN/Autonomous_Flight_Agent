'''Asynchronous human approval boundary.'''

from typing import Protocol, runtime_checkable

from flight_agent.contracts.models.human_approval_model import (
    HumanApprovalRequest,
    HumanApprovalResult,
)


@runtime_checkable
class HumanApprovalProtocol(Protocol):
    '''返回绑定到原始 Proposal 的审批终态。'''

    async def request_approval(self, request: HumanApprovalRequest) -> HumanApprovalResult:
        ...
