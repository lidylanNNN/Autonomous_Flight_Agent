'''本地 UI 到既有 Agent 输入入口的薄桥，不包含规划或飞行逻辑。'''
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread


class AgentBridge:
    '''由 HTTP 服务持有一个真实入口进程，按入口等待状态接受原文。'''

    def __init__(self, root: Path, run_dir: Path | None) -> None:
        '''保存路径与进程锁，不自动启动入口或模型。'''
        self.root, self.run_dir = root, run_dir
        self.process: subprocess.Popen[str] | None = None
        self.ready = False
        self.lock = Lock()
        self.log = None

    def status(self) -> dict:
        '''读取真实入口状态，不以进程存活代替飞行服务健康。'''
        with self.lock:
            active = self.process is not None and self.process.poll() is None
            return {'enabled': self.run_dir is not None, 'active': active,
                    'ready': active and self.ready, 'run_id': str(self.run_dir.relative_to(self.root / 'artifacts')) if self.run_dir else None}

    def start(self) -> None:
        '''仅在明确配置的本轮环境目录中创建实际 Agent 主循环。'''
        with self.lock:
            if self.run_dir is None:
                raise ValueError('未配置实时环境')
            if self.process is not None:
                raise ValueError('该入口会话已经启动，请继续使用当前会话')
            self.run_dir.mkdir(parents=True, exist_ok=True)
            read_fd, write_fd = os.pipe()
            env = dict(os.environ, AFA_UI_READY_FD=str(write_fd))
            env.pop('FLIGHT_LLM_API_KEY', None)
            self.log = (self.run_dir / 'agent_console.log').open('w')
            command = [sys.executable, str(self.root / 'scripts/operator_ui/agent_input.py'),
                '--ros-args', '-p', f'agent_trace_path:={self.run_dir / "agent.jsonl"}',
                '-p', f'behavior_result_dir:={self.run_dir / "behavior_results"}']
            try:
                self.process = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.PIPE,
                    stdout=self.log, stderr=subprocess.STDOUT, text=True, pass_fds=(write_fd,))
            except BaseException:
                os.close(read_fd)
                self.log.close()
                raise
            finally:
                os.close(write_fd)
            Thread(target=self._watch_ready, args=(read_fd,), daemon=True).start()

    def send(self, text: str) -> None:
        '''只在入口等待时写入 JSON 编码原文，拒绝忙碌与空白请求。'''
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 8000:
            raise ValueError('请输入不超过 8000 字节的非空任务')
        with self.lock:
            if self.process is None or self.process.poll() is not None or not self.ready:
                raise ValueError('入口未就绪或正在执行，未发送任务')
            assert self.process.stdin is not None and self.run_dir is not None
            self.ready = False
            # 记录时间仅作输入来源，不改写或补充任务内容。
            with (self.run_dir / 'human_inputs.jsonl').open('a') as stream:
                stream.write(json.dumps({'recorded_at': datetime.now(UTC).isoformat(), 'text': text}, ensure_ascii=False) + '\n')
            self.process.stdin.write(json.dumps(text, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def close(self) -> None:
        '''关闭入口 stdin；不把退出会话称作急停或返航。'''
        with self.lock:
            if self.process is not None and self.process.stdin is not None:
                self.process.stdin.close()
            if self.log is not None:
                self.log.close()
            self.ready = False

    def _watch_ready(self, read_fd: int) -> None:
        '''仅接受专用就绪管道消息，不从模型文本推断是否可发送。'''
        try:
            while os.read(read_fd, 1):
                with self.lock:
                    self.ready = True
        finally:
            os.close(read_fd)
            with self.lock:
                self.ready = False
