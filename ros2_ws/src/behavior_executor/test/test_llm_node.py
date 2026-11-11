'''在网络边界验证实际 LLM 回调；不访问供应商。'''

import json
import urllib.error
from unittest.mock import MagicMock, Mock

import pytest
import rclpy
from agent_comm import llm_node
from rosllm_srvs.srv import LLM


@pytest.mark.parametrize('test_input_case', [
    'success', 'missing_key', 'bad_url', 'bad_model', 'http', 'redirect', 'timeout',
    'network', 'bad_json', 'oversize', 'empty', 'truncated', 'incomplete', 'tools',
    'usage_missing', 'usage_inconsistent',
])
def test_llm_callback_checks_request_response_and_redacts_errors(
    monkeypatch: pytest.MonkeyPatch, test_input_case: str,
) -> None:
    '''保留请求构造、响应验证和错误映射，只替换 HTTP opener。'''
    monkeypatch.setenv('FLIGHT_LLM_API_KEY', 'test-only-secret')
    monkeypatch.setenv('FLIGHT_LLM_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('FLIGHT_LLM_MODEL', 'deepseek-flash')
    test_input_data = {
        'id': 'test-response', 'model': 'deepseek-flash',
        'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '  ```json\n{"name":"rtl","input":"{}"}\n```  ',
        }}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                  'prompt_cache_hit_tokens': 2, 'prompt_cache_miss_tokens': 8},
    }
    expected_output_error = None
    if test_input_case in ('missing_key', 'bad_url', 'bad_model'):
        name, value = {
            'missing_key': ('FLIGHT_LLM_API_KEY', ''),
            'bad_url': ('FLIGHT_LLM_BASE_URL', 'https://untrusted.invalid'),
            'bad_model': ('FLIGHT_LLM_MODEL', 'deepseek-v4-pro'),
        }[test_input_case]
        monkeypatch.setenv(name, value)
        expected_output_error = 'LLM_CONFIG_ERROR'
    elif test_input_case in ('truncated', 'incomplete'):
        test_input_data['choices'][0]['finish_reason'] = (
            'length' if test_input_case == 'truncated' else 'content_filter'
        )
        expected_output_error = 'LLM_TRUNCATED' if test_input_case == 'truncated' else 'LLM_INCOMPLETE'
    elif test_input_case in ('empty', 'tools'):
        test_input_data['choices'][0]['message'].update(
            {'content': ''} if test_input_case == 'empty' else {'tool_calls': [{'id': 'test-tool'}]},
        )
        expected_output_error = 'LLM_INVALID_RESPONSE'
    elif test_input_case.startswith('usage_'):
        test_input_data['usage'] = None if test_input_case == 'usage_missing' else {
            **test_input_data['usage'], 'total_tokens': 999,
        }
        expected_output_error = 'LLM_USAGE_UNAVAILABLE'
    test_temp_response = MagicMock()
    test_temp_response.__enter__.return_value = test_temp_response
    test_temp_response.status = 302 if test_input_case == 'redirect' else 200
    test_temp_response.read.return_value = (
        b'not-json test-only-secret' if test_input_case == 'bad_json' else
        b'x' * (llm_node._MAX_RESPONSE_BYTES + 1) if test_input_case == 'oversize' else
        json.dumps(test_input_data).encode()
    )
    test_temp_opener = Mock()
    test_temp_opener.open.return_value = test_temp_response
    if test_input_case in ('http', 'timeout', 'network'):
        test_temp_opener.open.side_effect = {
            'http': urllib.error.HTTPError('https://api.deepseek.com', 401,
                                          'test-only-secret', None, None),
            'timeout': TimeoutError('test-only-secret'),
            'network': urllib.error.URLError('test-only-secret'),
        }[test_input_case]
        expected_output_error = {
            'http': 'LLM_HTTP_ERROR', 'timeout': 'LLM_TIMEOUT', 'network': 'LLM_NETWORK_ERROR',
        }[test_input_case]
    elif test_input_case == 'redirect':
        expected_output_error = 'LLM_HTTP_ERROR'
    elif test_input_case in ('bad_json', 'oversize'):
        expected_output_error = 'LLM_INVALID_RESPONSE'
    test_temp_builder = Mock(return_value=test_temp_opener)
    monkeypatch.setattr(llm_node.urllib.request, 'build_opener', test_temp_builder)
    rclpy.init()
    node = llm_node.LLMNode()
    try:
        response = node.get_response(LLM.Request(prompt='测试完整 prompt'), LLM.Response())
        info = json.loads(response.info)
        assert info['error_code'] == expected_output_error
        assert response.success is (expected_output_error is None)
        assert response.response == (
            test_input_data['choices'][0]['message']['content'] if response.success else ''
        )
        assert 'test-only-secret' not in response.info
        if expected_output_error == 'LLM_CONFIG_ERROR':
            test_temp_builder.assert_not_called()
        else:
            test_temp_opener.open.assert_called_once()
            request = test_temp_opener.open.call_args.args[0]
            assert request.full_url == 'https://api.deepseek.com/chat/completions'
            assert request.get_header('Authorization') == 'Bearer test-only-secret'
            assert json.loads(request.data) == {
                'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': '测试完整 prompt'}],
                'thinking': {'type': 'disabled'}, 'max_tokens': 512, 'stream': False, 'temperature': 0,
            }
            assert test_temp_opener.open.call_args.kwargs['timeout'] == 30
            assert isinstance(test_temp_builder.call_args.args[0], llm_node._NoRedirect)
            assert test_temp_builder.call_args.args[0].redirect_request() is None
        if response.success or test_input_case == 'truncated':
            assert info['usage'] == test_input_data['usage']
    finally:
        node.destroy_node()
        rclpy.shutdown()
