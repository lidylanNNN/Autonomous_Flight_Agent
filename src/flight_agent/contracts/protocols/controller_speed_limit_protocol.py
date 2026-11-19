'''Controller speed-limit evidence required by Safety.'''

from datetime import datetime
from typing import Protocol, runtime_checkable


class ControllerSpeedLimitReading(Protocol):
    '''复用适配器已校验读数的数值及实际接收时间。'''

    @property
    def value(self) -> float: ...

    @property
    def received_at(self) -> datetime: ...


@runtime_checkable
class ControllerSpeedLimitProtocol(Protocol):
    '''读取并校验当前控制器的水平速度上限。'''

    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        '''返回来自目标飞控且仍在有效期内的正数限速。'''

        ...

    async def read_validated_speed_limit_reading(
        self, *, max_age_s: float,
    ) -> ControllerSpeedLimitReading:
        '''返回已核对来源、数值和接收时间的读数，供等待后再次检查。'''
        ...
