"""Private AI search dispatch boundary and real tool-message wire format; no provider calls."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import ai_assistant as ai
from classroom_app.services import ai_web_search_service as search
from classroom_app.services.ai_search_transport import WEB_SEARCH_TOOL, signed_search_request


class DispatchEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ai.app)
        self.payload = {'call_id': 'synthetic-call', 'dispatch_token': 'synthetic-token', 'query': '公开问题'}

    def signed(self, payload=None):
        return signed_search_request(payload or self.payload)

    def test_unsigned_expired_or_tampered_dispatch_never_reaches_provider(self):
        with patch.object(search, 'dispatch_reserved_search', new_callable=AsyncMock) as dispatch:
            self.assertEqual(self.client.post('/api/ai/search-dispatch', json=self.payload).status_code, 401)
            body, headers = self.signed()
            self.assertEqual(self.client.post('/api/ai/search-dispatch', content=body+b' ', headers=headers).status_code, 401)
            body, headers = signed_search_request(self.payload, now=1000000000)
            self.assertEqual(self.client.post('/api/ai/search-dispatch', content=body, headers=headers).status_code, 401)
            dispatch.assert_not_called()

    def test_only_exact_signed_reservation_envelope_is_accepted(self):
        with patch.object(search, 'dispatch_reserved_search', new_callable=AsyncMock, return_value={'status': 'empty'}) as dispatch:
            body, headers = self.signed()
            response = self.client.post('/api/ai/search-dispatch', content=body, headers=headers)
            self.assertEqual((response.status_code, response.json()), (200, {'status': 'empty'}))
            self.assertEqual(dispatch.call_args.args[0], self.payload)
            body, headers = self.signed({**self.payload, 'actor_id': 999})
            self.assertEqual(self.client.post('/api/ai/search-dispatch', content=body, headers=headers).status_code, 422)
            self.assertEqual(dispatch.await_count, 1)

    def test_oversize_body_is_rejected_before_dispatch(self):
        with patch.object(search, 'dispatch_reserved_search', new_callable=AsyncMock) as dispatch:
            self.assertEqual(self.client.post('/api/ai/search-dispatch', content=b'x'*4097).status_code, 413)
            dispatch.assert_not_called()


class ToolWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_flat_legacy_calls_and_standard_roundtrip_with_reasoning_are_both_preserved(self):
        flat = {'id': 'call-1', 'type': 'function', 'name': 'web_search', 'arguments': {'query': '公开问题'}}
        messages = [{'role': 'user', 'content': '请查公开资料'},
                    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'previous', 'type': 'function',
                        'function': {'name': 'web_search', 'arguments': '{"query":"旧问题"}'}}], 'reasoning_content': 'previous reasoning'},
                    {'role': 'tool', 'tool_call_id': 'previous', 'content': 'synthetic evidence'}]
        request = ai.AIChatRequest(system_prompt='系统', messages=messages, new_message='', tools=[WEB_SEARCH_TOOL])
        with patch.object(ai, '_call_ai_platform', new_callable=AsyncMock,
                          return_value={'text': '', 'tool_calls': [flat], 'reasoning_content': 'private reasoning'}) as model:
            data = await ai.ai_chat_task(request)
        self.assertEqual(data['tool_calls'], [flat], 'Existing platform_query callers keep their flat contract')
        self.assertEqual(data['provider_tool_calls'][0]['function']['name'], 'web_search')
        self.assertEqual(json.loads(data['provider_tool_calls'][0]['function']['arguments']), flat['arguments'])
        self.assertEqual(data['reasoning_content'], 'private reasoning')
        sent = model.call_args.args[0]
        self.assertEqual(sent[-1]['role'], 'tool', 'A tool continuation must not append an empty user turn')
        self.assertEqual(sent[-2]['reasoning_content'], 'previous reasoning')

    async def test_final_json_after_tool_result_is_parsed_without_leaking_tool_wire(self):
        request = ai.AIChatRequest(system_prompt='只返回JSON', new_message='', response_format='json',
            messages=[{'role': 'user', 'content': '公开问题'}, {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'evidence'}])
        with patch.object(ai, '_call_ai_platform', new_callable=AsyncMock, return_value={'parts': ['答复']}) as model:
            data = await ai.ai_chat_task(request)
        self.assertEqual(data['response_json'], {'parts': ['答复']})
        self.assertNotIn('reasoning_content', data)
        self.assertEqual(model.call_args.args[0][-1]['role'], 'tool')


if __name__ == '__main__':
    unittest.main()
