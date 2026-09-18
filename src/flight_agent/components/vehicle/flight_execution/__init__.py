'''飞行技能执行接口及其运行时实现。'''

from flight_agent.components.vehicle.flight_execution.interface import FlightExecutionInterface
from flight_agent.components.vehicle.flight_execution.mock_runtime import (
    MockExecutionOutcome,
    MockFlightExecutionRuntime,
)

__all__ = [
    'FlightExecutionInterface',
    'MockExecutionOutcome',
    'MockFlightExecutionRuntime',
]
