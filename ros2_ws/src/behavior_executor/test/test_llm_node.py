'''在网络边界验证实际 LLM 回调；不访问供应商。'''

import json
import os
import sqlite3
import urllib.error
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
import rclpy
from agent_comm import llm_node
from agent_comm.llm_budget import BudgetError, reserve_budget
from rosllm_srvs.srv import LLM


@pytest.mark.parametrize('test_input_case', [
    'success', 'missing_key', 'bad_url', 'bad_model', 'http', 'redirect', 'timeout',
    'network', 'bad_json', 'oversize', 'empty', 'truncated', 'incomplete', 'tools',
    'usage_missing', 'usage_inconsistent',
    'budget_missing', 'budget_corrupt', 'budget_low', 'budget_pending',
    'budget_prompt', 'budget_overrun',
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
    test_input_prompt = '测试完整 prompt'
    if test_input_case.startswith('budget_'):
        path = Path(os.environ['FLIGHT_LLM_BUDGET_DB'])
        expected_output_error = {
            'budget_missing': 'LLM_BUDGET_STORAGE_ERROR',
            'budget_corrupt': 'LLM_BUDGET_STORAGE_ERROR',
            'budget_low': 'LLM_BUDGET_EXCEEDED', 'budget_pending': 'LLM_BUDGET_UNSETTLED',
            'budget_prompt': 'LLM_PROMPT_TOO_LARGE',
            'budget_overrun': 'LLM_BUDGET_ESTIMATE_EXCEEDED',
        }[test_input_case]
        if test_input_case == 'budget_missing':
            monkeypatch.setenv('FLIGHT_LLM_BUDGET_DB', str(path.with_name('absent.sqlite3')))
        elif test_input_case == 'budget_corrupt':
            path.write_bytes(b'invalid database')
        elif test_input_case in ('budget_low', 'budget_pending'):
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE budget SET spent_cny=?, pending=? WHERE id=1', (
                    '0.97' if test_input_case == 'budget_low' else '0',
                    int(test_input_case == 'budget_pending'),
                ))
        elif test_input_case == 'budget_prompt':
            test_input_prompt = 'x' * 20_001
        else:
            test_input_data['usage'].update(completion_tokens=7000, total_tokens=7010)
    elif test_input_case in ('missing_key', 'bad_url', 'bad_model'):
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
        response = node.get_response(LLM.Request(prompt=test_input_prompt), LLM.Response())
        info = json.loads(response.info)
        assert info['error_code'] == expected_output_error
        assert response.success is (expected_output_error is None)
        assert response.response == (
            test_input_data['choices'][0]['message']['content'] if response.success else ''
        )
        assert 'test-only-secret' not in response.info
        if expected_output_error == 'LLM_CONFIG_ERROR' or test_input_case in {
            'budget_missing', 'budget_corrupt', 'budget_low', 'budget_pending', 'budget_prompt',
        }:
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
            assert info['cost_cny'] == '0.00005608'
            assert info['budget_pending'] is False
        if test_input_case in ('success', 'timeout', 'budget_overrun'):
            node.destroy_node()
            node = llm_node.LLMNode()
            reply = node.get_response(LLM.Request(prompt=test_input_prompt), LLM.Response())
            second_info = json.loads(reply.info)
            if test_input_case == 'success':
                assert reply.success
                assert Decimal(second_info['budget_spent_cny']) == Decimal('0.00011216')
                assert test_temp_opener.open.call_count == 2
            else:
                assert not reply.success and second_info['error_code'] == 'LLM_BUDGET_UNSETTLED'
                assert test_temp_opener.open.call_count == 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_budget_reservation_is_visible_to_another_connection() -> None:
    '''请求尚未返回时，另一个账本连接不能重复预留；中断后标志保留。'''
    info: dict[str, object] = {}
    with (
        reserve_budget('first', info),
        pytest.raises(BudgetError, match='LLM_BUDGET_UNSETTLED'),
        reserve_budget('second', {}),
    ):
        pytest.fail('第二个请求不得进入 HTTP 区间')
    with (
        pytest.raises(BudgetError, match='LLM_BUDGET_UNSETTLED'),
        reserve_budget('after_unknown_result', {}),
    ):
        pytest.fail('未知费用不得自动释放预留')
