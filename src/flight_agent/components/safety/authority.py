'''Deterministic control authority checks.'''

from flight_agent.contracts import ControlAuthority, SafetyReasonCode


def check_control_authority(
    authority: ControlAuthority,
) -> tuple[SafetyReasonCode, ...]:
    '''只允许Agent持有控制权时执行普通任务Proposal。'''

    if authority is ControlAuthority.AGENT_ALLOWED:
        return ()
    return (SafetyReasonCode.AUTHORITY_NOT_GRANTED,)
