'''Deterministic JSON-lines trace recorder for M2.'''

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flight_agent.contracts import WorldState


class TraceRecorder:
    '''以 JSON Lines 记录可回放的状态快照。'''

    def __init__(self, path: Path) -> None:
        '''初始化记录器并创建父目录。'''

        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_world_state(self, state: WorldState) -> None:
        '''追加一条世界状态记录。'''

        self._append({'type': 'world_state', 'state': state.model_dump(mode='json')})

    def _append(self, event: dict[str, Any]) -> None:
        '''写入带记录时间的 JSON Lines 事件。'''

        payload = {
            'recorded_at': datetime.now(UTC).isoformat(),
            **event,
        }
        with self._path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, sort_keys=True) + '\n')
