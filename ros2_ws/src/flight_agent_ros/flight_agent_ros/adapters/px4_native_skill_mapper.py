'''Map approved native flight Skills to deterministic PX4 VehicleCommand sequences.'''

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
class Px4NativeCommand:
    '''One native PX4 command in a deterministic Skill sequence.'''

    command_id: int
    parameters: VehicleCommandParameters


@dataclass(frozen=True)
class Px4NativeSkillPlan:
    '''Ordered native commands required to start one approved flight Skill.'''

    execution_id: str
    skill_name: SkillName
    commands: tuple[Px4NativeCommand, ...]


class Px4NativeSkillMappingError(ValueError):
    '''Stable mapping failure raised before any PX4 command is emitted.'''

    def __init__(self, failure_code: str) -> None:
        '''Store a machine-readable failure code for the Backend result.'''

        super().__init__(failure_code)
        self.failure_code = failure_code


def build_native_skill_plan(
    command: ApprovedSkillCommand, state: WorldState
) -> Px4NativeSkillPlan:
    '''Build the native PX4 command sequence for Takeoff, RTL or Land.'''

    if command.skill_name is SkillName.TAKEOFF:
        commands = _build_takeoff_commands(command, state)
    elif command.skill_name is SkillName.RTL:
        if not state.home_valid or state.home_position_wgs84 is None:
            raise Px4NativeSkillMappingError('HOME_REFERENCE_UNAVAILABLE')
        commands = (
            Px4NativeCommand(
                command_id=VehicleCommand.VEHICLE_CMD_NAV_RETURN_TO_LAUNCH,
                parameters=VehicleCommandParameters(),
            ),
        )
    elif command.skill_name is SkillName.LAND:
        commands = (
            Px4NativeCommand(
                command_id=VehicleCommand.VEHICLE_CMD_NAV_LAND,
                parameters=VehicleCommandParameters(),
            ),
        )
    else:
        raise Px4NativeSkillMappingError('SKILL_REQUIRES_OFFBOARD')

    return Px4NativeSkillPlan(
        execution_id=command.execution_id,
        skill_name=command.skill_name,
        commands=commands,
    )


def _build_takeoff_commands(
    command: ApprovedSkillCommand, state: WorldState
) -> tuple[Px4NativeCommand, ...]:
    '''Build PX4's takeoff-mode then arm sequence using an AMSL target.'''

    arguments = command.arguments
    if not isinstance(arguments, TakeoffArgs):
        raise Px4NativeSkillMappingError('SKILL_ARGUMENT_TYPE_MISMATCH')
    if not state.home_valid or state.home_position_wgs84 is None:
        raise Px4NativeSkillMappingError('HOME_REFERENCE_UNAVAILABLE')

    target_altitude_amsl_m = (
        state.home_position_wgs84.altitude_amsl_m + arguments.target_altitude_m
    )
    return (
        Px4NativeCommand(
            command_id=VehicleCommand.VEHICLE_CMD_NAV_TAKEOFF,
            parameters=VehicleCommandParameters(param7=target_altitude_amsl_m),
        ),
        Px4NativeCommand(
            command_id=VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM,
            parameters=VehicleCommandParameters(
                param1=float(VehicleCommand.ARMING_ACTION_ARM)
            ),
        ),
    )
