'''确定性 JSON Lines Trace 回放器。'''

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from flight_agent.contracts import WorldState


class WorldStateTraceRecord(BaseModel):
    '''Trace 文件中的一个强类型 WorldState 记录。'''

    model_config = ConfigDict(extra='forbid', frozen=True)

    recorded_at: AwareDatetime
    type: Literal['world_state']
    state: WorldState


class TraceReplayError(ValueError):
    '''表示 Trace 内容无法可靠回放。'''


class TraceReplay:
    '''读取并定位 Trace 中的 WorldState 快照。'''

    def __init__(self, path: Path) -> None:
        '''绑定待回放的 JSON Lines 文件。'''

        self._path = path

    def iter_world_state_records(self) -> Iterator[WorldStateTraceRecord]:
        '''按记录顺序读取并校验 WorldState Trace。'''

        previous_recorded_at: datetime | None = None
        with self._path.open(encoding='utf-8') as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                record = self._parse_record(line, line_number)
                if (
                    previous_recorded_at is not None
                    and record.recorded_at < previous_recorded_at
                ):
                    raise self._error(line_number, 'recorded_at is earlier than the prior record')
                previous_recorded_at = record.recorded_at
                yield record

    def replay_world_states(self) -> tuple[WorldState, ...]:
        '''恢复文件中的全部 WorldState，保留原始记录顺序。'''

        return tuple(record.state for record in self.iter_world_state_records())

    def world_state_by_record(self, record_number: int) -> WorldState:
        '''按从 1 开始的记录序号返回 WorldState。'''

        if record_number < 1:
            raise ValueError('record_number must be at least 1')
        for current_number, record in enumerate(self.iter_world_state_records(), start=1):
            if current_number == record_number:
                return record.state
        raise IndexError(f'world state record {record_number} does not exist')

    def find_world_state(self, state_id: str) -> WorldState | None:
        '''按 state_id 查找第一个匹配的 WorldState。'''

        for record in self.iter_world_state_records():
            if record.state.state_id == state_id:
                return record.state
        return None

    def world_state_at_or_before(self, recorded_at: datetime) -> WorldState | None:
        '''返回指定记录时间点之前最近的 WorldState。'''

        if recorded_at.tzinfo is None:
            raise ValueError('recorded_at must include a timezone')
        latest: WorldState | None = None
        for record in self.iter_world_state_records():
            if record.recorded_at > recorded_at:
                break
            latest = record.state
        return latest

    def latest_world_state(self) -> WorldState | None:
        '''返回 Trace 中最后一个 WorldState；空文件返回 None。'''

        latest: WorldState | None = None
        for record in self.iter_world_state_records():
            latest = record.state
        return latest

    def _parse_record(self, line: str, line_number: int) -> WorldStateTraceRecord:
        '''解析单行 JSON，并将格式错误转换为可定位异常。'''

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise self._error(line_number, f'invalid JSON: {error.msg}') from error
        try:
            return WorldStateTraceRecord.model_validate(payload)
        except ValidationError as error:
            raise self._error(line_number, f'invalid world state record: {error}') from error

    def _error(self, line_number: int, message: str) -> TraceReplayError:
        '''构造包含文件和行号的回放错误。'''

        return TraceReplayError(f'{self._path}:{line_number}: {message}')
