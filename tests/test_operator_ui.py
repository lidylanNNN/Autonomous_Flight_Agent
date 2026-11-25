'''验证 UI 薄桥的输入保真与忙碌保护；入口子进程为明确的测试替身。'''
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/operator_ui'))
import server
from bridge import AgentBridge


def test_temp_wait(test_input_check: Callable[[], bool]) -> None:
    '''有界等待真实管道事件，不依赖固定延时判定成功。'''
    test_temp_deadline = time.monotonic() + 5
    while not test_input_check():
        assert time.monotonic() < test_temp_deadline
        time.sleep(.01)


test_temp_wait.__test__ = False


def test_temp_input_preserved_and_busy_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''发现多行改写、重复派发及把普通 stdout 误当就绪信号的错误。'''
    test_temp_root = tmp_path / 'root'
    test_temp_run = test_temp_root / 'artifacts/live'
    test_temp_run.mkdir(parents=True)
    test_temp_child = tmp_path / 'test_temp_child.py'
    test_temp_child.write_text(
        "import os,sys,json\nfrom pathlib import Path\n"
        "os.write(int(os.environ['AFA_UI_READY_FD']),b'R')\n"
        "line=sys.stdin.readline()\nPath(sys.argv[1]).write_text(line)\n"
        "print('任务> AFA_INPUT_READY',flush=True)\n"
        "sys.stdin.read()\n")
    test_temp_received = tmp_path / 'test_temp_received.json'
    test_temp_popen = subprocess.Popen

    def test_temp_substitute_entry(test_input_command, **kwargs):
        '''仅替换 ros_agent_srv 入口；保留子进程、stdin 和专用就绪管道。'''
        return test_temp_popen([sys.executable, str(test_temp_child), str(test_temp_received)], **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', test_temp_substitute_entry)
    test_temp_bridge = AgentBridge(test_temp_root, test_temp_run)
    try:
        test_temp_bridge.start()
        test_temp_wait(lambda: test_temp_bridge.status()['ready'])
        test_input_text = '  补拍桥塔横撑\n保留换行与末尾空格。  '
        test_temp_bridge.send(test_input_text)
        test_temp_wait(test_temp_received.exists)
        import json
        assert json.loads(test_temp_received.read_text()) == test_input_text
        assert not test_temp_bridge.status()['ready']
        with pytest.raises(ValueError, match='未就绪或正在执行'):
            test_temp_bridge.send('第二条任务')
        assert len((test_temp_run / 'human_inputs.jsonl').read_text().splitlines()) == 1
    finally:
        test_temp_bridge.close()
        if test_temp_bridge.process:
            test_temp_bridge.process.wait(timeout=5)
        if test_temp_bridge.log:
            test_temp_bridge.log.close()


def test_temp_artifact_path_escape_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''拒绝通过目录回退或符号链接读取 artifacts 之外的内容。'''
    test_temp_artifacts = tmp_path / 'artifacts'
    test_temp_artifacts.mkdir()
    test_temp_outside = tmp_path / 'outside'
    test_temp_outside.mkdir()
    (test_temp_artifacts / 'link').symlink_to(test_temp_outside, target_is_directory=True)
    monkeypatch.setattr(server, 'ARTIFACTS', test_temp_artifacts)
    for test_input_path in ('../outside', 'link', '.'):
        with pytest.raises(ValueError):
            server.run_path(test_input_path)


def test_temp_new_input_does_not_reuse_previous_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''新输入等待模型时，不能把上一轮输出和成功报告挂到新任务下。'''
    test_temp_run = tmp_path / 'run'
    (test_temp_run / 'behavior_results').mkdir(parents=True)
    (test_temp_run / 'agent.jsonl').write_text(json.dumps({
        'type': 'llm_response', 'recorded_at': '2026-01-01T00:00:00+00:00',
        'response': '{"name":"capture_photo","input":"{}"}'}) + '\n')
    (test_temp_run / 'human_inputs.jsonl').write_text(json.dumps({
        'recorded_at': '2026-01-01T00:00:01+00:00', 'text': '新的任务'}) + '\n')
    test_temp_report = test_temp_run / 'behavior_results/old.json'
    test_temp_report.write_text('{"success":true,"steps":[]}')
    os.utime(test_temp_report, (1767225600, 1767225600))
    monkeypatch.setattr(server, 'ARTIFACTS', tmp_path)
    test_temp_snapshot = server.snapshot('run')
    assert test_temp_snapshot['human'] == '新的任务'
    assert test_temp_snapshot['llm'] == {}
    assert test_temp_snapshot['actions'] == []
    assert test_temp_snapshot['report'] == {}
