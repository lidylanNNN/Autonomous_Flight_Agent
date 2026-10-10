'''Deterministic battery checks for mission proposals.'''

from math import isfinite

from flight_agent.contracts import MissionConstraints, SafetyReasonCode, WorldState


def check_battery(
    state: WorldState,
    constraints: MissionConstraints,
) -> tuple[SafetyReasonCode, ...]:
    '''要求可用电池读数及不低于任务冻结下限。'''

    battery_percent = state.battery_percent
    if (
        battery_percent is None
        or not isfinite(battery_percent)
        or state.health_flags.get('battery_connected') is not True
        or state.health_flags.get('battery_valid') is not True
        or state.health_flags.get('battery_fault_free') is not True
    ):
        return (SafetyReasonCode.SYSTEM_UNHEALTHY,)
    if battery_percent < constraints.min_battery_percent:
        return (SafetyReasonCode.INSUFFICIENT_BATTERY,)
    return ()
