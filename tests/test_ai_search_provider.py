"""DeepSeek raw Search contract, purpose-bound dispatch and durable replay; no external sockets."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from classroom_app.db.schema_ai_search import ensure_ai_search_schema
from classroom_app.services import ai_search_quota_service as ledger
from classroom_app.services import ai_web_search_service as search
from classroom_app.services.ai_search_transport import (
    normalize_query, safe_result_url, sanitize_results, search_fingerprint,
    signed_search_request, verify_search_request, TIMESTAMP_HEADER, SIGNATURE_HEADER,
)
from classroom_app.services.deepseek_search_provider import SEARCH_URL, search_deepseek

RESULT = {'title': '公开资料', 'url': 'https://example.org/public?q=a(b)', 'summary': '合成公开事实', 'content': '非可信资料'}
USER = {'role': 'student', 'id': 1}


class SearchTransportTests(unittest.TestCase):
    def test_query_size_unicode_privacy_and_no_silent_truncation(self):
        self.assertEqual(normalize_query('  最新  课程信息 '), '最新 课程信息')
        self.assertEqual(normalize_query('中' * 128), '中' * 128)
        for value in ('', 'x' * 129, 'someone@example.org', '13800000000', '学号 2026001234', 'api_key=secretKey', 'https://u:p@example.org'):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                normalize_query(value)

    def test_signed_body_timestamp_and_purpose_cannot_be_forged(self):
        body, headers = signed_search_request({'query': '公开问题'}, now=1800000000)
        verify_search_request(body, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], now=1800000001)
        for raw, stamp, signature, now in ((body + b' ', headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], 1800000000),
                (body, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], 1800000061),
                (body, '1' * 5000, 'x', 1800000000), (b'x' * 4097, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], 1800000000)):
            with self.assertRaises(HTTPException):
                verify_search_request(raw, stamp, signature, now=now)

    def test_only_safe_public_source_links_and_bounded_utf8_cache(self):
        for url in ('javascript:alert(1)', 'file:///tmp/a', 'http://localhost/a', 'http://127.1/a',
                    'http://127.0.0.1/a', 'http://2130706433/a', 'https://u:p@example.org/', 'https://example.org:8000/', '//example.org/'):
            self.assertIsNone(safe_result_url(url), url)
        result = sanitize_results({'results': [{**RESULT, 'title': '中' * 1000, 'content': '长' * 20000}] * 50})
        self.assertLessEqual(len(result), 5)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 24000)
        self.assertNotIn('(', result[0]['url'])


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def run_provider(self, handler):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_deepseek('公开问题', 'synthetic-key', client=client)

    async def test_exact_official_request_and_validated_results(self):
        def handler(request):
            self.assertEqual(str(request.url), SEARCH_URL)
            self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-key')
            self.assertEqual(json.loads(request.content), {'query': '公开问题', 'count': 5, 'need_content': True})
            return httpx.Response(200, json={'results': [RESULT]})
        result = await self.run_provider(handler)
        self.assertEqual(result['outcome'], 'succeeded')
        self.assertEqual(len(result['results']), 1)

    async def test_only_explicit_unbilled_http_failure_is_retried_and_bounded(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429, headers={'Retry-After': '0'}) if len(calls) == 1 else httpx.Response(200, json={'results': [RESULT]})
        self.assertEqual((await self.run_provider(handler))['outcome'], 'succeeded')
        self.assertEqual(len(calls), 2)
        calls.clear()
        def forbidden(request):
            calls.append(request)
            return httpx.Response(402)
        self.assertEqual((await self.run_provider(forbidden))['outcome'], 'failed')
        self.assertEqual(len(calls), 1)

    async def test_empty_is_free_but_unsafe_nonempty_still_consumes(self):
        self.assertEqual((await self.run_provider(lambda r: httpx.Response(200, json={'results': []})))['outcome'], 'empty')
        result = await self.run_provider(lambda r: httpx.Response(200, json={'results': [{**RESULT, 'url': 'http://127.0.0.1/'}]}))
        self.assertEqual((result['outcome'], result['results']), ('succeeded', []))

    async def test_200_malformed_and_oversize_are_uncertain_never_free(self):
        for response in (httpx.Response(200, text='not json'), httpx.Response(200, json={'wrong': []}), httpx.Response(200, content=b'x' * 262145)):
            with self.subTest(response=response):
                self.assertEqual((await self.run_provider(lambda r: response))['outcome'], 'unknown')

    async def test_lost_response_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout('private response must not leak', request=request)
        result = await self.run_provider(handler)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('private', json.dumps(result))


class SearchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lanshare-search-')
        self.path = Path(self.tmp.name) / 'synthetic.db'
        self.connections, self.guard, self.provider_calls = 0, threading.Lock(), []
        with self.connect() as conn:
            ensure_ai_search_schema(conn)
            conn.execute('CREATE TABLE wanderer_agents(id INTEGER PRIMARY KEY,student_id INTEGER,status TEXT)')
            conn.execute("INSERT INTO wanderer_agents VALUES(7,70,'active')")
            conn.commit()
        self.patcher = patch.object(search, 'get_db_connection', self.connect)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        with self.guard:
            self.connections += 1
        try:
            yield conn
        finally:
            conn.close()
            with self.guard:
                self.connections -= 1

    async def provider(self, query, key):
        self.assertEqual(self.connections, 0, 'No DB connection may be held across provider HTTP')
        self.provider_calls.append(query)
        return {'outcome': 'succeeded', 'results': sanitize_results({'results': [RESULT]})}

    def client(self, *, provider=None, enabled=True, lose_reply=False):
        owner = self
        class Client:
            async def post(self, endpoint, *, content, headers, timeout):
                owner.assertEqual(endpoint, search.PROVIDER_ENDPOINT)
                verify_search_request(content, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER])
                reply = await search.dispatch_reserved_search(json.loads(content), api_key='synthetic-key', enabled=enabled,
                                                              provider_call=provider or owner.provider)
                if lose_reply:
                    raise httpx.ReadTimeout('lost reply')
                return httpx.Response(200, json=reply, request=httpx.Request('POST', 'http://synthetic/dispatch'))
        return Client()

    async def test_search_settles_before_model_and_replay_is_free(self):
        first = await search.search_for_user(USER, '公开问题', 'turn-1', client=self.client())
        second = await search.search_for_user(USER, '公开问题', 'turn-1', client=self.client())
        self.assertEqual((first['status'], second['status'], first['quota']['used']), ('success', 'success', 1))
        self.assertEqual(self.provider_calls, ['公开问题'])
        self.assertIn('非可信', first['text'])
        with self.assertRaises(HTTPException):
            await search.search_for_user(USER, '另一问题', 'turn-1', client=self.client())

    async def test_actual_private_asgi_endpoint_joins_signing_cas_provider_and_settlement(self):
        import ai_assistant as ai
        from classroom_app.services import deepseek_search_provider as provider
        from unittest.mock import AsyncMock
        with patch.dict(ai.PLATFORMS_CONFIG, {'deepseek': {'enabled': True, 'api_key': 'synthetic-key'}}), \
                patch.object(provider, 'search_deepseek', new_callable=AsyncMock, side_effect=self.provider) as call:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ai.app), base_url='http://synthetic') as client:
                first = await search.search_for_user(USER, '公开问题', 'asgi-turn', client=client)
                replay = await search.search_for_user(USER, '公开问题', 'asgi-turn', client=client)
        self.assertEqual((first['status'], replay['status'], first['quota']['used']), ('success', 'success', 1))
        self.assertEqual(call.await_count, 1)

    async def test_no_key_or_definite_failure_releases_budget(self):
        no_key = await search.search_for_user(USER, '公开问题', 'disabled', client=self.client(enabled=False))
        self.assertEqual((no_key['status'], no_key['quota']['remaining']), ('unavailable', 10))
        async def failed(query, key):
            return {'outcome': 'failed', 'error': 'provider_http_402', 'results': []}
        result = await search.search_for_user(USER, '公开问题', 'failed', client=self.client(provider=failed))
        self.assertEqual((result['quota']['remaining'], result['quota']['failed']), (10, 1))
        self.assertEqual(self.provider_calls, [])

    async def test_lost_app_reply_does_not_refund_or_repeat_successful_search(self):
        result = await search.search_for_user(USER, '公开问题', 'lost', client=self.client(lose_reply=True))
        self.assertEqual((result['status'], result['quota']['used']), ('success', 1))
        again = await search.search_for_user(USER, '公开问题', 'lost', client=self.client())
        self.assertEqual(again['status'], 'success')
        self.assertEqual(len(self.provider_calls), 1)

    async def test_unknown_is_occupied_and_replay_never_dispatches(self):
        async def uncertain(query, key):
            self.provider_calls.append(query)
            return {'outcome': 'unknown', 'error': 'provider_result_uncertain', 'results': []}
        result = await search.search_for_user(USER, '公开问题', 'unknown', client=self.client(provider=uncertain))
        self.assertEqual((result['quota']['used'], result['quota']['unknown'], result['quota']['remaining']), (0, 1, 9))
        await search.search_for_user(USER, '公开问题', 'unknown', client=self.client())
        self.assertEqual(len(self.provider_calls), 1)

    async def test_dispatch_token_query_and_actor_cannot_be_forged(self):
        with self.connect() as conn:
            receipt = ledger.reserve(conn, actor_kind='student', actor_id=1, channel='chat', logical_call_id='authorized', fingerprint=search_fingerprint('公开问题'))
            conn.commit()
        for payload in ({'call_id': receipt['call_id'], 'dispatch_token': 'wrong', 'query': '公开问题'},
                        {'call_id': receipt['call_id'], 'dispatch_token': receipt['dispatch_token'], 'query': '另一问题'}):
            with self.assertRaises(HTTPException):
                await search.dispatch_reserved_search(payload, api_key='x', enabled=True, provider_call=self.provider)
        self.assertEqual(self.provider_calls, [])
        with self.assertRaises(HTTPException):
            await search.search_for_user(USER, '公开问题', 'agent-by-student', channel='agent', client=self.client())

    async def test_wanderer_uses_own_verified_identity_and_not_the_friend(self):
        result = await search.search_for_wanderer({'id': 7, 'student_id': 70}, '公开问题', 'wanderer', client=self.client())
        self.assertEqual((result['quota']['channel'], result['quota']['used']), ('wanderer', 1))
        with self.connect() as conn:
            self.assertEqual(search.snapshot_for_user(conn, USER)['used'], 0)
            conn.execute("UPDATE wanderer_agents SET status='dead' WHERE id=7")
            conn.commit()
        with self.assertRaises(HTTPException):
            await search.search_for_wanderer({'id': 7, 'student_id': 70}, '公开问题', 'dead', client=self.client())

    async def test_cancel_during_reservation_commit_releases_unsent_owner(self):
        started, allow = threading.Event(), threading.Event()
        original = ledger.reserve
        def gated(*args, **kwargs):
            result = original(*args, **kwargs)
            started.set()
            self.assertTrue(allow.wait(5))
            return result
        with patch.object(ledger, 'reserve', gated):
            task = asyncio.create_task(search.search_for_user(USER, '公开问题', 'cancel-reserve', client=self.client()))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel(); allow.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with self.connect() as conn:
            self.assertEqual(search.snapshot_for_user(conn, USER)['remaining'], 10)
        self.assertEqual(self.provider_calls, [])

    async def test_cancel_during_claim_commit_is_definite_failure_before_provider(self):
        with self.connect() as conn:
            receipt = ledger.reserve(conn, actor_kind='student',actor_id=1,channel='chat',logical_call_id='cancel-claim',fingerprint=search_fingerprint('公开问题'))
            conn.commit()
        started, allow = threading.Event(), threading.Event()
        original = ledger.mark_sent
        def gated(*args, **kwargs):
            result = original(*args, **kwargs)
            started.set()
            self.assertTrue(allow.wait(5))
            return result
        payload = {'call_id': receipt['call_id'], 'dispatch_token': receipt['dispatch_token'], 'query': '公开问题'}
        with patch.object(ledger, 'mark_sent', gated):
            task = asyncio.create_task(search.dispatch_reserved_search(payload, api_key='x',enabled=True,provider_call=self.provider))
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            task.cancel(); allow.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with self.connect() as conn:
            self.assertEqual(search.snapshot_for_user(conn, USER)['remaining'], 10)
            self.assertEqual(ledger.get_call(conn, receipt['call_id'])['state'], 'failed')
        self.assertEqual(self.provider_calls, [])


if __name__ == '__main__':
    unittest.main()
