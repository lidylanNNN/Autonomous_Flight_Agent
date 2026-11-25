'''仅监听本机的交互界面：真实记录浏览、媒体读取和既有入口输入。'''
from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bridge import AgentBridge

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / 'artifacts'
STATIC = Path(__file__).resolve().parent


def read_json(path: Path, default: object = None) -> object:
    '''读取已完成 JSON；写入过程中的文件在下一次刷新重读。'''
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_lines(path: Path) -> list[dict]:
    '''读取真实 JSONL，容忍仍在写入的末行。'''
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def run_path(run_id: str) -> Path:
    '''限定到 artifacts 子目录，拒绝越界和符号链接逃逸。'''
    path = (ARTIFACTS / run_id).resolve()
    if not path.is_relative_to(ARTIFACTS.resolve()) or path == ARTIFACTS.resolve() or not path.is_dir():
        raise ValueError('记录不存在或路径无效')
    return path


def snapshot(run_id: str) -> dict:
    '''将既有记录格式组合为展示数据，不替代任务验收结论。'''
    path = run_path(run_id)
    agent, safety = read_lines(path / 'agent.jsonl'), read_lines(path / 'safety.jsonl')
    states = read_lines(path / 'world.jsonl')
    submissions = read_lines(path / 'human_inputs.jsonl')
    round_start = datetime.fromisoformat(submissions[-1]['recorded_at']).timestamp() if submissions else 0
    responses = [r for r in agent if r.get('type') == 'llm_response'
                 and datetime.fromisoformat(r['recorded_at']).timestamp() >= round_start]
    llm = responses[-1] if responses else {}
    human = submissions[-1]['text'] if submissions else ((path / 'test_temp_human.txt').read_text() if (path / 'test_temp_human.txt').exists() else '')
    actions = []
    for line in llm.get('response', '').splitlines():
        if line.strip().startswith('{'):
            try:
                action = json.loads(line)
                action['arguments'] = json.loads(action['input'])
                actions.append(action)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    report_files = sorted((p for p in (path / 'behavior_results').glob('*.json')
                           if p.stat().st_mtime >= round_start), key=lambda p: p.stat().st_mtime)
    report = read_json(report_files[-1], {}) if report_files else {}
    result = read_json(path / 'test_temp_result.json', {})
    correction = read_json(path / 'test_temp_input_scope_correction.json', {})
    video = next((p.name for p in (path / 'bridge_capture_demo.mp4', path / 'm7_flight_demo.mp4') if p.exists()), None)
    poster = f'{run_id}/test_temp_preview.png' if (path / 'test_temp_preview.png').exists() else None
    photos = []
    for step in report.get('steps', []):
        if step.get('name') == 'capture_photo' and step.get('success'):
            output = json.loads(step['output'])
            image_path = Path(output['image_path']).resolve()
            if image_path.is_relative_to(ARTIFACTS.resolve()):
                photos.append({'path': str(image_path.relative_to(ARTIFACTS)), 'id': output['photo_id']})
    marker = path / 'test_temp_recording_started_at.txt'
    origin = datetime.fromisoformat(marker.read_text()).timestamp() - 1 if marker.exists() else None
    recovery = read_json(path / 'test_temp_operator_recovery_response.json', {})
    return {'id': run_id, 'human': human, 'submissions': submissions, 'llm': llm, 'agent_events': agent,
        'operator_recovery': recovery.get('output', {}),
        'actions': actions, 'safety': safety, 'states': states[::max(1, len(states) // 500)],
        'last_state': states[-1] if states else None, 'report': report, 'result': result,
        'correction': correction, 'video': f'{run_id}/{video}' if video else None, 'video_origin': origin,
        'photos': photos, 'poster': poster, 'console': (path / 'agent_console.log').read_text()[-12000:] if (path / 'agent_console.log').exists() else ''}


class Handler(BaseHTTPRequestHandler):
    '''提供只绑定本机的页面与有限接口。'''

    def do_GET(self) -> None:
        '''返回记录、实际状态或受限媒体。'''
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == '/api/status':
                self.respond(200, self.server.bridge.status())
            elif parsed.path == '/api/runs':
                paths = {p.parent for p in ARTIFACTS.glob('*/agent.jsonl')} | {p.parent for p in ARTIFACTS.glob('operator_ui/*/agent.jsonl')}
                if self.server.bridge.run_dir:
                    paths.add(self.server.bridge.run_dir)
                self.respond(200, [{'id': str(p.relative_to(ARTIFACTS)), 'updated': p.stat().st_mtime}
                                  for p in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)])
            elif parsed.path == '/api/run':
                self.respond(200, snapshot(query.get('id', [''])[0]))
            elif parsed.path == '/media':
                path = (ARTIFACTS / query.get('path', [''])[0]).resolve()
                if not path.is_relative_to(ARTIFACTS.resolve()) or path.suffix not in {'.mp4', '.png', '.json', '.jsonl', '.txt'}:
                    raise ValueError('不允许读取该文件')
                self.file(path)
            elif parsed.path in {'/', '/app.js', '/style.css'}:
                self.file(STATIC / ('index.html' if parsed.path == '/' else parsed.path[1:]))
            else:
                self.respond(404, {'error': '接口不存在'})
        except (ValueError, FileNotFoundError) as error:
            self.respond(400, {'error': str(error)})

    def do_POST(self) -> None:
        '''在原入口就绪时接受原文；不提供任意命令执行接口。'''
        try:
            if self.headers.get('Origin') != f'http://{self.headers.get("Host")}' or self.headers.get('X-Flight-UI') != '1':
                raise ValueError('请求来源无效')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16000:
                raise ValueError('请求长度无效')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise TypeError('请求必须为 JSON 对象')
            if self.path == '/api/connect':
                self.server.bridge.start()
            elif self.path == '/api/input':
                self.server.bridge.send(body.get('text'))
            else:
                raise ValueError('接口不存在')
            self.respond(200, self.server.bridge.status())
        except (ValueError, TypeError, OSError) as error:
            self.respond(409, {'error': str(error)})

    def respond(self, status: int, value: object) -> None:
        '''发送不缓存的 UTF-8 JSON。'''
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def file(self, path: Path) -> None:
        '''支持视频字节范围请求，逐块传输而不整体载入内存。'''
        size = path.stat().st_size
        start, end = 0, size - 1
        partial = self.headers.get('Range', '').startswith('bytes=')
        if partial:
            first, last = self.headers['Range'][6:].split('-', 1)
            if not first:
                start = max(0, size - int(last))
            else:
                start, end = int(first), min(int(last) if last else end, end)
            if not 0 <= start <= end:
                self.send_error(416)
                return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    '''创建本地 HTTP 服务；仅显式传入 live-dir 时允许连接真实入口。'''
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--live-dir', type=Path)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.bridge = AgentBridge(ROOT, args.live_dir.resolve() if args.live_dir else None)
    print(f'http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.bridge.close()
        server.server_close()


if __name__ == '__main__':
    main()
