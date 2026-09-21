'''Map approved Agent Flight Skills to deterministic PX4 command plans.'''

from __future__ import annotations

from dataclasses import dataclass

from px4_msgs.msg import VehicleCommand

from flight_agent.contracts import (
    ApprovedSkillCommand,
    SkillName,
    TakeoffArgs,
    WorldState,
)
from flight_agent_ros.adapters.px4_vehicle_command_adapter import (
    VehicleCommandParameters,
)


@dataclass(frozen=True)
class Px4PlannedCommand:
    '''One PX4 command in the execution plan for an Agent Flight Skill.'''

    command_id: int
    parameters: VehicleCommandParameters


@dataclass(frozen=True)
class Px4CommandPlan:
    '''Ordered PX4 commands required to start one approved Agent Flight Skill.'''

    execution_id: str
    skill_name: SkillName
    commands: tuple[Px4PlannedCommand, ...]


class Px4CommandPlanMappingError(ValueError):
    '''Stable mapping failure raised before any PX4 command is emitted.'''

    def __init__(self, failure_code: str) -> None:
        '''Store a machine-readable failure code for the Backend result.'''

        super().__init__(failure_code)
        self.failure_code = failure_code


def build_px4_command_plan(
    command: ApprovedSkillCommand, state: WorldState
) -> Px4CommandPlan:
    '''Build the PX4 command plan for Takeoff, RTL or Land.'''

    if command.skill_name is SkillName.TAKEOFF:
        commands = _build_takeoff_commands(command, state)
    elif command.skill_name is SkillName.RTL:
        if not state.home_valid or state.home_position_wgs84 is None:
            raise Px4CommandPlanMappingError('HOME_REFERENCE_UNAVAILABLE')
        commands = (
            Px4PlannedCommand(
                command_id=VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH,
                parameters=VehicleCommandParameters(),
            ),
        )
    elif command.skill_name is SkillName.LAND:
        commands = (
            Px4PlannedCommand(
                command_id=VehicleCommand.VEHICLE_CMD_NAV_LAND,
                parameters=VehicleCommandParameters(),
            ),
        )
    else:
        raise Px4CommandPlanMappingError('SKILL_REQUIRES_OFFBOARD')

    return Px4CommandPlan(
        execution_id=command.execution_id,
        skill_name=command.skill_name,
        commands=commands,
    )


def _build_takeoff_commands(
    command: ApprovedSkillCommand, state: WorldState
) -> tuple[Px4PlannedCommand, ...]:
    '''Build PX4's takeoff-mode then arm sequence using an AMSL target.'''

    arguments = command.arguments
    if not isinstance(arguments, TakeoffArgs):
        raise Px4CommandPlanMappingError('SKILL_ARGUMENT_TYPE_MISMATCH')
    if not state.home_valid or state.home_position_wgs84 is None:
        raise Px4CommandPlanMappingError('HOME_REFERENCE_UNAVAILABLE')

    target_altitude_amsl_m = (
        state.home_position_wgs84.altitude_amsl_m + arguments.target_altitude_m
    )
    return (
        Px4PlannedCommand(
            command_id=VehicleCommand.VEHICLE_CMD_NAV_TAKEOFF,
            parameters=VehicleCommandParameters(param7=target_altitude_amsl_m),
        ),
        Px4PlannedCommand(
            command_id=VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,
            parameters=VehicleCommandParameters(
                param1=float(VehicleCommand.ARMING_ACTION_ARM)
            ),
        ),
    )
