'''M4 vehicle state and command sequence check unit tests.'''

from datetime import UTC, datetime

import pytest

from flight_agent.components.safety import (
    check_command_sequence,
    check_vehicle_state,
)
from flight_agent.contracts import SafetyReasonCode, SkillName, WorldState


def make_state(
    *,
    armed: bool,
    landed: bool | None,
    flight_mode: str | None = None,
    position_valid: bool = True,
) -> WorldState:
    '''构造飞行生命周期检查使用的WorldState。'''

    return WorldState(
        state_id='state-1',
        source_timestamp_us=1,
        received_at=datetime(2026, 10, 8, tzinfo=UTC),
        state_age_ms=0,
        armed=armed,
        landed=landed,
        flight_mode=flight_mode,
        position_valid=position_valid,
        home_valid=True,
        link_healthy=True,
    )


@pytest.mark.parametrize(
    ('skill_name', 'state'),
    [
        (SkillName.TAKEOFF, make_state(armed=False, landed=True)),
        (SkillName.TAKEOFF, make_state(armed=True, landed=True)),
        (SkillName.GOTO, make_state(armed=True, landed=False)),
        (SkillName.RTL, make_state(armed=True, landed=False)),
    ],
)
def test_usable_vehicle_state_is_allowed(
    skill_name: SkillName,
    state: WorldState,
) -> None:
    '''验证明确且满足Skill观测要求的状态通过检查。'''

    assert check_vehicle_state(skill_name, state) == ()


@pytest.mark.parametrize(
    ('skill_name', 'state'),
    [
        (SkillName.TAKEOFF, make_state(armed=False, landed=None)),
        (SkillName.GOTO, make_state(armed=False, landed=False)),
        (
            SkillName.GOTO,
            make_state(armed=True, landed=False, position_valid=False),
        ),
        (
            SkillName.HOLD,
            make_state(armed=True, landed=False, position_valid=False),
        ),
        (
            SkillName.LAND,
            make_state(armed=True, landed=False, position_valid=False),
        ),
    ],
)
def test_unusable_vehicle_state_is_rejected(
    skill_name: SkillName,
    state: WorldState,
) -> None:
    '''验证未知生命周期、空中未解锁和无效位置被拒绝。'''

    assert check_vehicle_state(skill_name, state) == (
        SafetyReasonCode.INVALID_VEHICLE_STATE,
    )


@pytest.mark.parametrize(
    ('skill_name', 'landed'),
    [
        (SkillName.TAKEOFF, True),
        (SkillName.GOTO, False),
        (SkillName.HOLD, False),
        (SkillName.RTL, False),
        (SkillName.LAND, False),
    ],
)
def test_legal_command_sequence_is_allowed(
    skill_name: SkillName,
    landed: bool,
) -> None:
    '''验证地面与空中的合法Skill状态转换。'''

    state = make_state(armed=not landed, landed=landed)
    assert check_command_sequence(skill_name, state) == ()


@pytest.mark.parametrize(
    ('skill_name', 'landed'),
    [
        (SkillName.TAKEOFF, False),
        (SkillName.GOTO, True),
        (SkillName.HOLD, True),
        (SkillName.RTL, True),
        (SkillName.LAND, True),
    ],
)
def test_illegal_command_sequence_is_rejected(
    skill_name: SkillName,
    landed: bool,
) -> None:
    '''验证重复起飞和地面导航类Skill被拒绝。'''

    state = make_state(armed=not landed, landed=landed)
    assert check_command_sequence(skill_name, state) == (
        SafetyReasonCode.INVALID_COMMAND_SEQUENCE,
    )


@pytest.mark.parametrize('flight_mode', ['AUTO_TAKEOFF', 'AUTO_RTL', 'AUTO_LAND'])
def test_transition_mode_blocks_new_agent_skill(flight_mode: str) -> None:
    '''验证自动转换阶段不接受新的普通Agent Skill。'''

    state = make_state(armed=True, landed=False, flight_mode=flight_mode)
    for skill_name in SkillName:
        assert check_command_sequence(skill_name, state) == (
            SafetyReasonCode.INVALID_COMMAND_SEQUENCE,
        )


def test_unknown_landed_state_is_rejected_by_both_checks() -> None:
    '''验证单独调用Sequence时未知着陆状态也不会被视为通过。'''

    state = make_state(armed=False, landed=None)
    assert check_vehicle_state(SkillName.TAKEOFF, state) == (
        SafetyReasonCode.INVALID_VEHICLE_STATE,
    )
    assert check_command_sequence(SkillName.TAKEOFF, state) == (
        SafetyReasonCode.INVALID_VEHICLE_STATE,
    )
