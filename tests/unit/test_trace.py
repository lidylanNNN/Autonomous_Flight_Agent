'''Trace recorder and replay tests.'''

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flight_agent.components.tracing import TraceRecorder, TraceReplay, TraceReplayError
from flight_agent.contracts import (
    ApprovedSkillCommand,
    AtomicActionProposal,
    SafetyDecision,
    SafetyDecisionType,
    SafetyReasonCode,
    SkillExecutionStatus,
    SkillResult,
    Vector3,
    WorldState,
)


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


def test_safety_decision_shares_trace_without_breaking_world_state_replay(
    tmp_path: Path,
) -> None:
    proposal = AtomicActionProposal(
        proposal_id='proposal-1', skill_name='takeoff',
        arguments={'target_altitude_m': 10.0}, based_on_state_id='state-1',
    )
    decision = SafetyDecision(
        decision_id='decision-1', proposal_id=proposal.proposal_id,
        decision=SafetyDecisionType.REJECT,
        reason_codes=(SafetyReasonCode.SYSTEM_UNHEALTHY,),
        checked_state_id='state-1', policy_version='m4-v1',
    )
    path = tmp_path / 'trace.jsonl'
    recorder = TraceRecorder(path)
    recorder.record_world_state(make_world_state(1))
    recorder.record_safety_decision(proposal, decision)
    recorder.record_world_state(make_world_state(2))

    replay = TraceReplay(path)
    assert tuple(state.state_id for state in replay.replay_world_states()) == (
        'state-1', 'state-2',
    )
    safety_records = tuple(replay.iter_safety_decision_records())
    assert len(safety_records) == 1
    assert safety_records[0].recorded_at.tzinfo is not None
    assert safety_records[0].proposal == proposal
    assert safety_records[0].decision == decision


def test_safety_trace_rejects_mismatched_proposal_id(tmp_path: Path) -> None:
    proposal = AtomicActionProposal(
        proposal_id='proposal-1', skill_name='takeoff',
        arguments={'target_altitude_m': 10.0}, based_on_state_id='state-1',
    )
    decision = SafetyDecision(
        decision_id='decision-1', proposal_id='proposal-2',
        decision=SafetyDecisionType.REJECT,
        reason_codes=(SafetyReasonCode.SYSTEM_UNHEALTHY,),
        checked_state_id='state-1', policy_version='m4-v1',
    )
    path = tmp_path / 'trace.jsonl'

    with pytest.raises(ValueError, match='does not belong'):
        TraceRecorder(path).record_safety_decision(proposal, decision)
    assert not path.exists()


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


def command_and_result(status: SkillExecutionStatus) -> tuple[ApprovedSkillCommand, SkillResult]:
    command = ApprovedSkillCommand(
        execution_id='execution-1', proposal_id='proposal-1', decision_id='decision-1',
        skill_name='hold', arguments={'duration_s': 1}, timeout_s=5,
        approved_state_id='state-1',
    )
    result = SkillResult(
        execution_id=command.execution_id, proposal_id=command.proposal_id,
        skill_name=command.skill_name, status=status, px4_ack='accepted',
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        start_state_id='state-1', end_state_id='state-2',
        failure_code=None if status == SkillExecutionStatus.SUCCEEDED else 'BACKEND_FAILURE',
    )
    return command, result


@pytest.mark.parametrize('status', list(SkillExecutionStatus))
@pytest.mark.parametrize('success', [True, False])
def test_mixed_trace_preserves_complete_content_and_results(
    tmp_path: Path, status: SkillExecutionStatus, success: bool,
) -> None:
    path = tmp_path / 'mixed.jsonl'
    recorder = TraceRecorder(path)
    command, result = command_and_result(status)
    text = '  占位来源\n```json\n{"action": "hold"}\n```\n  '
    recorder.record_world_state(make_world_state(1))
    recorder.record_observation(
        success=success, observation=text, source=' px4:mx_placeholder ', message='\n诊断 ',
    )
    recorder.record_llm_response(prompt=text, success=success, response=text, info=' MX_FIXED ')
    recorder.record_skill_result(command, result)
    recorder.record_skill_execution_error(command, TimeoutError(' 等待超时\n '))
    recorder.record_world_state(make_world_state(2))

    replay = TraceReplay(path)
    assert len(replay.replay_world_states()) == 2
    observation, = replay.iter_observation_records()
    assert (observation.success, observation.observation, observation.source, observation.message) == (
        success, text, ' px4:mx_placeholder ', '\n诊断 ',
    )
    llm, = replay.iter_llm_response_records()
    assert (llm.prompt, llm.success, llm.response, llm.info) == (text, success, text, ' MX_FIXED ')
    executed, = replay.iter_skill_result_records()
    assert (executed.command, executed.result) == (command, result)
    error, = replay.iter_skill_execution_error_records()
    assert error.command == command
    assert (error.error_type, error.error_message) == ('TimeoutError', ' 等待超时\n ')


@pytest.mark.parametrize('field,value', [
    ('execution_id', 'other-execution'), ('proposal_id', 'other-proposal'), ('skill_name', 'rtl'),
])
def test_result_identity_mismatch_rejected_on_write_and_replay(
    tmp_path: Path, field: str, value: str,
) -> None:
    command, result = command_and_result(SkillExecutionStatus.SUCCEEDED)
    mismatched = SkillResult.model_validate({**result.model_dump(), field: value})
    path = tmp_path / 'identity.jsonl'
    with pytest.raises(ValueError, match=field):
        TraceRecorder(path).record_skill_result(command, mismatched)
    assert not path.exists()
    path.write_text(json.dumps({
        'recorded_at': datetime.now(UTC).isoformat(), 'type': 'skill_result',
        'command': command.model_dump(mode='json'), 'result': mismatched.model_dump(mode='json'),
    }), encoding='utf-8')
    with pytest.raises(TraceReplayError, match=f'(?s):1:.*{field}'):
        tuple(TraceReplay(path).iter_skill_result_records())


@pytest.mark.parametrize('kind,fields', [
    ('observation', {'success': True, 'observation': '', 'source': '', 'message': ''}),
    ('llm_response', {'prompt': '', 'success': False, 'response': '', 'info': ''}),
    ('skill_result', {}), ('skill_execution_error', {}),
])
@pytest.mark.parametrize('corruption', ['missing', 'extra', 'wrong_type'])
def test_new_trace_records_reject_invalid_fields(
    tmp_path: Path, kind: str, fields: dict, corruption: str,
) -> None:
    command, result = command_and_result(SkillExecutionStatus.SUCCEEDED)
    if kind.startswith('skill_'):
        fields = {'command': command.model_dump(mode='json')}
        fields.update({'result': result.model_dump(mode='json')} if kind == 'skill_result' else {
            'error_type': 'RuntimeError', 'error_message': '',
        })
    payload = {'recorded_at': datetime.now(UTC).isoformat(), 'type': kind, **fields}
    if corruption == 'missing':
        del payload[next(iter(fields))]
    elif corruption == 'extra':
        payload['unexpected'] = 'extra'
    else:
        payload[next(iter(fields))] = []
    path = tmp_path / 'invalid.jsonl'
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(TraceReplayError, match=f':1: invalid {kind} record'):
        TraceReplay(path).replay_world_states()


def test_record_write_failure_propagates(tmp_path: Path) -> None:
    # A directory cannot be opened as an appendable file.
    recorder = TraceRecorder(tmp_path)
    with pytest.raises(OSError):
        recorder.record_llm_response(prompt='', success=False, response='', info='unavailable')
