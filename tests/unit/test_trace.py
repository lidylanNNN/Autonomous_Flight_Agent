'''Trace recorder tests.'''

import json
from datetime import UTC, datetime

from flight_agent.contracts import CoordinateFrame, Vector3, WorldState
from flight_agent.runtime import TraceRecorder


def test_trace_recorder_writes_replayable_json_line(tmp_path) -> None:
    '''验证世界状态可以被写入并恢复为 JSON 对象。'''

    state = WorldState(
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_timestamp_us=42,
        frame=CoordinateFrame.ENU,
        position_m=Vector3(x=1, y=2, z=3),
        velocity_mps=Vector3(x=0, y=0, z=0),
        armed=False,
        landed=True,
        connected=True,
    )
    path = tmp_path / 'trace.jsonl'

    TraceRecorder(path).record_world_state(state)

    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['type'] == 'world_state'
    assert record['state']['source_timestamp_us'] == 42
