'''M4 control authority check unit tests.'''

import pytest

from flight_agent.components.safety import check_control_authority
from flight_agent.contracts import ControlAuthority, SafetyReasonCode


def test_agent_authority_is_allowed() -> None:
    '''验证Agent持有控制权时通过Authority检查。'''

    assert check_control_authority(ControlAuthority.AGENT_ALLOWED) == ()


@pytest.mark.parametrize(
    'authority',
    [
        ControlAuthority.HUMAN_CONTROL_ACTIVE,
        ControlAuthority.RECOVERY_ACTIVE,
        ControlAuthority.PX4_FAILSAFE_ACTIVE,
    ],
)
def test_higher_priority_authority_blocks_agent(
    authority: ControlAuthority,
) -> None:
    '''验证人工、恢复和PX4 Failsafe控制权均阻断Agent。'''

    assert check_control_authority(authority) == (
        SafetyReasonCode.AUTHORITY_NOT_GRANTED,
    )
