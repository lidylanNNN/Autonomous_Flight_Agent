'''确定性 JSON Lines Trace 记录器。'''

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flight_agent.components.tracing.replay import (
    LLMResponseTraceRecord,
    ObservationTraceRecord,
    SkillExecutionErrorTraceRecord,
    SkillResultTraceRecord,
)
from flight_agent.contracts import (
    ApprovedSkillCommand,
    AtomicActionProposal,
    SafetyDecision,
    SkillResult,
    WorldState,
)


class TraceRecorder:
    '''以 JSON Lines 记录可回放的状态快照。'''

    def __init__(self, path: Path) -> None:
        '''初始化记录器并创建父目录。'''

        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_world_state(self, state: WorldState) -> None:
        '''追加一条世界状态记录。'''

        self._append({'type': 'world_state', 'state': state.model_dump(mode='json')})

    def record_safety_decision(
        self, proposal: AtomicActionProposal, decision: SafetyDecision
    ) -> None:
        '''Record the proposal and its complete deterministic safety decision.'''

        if proposal.proposal_id != decision.proposal_id:
            raise ValueError('safety decision does not belong to proposal')
        self._append({
            'type': 'safety_decision',
            'proposal': proposal.model_dump(mode='json'),
            'decision': decision.model_dump(mode='json'),
        })

    def record_observation(
        self, *, success: bool, observation: str, source: str, message: str,
    ) -> None:
        '''校验并保存观察服务完整响应；写入错误直接向调用方传播。'''
        record = ObservationTraceRecord(
            recorded_at=datetime.now(UTC), success=success, observation=observation,
            source=source, message=message,
        )
        self._append(record.model_dump(mode='json'))

    def record_llm_response(
        self, *, prompt: str, success: bool, response: str, info: str,
    ) -> None:
        '''保存实际 prompt 和完整回复，包括失败响应。'''
        record = LLMResponseTraceRecord(
            recorded_at=datetime.now(UTC), prompt=prompt, success=success,
            response=response, info=info,
        )
        self._append(record.model_dump(mode='json'))

    def record_skill_result(self, command: ApprovedSkillCommand, result: SkillResult) -> None:
        '''身份校验通过后保存动作结果，不执行或重试动作。'''
        record = SkillResultTraceRecord(recorded_at=datetime.now(UTC), command=command, result=result)
        self._append(record.model_dump(mode='json'))

    def record_skill_execution_error(
        self, command: ApprovedSkillCommand, error: Exception,
    ) -> None:
        '''保存异常类型和原始文字；由调用方决定如何传播原执行异常。'''
        record = SkillExecutionErrorTraceRecord(
            recorded_at=datetime.now(UTC), command=command,
            error_type=type(error).__name__, error_message=str(error),
        )
        self._append(record.model_dump(mode='json'))

    def _append(self, event: dict[str, Any]) -> None:
        '''写入带记录时间的 JSON Lines 事件。'''

        payload = {
            'recorded_at': datetime.now(UTC).isoformat(),
            **event,
        }
        with self._path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, sort_keys=True) + '\n')
