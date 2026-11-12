'''离线回归使用独立预算账本，不能修改真实调用的累计消费。'''

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def test_temp_budget_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    '''仅显式 live 参数保留真实账本配置，其余用例各自从零开始。'''
    test_input_model = getattr(request.node, 'callspec', None)
    if test_input_model is not None and test_input_model.params.get('test_input_model') == 'live':
        return
    path = tmp_path / 'budget.sqlite3'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE budget (id INTEGER PRIMARY KEY CHECK(id=1), '
                   'spent_cny TEXT NOT NULL, pending INTEGER NOT NULL CHECK(pending IN (0,1)))')
        db.execute('INSERT INTO budget VALUES (1, ?, 0)', ('0',))
    monkeypatch.setenv('FLIGHT_LLM_BUDGET_DB', str(path))
