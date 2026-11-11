'''通过环境配置调用 DeepSeek；保留显式固定行为开发模式。'''

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Any

import rclpy
from rclpy.node import Node
from rosllm_srvs.srv import LLM

_FIXED_BEHAVIOR = (
    '```json\n'
    '{"name":"hold","input":"{\\"duration_s\\":1.0}"}\n'
    '{"name":"rtl","input":"{}"}\n'
    '```'
)

_HTTP_TIMEOUT_S = 30.0
_MAX_RESPONSE_BYTES = 256 * 1024


class _LLMError(Exception):
    '''仅携带可公开的稳定错误码。'''


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    '''禁止认证请求随 HTTP 重定向转发。'''

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        '''返回 None，使标准库将重定向作为 HTTP 错误处理。'''
        return


def _request_completion(prompt: str, info: dict[str, Any]) -> str:
    '''发送一次非思考请求；保存用量，拒绝不完整结果，不重试。'''
    key = os.environ.get('FLIGHT_LLM_API_KEY', '')
    base = os.environ.get('FLIGHT_LLM_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    model = os.environ.get('FLIGHT_LLM_MODEL', 'deepseek-flash')
    if not key.strip() or base not in (
        'https://api.deepseek.com', 'https://api.deepseek.com/v1',
    ) or model != 'deepseek-flash':
        raise _LLMError('LLM_CONFIG_ERROR')
    request = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': model, 'messages': [{'role': 'user', 'content': prompt}],
            'thinking': {'type': 'disabled'}, 'max_tokens': 512,
            'stream': False, 'temperature': 0,
        }, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.build_opener(_NoRedirect()).open(
        request, timeout=_HTTP_TIMEOUT_S,
    ) as response:
        if response.status != 200:
            info['http_status'] = response.status
            raise _LLMError('LLM_HTTP_ERROR')
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise _LLMError('LLM_INVALID_RESPONSE')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise _LLMError('LLM_INVALID_RESPONSE')
    info.update({
        'model': data.get('model'), 'response_id': data.get('id'),
        'usage': data.get('usage'),
    })
    usage = data.get('usage')
    counts = ('prompt_tokens', 'completion_tokens', 'total_tokens',
              'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
    if not isinstance(usage, dict) or any(
        type(usage.get(name)) is not int or usage[name] < 0 for name in counts
    ) or usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens'] or (
        usage['prompt_cache_hit_tokens'] + usage['prompt_cache_miss_tokens']
        != usage['prompt_tokens']
    ):
        raise _LLMError('LLM_USAGE_UNAVAILABLE')
    choices = data.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _LLMError('LLM_INVALID_RESPONSE')
    choice = choices[0]
    info['finish_reason'] = choice.get('finish_reason')
    if info['finish_reason'] != 'stop':
        raise _LLMError('LLM_TRUNCATED' if info['finish_reason'] == 'length' else 'LLM_INCOMPLETE')
    message = choice.get('message')
    if not isinstance(message, dict) or message.get('role') != 'assistant' or (
        message.get('tool_calls') or not isinstance(message.get('content'), str)
        or not message['content'].strip()
    ) or not isinstance(data.get('model'), str) or not data['model']:
        raise _LLMError('LLM_INVALID_RESPONSE')
    return str(message['content'])


class LLMNode(Node):
    '''持有串行 LLM 服务，模型只生成文本，不执行动作。'''

    def __init__(self) -> None:
        super().__init__('llm_node')
        self.declare_parameter('mx_fixed_behavior', False)
        self.create_service(LLM, 'call_llm', self.get_response)

    def get_response(self, request: LLM.Request, response: LLM.Response) -> LLM.Response:
        '''返回固定开发行为或一次真实模型结果；失败清空行为且不回显异常。'''
        if self.get_parameter('mx_fixed_behavior').value is True:
            response.success = True
            response.response = _FIXED_BEHAVIOR
            response.info = 'MX_FIXED_BEHAVIOR: hold,rtl'
            return response
        info: dict[str, Any] = {'source': 'deepseek_api', 'error_code': None}
        response.success = False
        response.response = ''
        try:
            response.response = _request_completion(request.prompt, info)
            response.success = True
        except _LLMError as error:
            info['error_code'] = str(error)
        except urllib.error.HTTPError as error:
            info.update(error_code='LLM_HTTP_ERROR', http_status=error.code)
            error.close()
        except (TimeoutError, urllib.error.URLError) as error:
            timed_out = isinstance(error, TimeoutError) or isinstance(
                getattr(error, 'reason', None), TimeoutError,
            )
            info['error_code'] = 'LLM_TIMEOUT' if timed_out else 'LLM_NETWORK_ERROR'
        except (ValueError, TypeError, HTTPException):
            info['error_code'] = 'LLM_INVALID_RESPONSE'
        except OSError:
            info['error_code'] = 'LLM_NETWORK_ERROR'
        response.info = json.dumps(info, ensure_ascii=False, sort_keys=True)
        return response


def main() -> None:
    '''启动从环境读取连接配置的模型服务。'''

    rclpy.init()
    node = LLMNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
