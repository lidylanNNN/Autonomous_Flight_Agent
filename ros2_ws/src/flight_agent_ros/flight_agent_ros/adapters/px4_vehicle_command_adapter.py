'''Correlate PX4 VehicleCommand requests with their protocol-level acknowledgements.'''

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from px4_msgs.msg import VehicleCommand, VehicleCommandAck
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy


class Px4CommandAckStatus(StrEnum):
    '''PX4 transport-layer terminal status for one submitted VehicleCommand.'''

    ACCEPTED = 'ACCEPTED'
    REJECTED = 'REJECTED'
    TIMED_OUT = 'TIMED_OUT'
    CANCELLED = 'CANCELLED'


@dataclass(frozen=True)
class VehicleCommandParameters:
    '''PX4 VehicleCommand parameters, kept at the ROS adapter boundary.'''

    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: float = 0.0
    param5: float = 0.0
    param6: float = 0.0
    param7: float = 0.0


@dataclass(frozen=True)
class Px4CommandAck:
    '''Terminal response to a VehicleCommand, not a completed flight Skill.'''

    execution_id: str
    command_id: int
    status: Px4CommandAckStatus
    ack_name: str | None
    failure_code: str | None
    result_param1: int | None = None
    result_param2: int | None = None


@dataclass
class _PendingCommand:
    '''Internal waiter for the one native PX4 command currently in flight.'''

    execution_id: str
    command_id: int
    loop: asyncio.AbstractEventLoop
    resolved: asyncio.Event
    outcome: Px4CommandAck | None = None


_ACK_NAMES = {
    VehicleCommandAck.VEHICLE_CMD_RESULT_ACCEPTED: 'ACCEPTED',
    VehicleCommandAck.VEHICLE_CMD_RESULT_TEMPORARILY_REJECTED: 'TEMPORARILY_REJECTED',
    VehicleCommandAck.VEHICLE_CMD_RESULT_DENIED: 'DENIED',
    VehicleCommandAck.VEHICLE_CMD_RESULT_UNSUPPORTED: 'UNSUPPORTED',
    VehicleCommandAck.VEHICLE_CMD_RESULT_FAILED: 'FAILED',
    VehicleCommandAck.VEHICLE_CMD_RESULT_IN_PROGRESS: 'IN_PROGRESS',
    VehicleCommandAck.VEHICLE_CMD_RESULT_CANCELLED: 'CANCELLED',
}

_REJECTED_ACK_RESULTS = {
    VehicleCommandAck.VEHICLE_CMD_RESULT_TEMPORARILY_REJECTED,
    VehicleCommandAck.VEHICLE_CMD_RESULT_DENIED,
    VehicleCommandAck.VEHICLE_CMD_RESULT_UNSUPPORTED,
    VehicleCommandAck.VEHICLE_CMD_RESULT_FAILED,
    VehicleCommandAck.VEHICLE_CMD_RESULT_CANCELLED,
}


