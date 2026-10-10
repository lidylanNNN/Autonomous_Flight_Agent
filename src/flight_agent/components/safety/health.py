'''Deterministic PX4 and state-link health checks.'''

from flight_agent.contracts import SafetyReasonCode, SkillName, WorldState


def check_system_health(
    skill_name: SkillName,
    state: WorldState,
) -> tuple[SafetyReasonCode, ...]:
    '''拒绝链路、失效保护或故障检测不健康的普通任务动作。'''

    if (
        not state.link_healthy
        or state.failsafe_active
        or state.health_flags.get('failure_detector_clear') is not True
        or (
            skill_name is SkillName.TAKEOFF
            and state.health_flags.get('preflight_checks_pass') is not True
        )
    ):
        return (SafetyReasonCode.SYSTEM_UNHEALTHY,)
    return ()
