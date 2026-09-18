'''Trace recorder and replay tests.'''

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flight_agent.components.tracing import TraceRecorder, TraceReplay, TraceReplayError
from flight_agent.contracts import Vector3, WorldState


def make_world_state(sequence: int) -> WorldState:
    '''创建具有稳定标识和时间戳的测试状态。'''

    return WorldState(
        state_id=f'state-{sequence}',
        source_timestamp_us=sequence,
        received_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sequence),
        state_age_ms=0,
        position_ned_m=Vector3(x=sequence, y=2, z=3),
        velocity_ned_mps=Vector3(x=0, y=0, z=0),
        armed=False,
        landed=True,
        position_valid=True,
        link_healthy=True,
    )


def write_trace(path: Path, records: list[tuple[datetime, WorldState]]) -> None:
    '''写入可控制记录时间的测试 Trace。'''

    lines = [
        json.dumps(
            {
                'recorded_at': recorded_at.isoformat(),
                'type': 'world_state',
                'state': state.model_dump(mode='json'),
            },
            sort_keys=True,
        )
        for recorded_at, state in records
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_trace_recorder_writes_replayable_world_state(tmp_path: Path) -> None:
    '''验证记录器输出可恢复为完全相同的 WorldState。'''

    state = make_world_state(42)
    path = tmp_path / 'trace.jsonl'

    TraceRecorder(path).record_world_state(state)

    records = tuple(TraceReplay(path).iter_world_state_records())
    assert len(records) == 1
    assert records[0].type == 'world_state'
    assert records[0].recorded_at.tzinfo is not None
    assert records[0].state == state


def test_trace_replay_locates_states_by_record_id_and_latest(tmp_path: Path) -> None:
    '''验证可按序号、state_id 查询，并取得最后一个状态。'''

    first = make_world_state(1)
    second = make_world_state(2)
    path = tmp_path / 'trace.jsonl'
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    write_trace(path, [(base_time, first), (base_time + timedelta(seconds=1), second)])
    replay = TraceReplay(path)

    assert replay.replay_world_states() == (first, second)
    assert replay.world_state_by_record(2) == second
    assert replay.find_world_state('state-1') == first
    assert replay.find_world_state('missing') is None
    assert replay.latest_world_state() == second


def test_trace_replay_locates_state_at_or_before_time(tmp_path: Path) -> None:
    '''验证时间查询返回目标时刻之前最近的状态。'''

    first = make_world_state(1)
    second = make_world_state(2)
    path = tmp_path / 'trace.jsonl'
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    write_trace(path, [(base_time, first), (base_time + timedelta(seconds=2), second)])
    replay = TraceReplay(path)

    assert replay.world_state_at_or_before(base_time - timedelta(seconds=1)) is None
    assert replay.world_state_at_or_before(base_time + timedelta(seconds=1)) == first
    assert replay.world_state_at_or_before(base_time + timedelta(seconds=2)) == second


def test_trace_replay_handles_empty_file_and_invalid_record_number(tmp_path: Path) -> None:
    '''验证空 Trace 与越界序号具有明确结果。'''

    path = tmp_path / 'trace.jsonl'
    path.touch()
    replay = TraceReplay(path)

    assert replay.replay_world_states() == ()
    assert replay.latest_world_state() is None
    with pytest.raises(ValueError, match='at least 1'):
        replay.world_state_by_record(0)
    with pytest.raises(IndexError, match='does not exist'):
        replay.world_state_by_record(1)


@pytest.mark.parametrize(
    ('content', 'expected_message'),
    [
        ('{broken json\n', 'invalid JSON'),
        (
            json.dumps(
                {
                    'recorded_at': '2026-01-01T00:00:00+00:00',
                    'type': 'unknown',
                    'state': make_world_state(1).model_dump(mode='json'),
                }
            ),
            'invalid world state record',
        ),
    ],
)
def test_trace_replay_reports_line_for_invalid_records(
    tmp_path: Path, content: str, expected_message: str
) -> None:
    '''验证格式错误包含文件、行号和原因。'''

    path = tmp_path / 'trace.jsonl'
    path.write_text(content, encoding='utf-8')

    with pytest.raises(TraceReplayError) as captured:
        TraceReplay(path).replay_world_states()

    assert f'{path}:1:' in str(captured.value)
    assert expected_message in str(captured.value)


def test_trace_replay_rejects_recorded_time_going_backwards(tmp_path: Path) -> None:
    '''验证记录时间倒序时停止回放并定位异常行。'''

    path = tmp_path / 'trace.jsonl'
    later = datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)
    earlier = later - timedelta(seconds=1)
    write_trace(path, [(later, make_world_state(1)), (earlier, make_world_state(2))])

    with pytest.raises(TraceReplayError, match=r':2: recorded_at is earlier'):
        TraceReplay(path).replay_world_states()