class Px4VehicleCommandAdapter(Node):
    '''PX4 VehicleCommand transport with ACK, timeout and local cancellation lifecycle.'''

    def __init__(
        self,
        *,
        target_system: int = 1,
        target_component: int = 1,
        source_system: int = 1,
        source_component: int = 1,
    ) -> None:
        '''Create the fixed-topic PX4 command publisher and acknowledgement subscription.'''

        super().__init__('px4_vehicle_command_adapter')
        self._target_system = target_system
        self._target_component = target_component
        self._source_system = source_system
        self._source_component = source_component
        self._pending: _PendingCommand | None = None
        self._submission_lock = asyncio.Lock()
        px4_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._command_publisher = self.create_publisher(
            VehicleCommand, '/fmu/in/vehicle_command', px4_qos
        )
        self.create_subscription(
            VehicleCommandAck,
            '/fmu/out/vehicle_command_ack',
            self._handle_command_ack,
            px4_qos,
        )

    async def submit_and_wait(
        self,
        *,
        execution_id: str,
        command_id: int,
        timeout_s: float,
        parameters: VehicleCommandParameters | None = None,
    ) -> Px4CommandAck:
        '''Publish one native command and wait for its terminal PX4 ACK result.

        PX4's VehicleCommandAck has no caller-generated request ID. The adapter therefore
        serializes submissions, so a terminal ACK for ``command_id`` maps to exactly one
        ``execution_id``. This ACK remains transport evidence, not Skill completion evidence.
        '''

        if not execution_id:
            raise ValueError('execution_id must not be empty')
        if command_id < 0:
            raise ValueError('command_id must be non-negative')
        if timeout_s <= 0.0:
            raise ValueError('timeout_s must be positive')

        async with self._submission_lock:
            pending = _PendingCommand(
                execution_id=execution_id,
                command_id=command_id,
                loop=asyncio.get_running_loop(),
                resolved=asyncio.Event(),
            )
            self._pending = pending
            self._command_publisher.publish(
                self._make_command(command_id, parameters or VehicleCommandParameters())
            )
            try:
                await asyncio.wait_for(pending.resolved.wait(), timeout=timeout_s)
            except TimeoutError:
                return Px4CommandAck(
                    execution_id=execution_id,
                    command_id=command_id,
                    status=Px4CommandAckStatus.TIMED_OUT,
                    ack_name=None,
                    failure_code='PX4_ACK_TIMEOUT',
                )
            finally:
                if self._pending is pending:
                    self._pending = None

            assert pending.outcome is not None
            return pending.outcome

    async def cancel(self, execution_id: str) -> bool:
        '''End the local wait for a matching command; repeated or unknown cancels are inert.

        This method does not claim to stop an already accepted PX4 flight action. The Skill
        Backend must issue its explicit hold or safety-mode command in M3-4/M3-5.
        '''

        pending = self._pending
        if (
            pending is None
            or pending.execution_id != execution_id
            or pending.outcome is not None
        ):
            return False
        pending.outcome = Px4CommandAck(
            execution_id=execution_id,
            command_id=pending.command_id,
            status=Px4CommandAckStatus.CANCELLED,
            ack_name=None,
            failure_code='CANCELLED_BY_REQUEST',
        )
        pending.loop.call_soon_threadsafe(pending.resolved.set)
        return True

    def _handle_command_ack(self, message: VehicleCommandAck) -> None:
        '''Resolve the active request only when PX4 emits its terminal ACK for that command.'''

        pending = self._pending
        if pending is None or int(message.command) != pending.command_id:
            return
        result = int(message.result)
        if result == VehicleCommandAck.VEHICLE_CMD_RESULT_IN_PROGRESS:
            return
        ack_name = _ACK_NAMES.get(result, f'UNKNOWN_{result}')
        if result == VehicleCommandAck.VEHICLE_CMD_RESULT_ACCEPTED:
            status = Px4CommandAckStatus.ACCEPTED
            failure_code = None
        else:
            status = Px4CommandAckStatus.REJECTED
            failure_code = f'PX4_ACK_{ack_name}'
            if result not in _REJECTED_ACK_RESULTS:
                failure_code = f'PX4_ACK_UNKNOWN_{result}'
        pending.outcome = Px4CommandAck(
            execution_id=pending.execution_id,
            command_id=pending.command_id,
            status=status,
            ack_name=ack_name,
            failure_code=failure_code,
            result_param1=int(message.result_param1),
            result_param2=int(message.result_param2),
        )
        pending.loop.call_soon_threadsafe(pending.resolved.set)

    def _make_command(
        self, command_id: int, parameters: VehicleCommandParameters
    ) -> VehicleCommand:
        '''Build the only raw PX4 command shape emitted by the M3 adapter layer.'''

        message = VehicleCommand()
        message.timestamp = self.get_clock().now().nanoseconds // 1_000
        message.param1 = parameters.param1
        message.param2 = parameters.param2
        message.param3 = parameters.param3
        message.param4 = parameters.param4
        message.param5 = parameters.param5
        message.param6 = parameters.param6
        message.param7 = parameters.param7
        message.command = command_id
        message.target_system = self._target_system
        message.target_component = self._target_component
        message.source_system = self._source_system
        message.source_component = self._source_component
        message.from_external = True
        return message
