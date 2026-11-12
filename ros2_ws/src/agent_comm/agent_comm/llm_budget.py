'''单工作区 Flash 消费账本；未知费用保留未结算标志，禁止自动重试。'''

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_LIMIT_CNY = Decimal(1)
_RESERVE_CNY = Decimal('0.05')
_MAX_PROMPT_BYTES = 20_000


class BudgetError(Exception):
    '''仅返回稳定预算错误码，不携带账本或网络异常细节。'''


@contextmanager
def reserve_budget(prompt: str, info: dict[str, Any]) -> Iterator[None]:
    '''在 HTTP 前持久化预留，在退出时按有效 usage 结算，包括内容失败响应。'''
    if len(prompt.encode('utf-8')) > _MAX_PROMPT_BYTES:
        raise BudgetError('LLM_PROMPT_TOO_LARGE')
    path = Path(os.environ.get('FLIGHT_LLM_BUDGET_DB', ''))
    if not path.is_absolute() or not path.is_file():
        raise BudgetError('LLM_BUDGET_STORAGE_ERROR')
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1)) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT spent_cny, pending FROM budget WHERE id=1').fetchone()
                if row is None:
                    raise BudgetError('LLM_BUDGET_STORAGE_ERROR')
                try:
                    spent = Decimal(row[0])
                except (InvalidOperation, TypeError, ValueError):
                    raise BudgetError('LLM_BUDGET_STORAGE_ERROR') from None
                if not spent.is_finite() or spent < 0 or row[1] not in (0, 1):
                    raise BudgetError('LLM_BUDGET_STORAGE_ERROR')
                info.update(budget_spent_cny=str(spent), budget_pending=bool(row[1]))
                if row[1]:
                    raise BudgetError('LLM_BUDGET_UNSETTLED')
                if spent + _RESERVE_CNY > _LIMIT_CNY:
                    raise BudgetError('LLM_BUDGET_EXCEEDED')
                db.execute('UPDATE budget SET pending=1 WHERE id=1')
            info['budget_pending'] = True
            try:
                yield
            finally:
                cost = usage_cost(info.get('usage'))
                if cost is not None:
                    spent += cost
                    blocked = cost > _RESERVE_CNY or spent > _LIMIT_CNY
                    with db:
                        db.execute(
                            'UPDATE budget SET spent_cny=?, pending=? WHERE id=1',
                            (str(spent), int(blocked)),
                        )
                    info.update(
                        cost_cny=str(cost), budget_spent_cny=str(spent), budget_pending=blocked,
                    )
                    if blocked:
                        raise BudgetError('LLM_BUDGET_ESTIMATE_EXCEEDED')
    except sqlite3.Error:
        raise BudgetError('LLM_BUDGET_STORAGE_ERROR') from None


def usage_cost(usage: Any) -> Decimal | None:
    '''校验完整 token 计数，按 2026-11-12 Flash 高峰人民币费率结算。'''
    counts = ('prompt_tokens', 'completion_tokens', 'total_tokens',
              'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
    if not isinstance(usage, dict) or any(
        type(usage.get(name)) is not int or usage[name] < 0 for name in counts
    ) or usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens'] or (
        usage['prompt_cache_hit_tokens'] + usage['prompt_cache_miss_tokens']
        != usage['prompt_tokens']
    ):
        return None
    return (
        Decimal(usage['prompt_cache_hit_tokens']) * Decimal('0.04')
        + Decimal(usage['prompt_cache_miss_tokens']) * 2 + Decimal(usage['completion_tokens']) * 8
    ) / 1_000_000
