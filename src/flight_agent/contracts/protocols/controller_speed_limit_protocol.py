'''Controller speed-limit evidence required by Safety.'''

from typing import Protocol, runtime_checkable


@runtime_checkable
class ControllerSpeedLimitProtocol(Protocol):
    '''读取并校验当前控制器的水平速度上限。'''

    async def read_validated_speed_limit(self, *, max_age_s: float) -> float:
        '''返回来自目标飞控且仍在有效期内的正数限速。'''

        ...
