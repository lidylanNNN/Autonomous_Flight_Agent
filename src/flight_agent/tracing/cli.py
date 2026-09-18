'''WorldState Trace 回放命令行入口。'''

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from flight_agent.contracts import WorldState
from flight_agent.tracing import TraceReplay, TraceReplayError


def main() -> None:
    '''读取查询参数并输出匹配的 WorldState JSON。'''

    parser = _build_parser()
    arguments = parser.parse_args()
    replay = TraceReplay(arguments.trace_path)
    try:
        state = _select_world_state(replay, arguments)
    except (IndexError, OSError, TraceReplayError, ValueError) as error:
        parser.error(str(error))
    if state is None:
        _fail(parser, 'no matching world state found')
    print(state.model_dump_json(indent=2))


def _build_parser() -> argparse.ArgumentParser:
    '''创建互斥查询参数。'''

    parser = argparse.ArgumentParser(
        description='Replay and locate WorldState snapshots from a JSONL trace.',
    )
    parser.add_argument('trace_path', type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--record', type=int, help='1-based WorldState record number')
    selection.add_argument('--state-id', help='exact WorldState state_id')
    selection.add_argument('--at', type=_aware_datetime, help='latest record at/before ISO-8601 time')
    return parser


def _select_world_state(
    replay: TraceReplay, arguments: argparse.Namespace
) -> WorldState | None:
    '''根据命令行参数选择一个状态快照。'''

    if arguments.record is not None:
        return replay.world_state_by_record(arguments.record)
    if arguments.state_id is not None:
        return replay.find_world_state(arguments.state_id)
    if arguments.at is not None:
        return replay.world_state_at_or_before(arguments.at)
    return replay.latest_world_state()


def _aware_datetime(value: str) -> datetime:
    '''解析带时区的 ISO-8601 时间。'''

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError('time must include a timezone')
    return parsed


def _fail(parser: argparse.ArgumentParser, message: str) -> NoReturn:
    '''通过 argparse 输出一致的失败信息。'''

    parser.error(message)


if __name__ == '__main__':
    main()
