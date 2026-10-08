'''M4 Proposal state consistency check unit tests.'''

from datetime import UTC, datetime, timedelta

from flight_agent.components.safety import check_proposal_state
from flight_agent.contracts import (
    MissionConstraints,
    MissionContract,
    NedGeofence,
    SafetyReasonCode,
    SkillName,
    SkillProposal,
    WorldState,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def make_contract() -> MissionContract:
    '''构造状态检查使用的任务契约。'''

    return MissionContract(
        mission_id='mission-1',
        contract_version=1,
        objective='执行一次安全飞行',
        constraints=MissionConstraints(
            max_altitude_m=50.0,
            max_horizontal_speed_mps=8.0,
            max_mission_radius_m=100.0,
            min_battery_percent=25.0,
            max_state_age_ms=1_000,
            geofence=NedGeofence(
                north_min_m=-100.0,
                north_max_m=100.0,
                east_min_m=-100.0,
                east_max_m=100.0,
            ),
            allowed_skills=tuple(SkillName),
        ),
        completion_criteria=('动作完成',),
        abort_criteria=('安全检查失败',),
    )


def make_proposal(state_id: str = 'state-1') -> SkillProposal:
    '''构造引用指定WorldState的Proposal。'''

    return SkillProposal(
        proposal_id='proposal-1',
        mission_id='mission-1',
        plan_revision=0,
        step_id='step-1',
        skill_name='takeoff',
        arguments={'target_altitude_m': 10.0},
        based_on_state_id=state_id,
    )


def make_state(*, state_id: str = 'state-1', age_ms: int = 1_000) -> WorldState:
    '''构造具有确定年龄的WorldState。'''

    return WorldState(
        state_id=state_id,
        source_timestamp_us=1,
        received_at=NOW - timedelta(milliseconds=age_ms),
        state_age_ms=age_ms,
        landed=True,
        position_valid=True,
        home_valid=True,
        link_healthy=True,
    )


def test_state_at_freshness_boundary_is_allowed() -> None:
    '''验证状态年龄等于上限时仍属于新鲜状态。'''

    assert check_proposal_state(
        make_proposal(), make_contract(), make_state(), now=NOW
    ) == ()


def test_stale_state_is_rejected() -> None:
    '''验证超过Contract上限一毫秒的状态被拒绝。'''

    assert check_proposal_state(
        make_proposal(), make_contract(), make_state(age_ms=1_001), now=NOW
    ) == (SafetyReasonCode.STALE_STATE,)


def test_state_id_mismatch_is_rejected() -> None:
    '''验证Proposal不能使用不同版本的WorldState。'''

    assert check_proposal_state(
        make_proposal('state-old'), make_contract(), make_state(), now=NOW
    ) == (SafetyReasonCode.STATE_ID_MISMATCH,)


def test_state_failures_follow_stable_reason_order() -> None:
    '''验证状态错误按Freshness再State ID的固定顺序返回。'''

    assert check_proposal_state(
        make_proposal('state-old'),
        make_contract(),
        make_state(age_ms=1_001),
        now=NOW,
    ) == (
        SafetyReasonCode.STALE_STATE,
        SafetyReasonCode.STATE_ID_MISMATCH,
    )
