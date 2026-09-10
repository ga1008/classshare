"""No external model calls: real SQLite authority and HTTPX protocol fixtures."""
import contextlib
import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.schema_agent_authority import ensure_agent_authority_schema
from classroom_app.db.schema_agent_model import ensure_agent_model_schema
from classroom_app.db.schema_agent_request_budget import ensure_agent_request_budget_schema
from classroom_app.routers import agent_model_gateway as router
from classroom_app.services import agent_model_gateway_service as service
from classroom_app.services.agent_delegation_service import create_task_attempt, issue_task_delegation
from tests import test_agent_authority as authority_fixture


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = authority_fixture.AgentAuthorityTests()
        self.fixture.setUp(http_authority=False)
        self.conn = self.fixture.conn
        ensure_agent_authority_schema(self.conn)
        ensure_agent_model_schema(self.conn)
        ensure_agent_request_budget_schema(self.conn)
        self.conn.executescript("""
            CREATE TABLE user_sessions (session_id TEXT, session_user_key TEXT, user_id TEXT, role TEXT, expires_at TEXT);
            INSERT INTO user_sessions VALUES ('fixture-session', 'teacher:1', '1', 'teacher', '2099-01-01T00:00:00+00:00');
            CREATE TABLE agent_runtime_api_keys (id INTEGER PRIMARY KEY, last_used_at TEXT);
            INSERT INTO agent_runtime_api_keys VALUES (1, NULL);
        """)
        attempt = create_task_attempt(self.conn, task_id=7, worker_id="fixture", startup_key="start", lease_seconds=300)
        self.token = issue_task_delegation(self.conn, task_id=7, attempt_id=attempt["id"],
            fencing_token=attempt["fencing_token"], purpose="model", scopes=["model:chat", "model:search"],
            source_session_id="fixture-session")["token"]
        self.conn.commit()
        self.key = {"id": 1, "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com",
                    "key_fingerprint": "synthetic", "updated_at": "2026-09-10"}
        self.patches = [patch.object(service, "get_active_agent_api_key", side_effect=lambda conn: (self.key, "SYNTHETIC-MODEL-SECRET")),
                        patch.object(router, "get_db_connection", self.connection)]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(router.router)
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + self.token}

    @contextlib.contextmanager
    def connection(self):
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback()
            raise

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()

    def payload(self, **kwargs):
        return {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "synthetic"}], **kwargs}

    def reserve(self, **kwargs):
        return service.reserve_model_request(self.conn, self.token, endpoint="chat/completions", payload=self.payload(**kwargs))

    def test_reservation_uses_live_authority_and_separates_secret(self):
        grant = self.reserve(thinking={"type": "enabled"})
        self.assertNotIn("SYNTHETIC-MODEL-SECRET", repr(grant))
        self.assertEqual("https://api.deepseek.com/chat/completions", grant.url)
        self.assertEqual({"type": "enabled"}, grant.payload["thinking"])
        row = dict(self.conn.execute("SELECT * FROM agent_model_requests").fetchone())
        self.assertNotIn("SYNTHETIC-MODEL-SECRET", str(row))
        self.assertEqual("teacher", row["actor_role"])
        self.assertIsNone(row["input_tokens"])
        self.conn.execute("UPDATE agent_tasks SET cancel_requested_at = 'now' WHERE id = 7")
        with self.assertRaises(HTTPException):
            self.reserve()

    def test_model_host_and_budget_cannot_be_overridden(self):
        for payload in (self.payload(model="other"), self.payload(base_url="http://127.0.0.1"),
                        self.payload(max_tokens=True), self.payload(max_tokens=20000),
                        self.payload(max_completion_tokens=100000), self.payload(n=10)):
            with self.subTest(payload=payload), self.assertRaises(HTTPException):
                service.validate_model_payload(payload, model="deepseek-v4-pro", endpoint="chat/completions")
        for url in ("https://evil.invalid", "https://api.deepseek.com@127.0.0.1", "http://127.0.0.1"):
            with self.assertRaises(HTTPException):
                service.approved_model_base_url(url)

    def test_concurrent_and_total_request_budgets(self):
        first, second = self.reserve(), self.reserve()
        with self.assertRaises(HTTPException) as caught:
            self.reserve()
        self.assertEqual(429, caught.exception.status_code)
        service.finish_model_request(self.conn, first.id, status="completed", upstream_status=200)
        with patch.object(service, "MAX_REQUESTS_PER_TASK", 2), self.assertRaises(HTTPException):
            self.reserve()
        self.assertTrue(second.id)

    def test_crashed_gateway_reservation_expires(self):
        self.reserve()
        self.reserve()
        self.conn.execute("UPDATE agent_model_requests SET expires_at = 0")
        self.conn.execute("UPDATE agent_request_budget_leases SET expires_at_ms = 0")
        grant = self.reserve()
        self.assertTrue(grant.id)
        statuses = [row["status"] for row in self.conn.execute("SELECT status FROM agent_model_requests")]
        self.assertEqual(2, statuses.count("failed"))

    def test_receipt_missing_usage_stays_unknown_and_cas_is_idempotent(self):
        grant = self.reserve()
        service.finish_model_request(self.conn, grant.id, status="completed", upstream_status=200)
        service.finish_model_request(self.conn, grant.id, status="failed", upstream_status=500, usage={"prompt_tokens": 4})
        row = dict(self.conn.execute("SELECT * FROM agent_model_requests").fetchone())
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["usage_source"])
        self.assertEqual("completed", row["status"])

    def test_usage_sse_split_frames_and_anthropic_delta(self):
        collector = service.UsageCollector()
        for chunk in (b'data: {"usage":{"prompt_', b'tokens":9}}\r\n\n',
                      b'data: {"message":{"usage":{"input_tokens":7}}}\n',
                      b'data: {"usage":{"output_tokens":3}}\n\ndata: [DONE]\n\n'):
            collector.feed(chunk)
        self.assertEqual({"prompt_tokens": 9, "input_tokens": 7, "output_tokens": 3}, collector.usage)

    def test_http_stream_forwards_protocol_without_forwarding_token(self):
        seen = []
        body = b'data: {"choices":[{"delta":{"reasoning_content":"trace"}}]}\n\ndata: {"usage":{"prompt_tokens":8,"completion_tokens":2}}\n\ndata: [DONE]\n\n'
        def upstream(request):
            seen.append(request)
            self.assertEqual("Bearer SYNTHETIC-MODEL-SECRET", request.headers["authorization"])
            self.assertNotIn(self.token, str(request.headers))
            self.assertTrue(json.loads(request.content)["stream_options"]["include_usage"])
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)
        real_client = httpx.AsyncClient
        with patch.object(router.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs)):
            result = self.client.post("/api/agent-model/chat/completions", headers=self.headers, json=self.payload(stream=True))
        self.assertEqual(200, result.status_code)
        self.assertEqual(body, result.content)
        row = self.conn.execute("SELECT * FROM agent_model_requests").fetchone()
        self.assertEqual((8, 2, "completed"), (row["input_tokens"], row["output_tokens"], row["status"]))
        self.assertEqual(1, len(seen))

    def test_http_nonstream_and_key_generation_change(self):
        generation = service.configuration_generation(self.key)
        self.key["updated_at"] = "2026-09-11"
        def upstream(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 4}})
        real_client = httpx.AsyncClient
        with patch.object(router.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs)):
            result = self.client.post("/api/agent-model/chat/completions", headers=self.headers, json=self.payload())
        self.assertEqual(200, result.status_code)
        row = self.conn.execute("SELECT * FROM agent_model_requests").fetchone()
        self.assertNotEqual(generation, row["config_generation"])
        self.assertEqual(4, row["input_tokens"])

    def test_search_preserves_native_tools_and_uses_separate_endpoint(self):
        payload = self.payload(model="deepseek-flash", tools=[{"type": "web_search_20250305", "name": "web_search"}])
        grant = service.reserve_model_request(self.conn, self.token, endpoint="messages", payload=payload)
        self.assertEqual("https://api.deepseek.com/anthropic/v1/messages", grant.url)
        self.assertEqual(payload["tools"], grant.payload["tools"])

    def test_wrong_scope_or_logged_out_session_never_sends_model_request(self):
        self.conn.execute("DELETE FROM user_sessions")
        self.conn.commit()
        with patch.object(router.httpx, "AsyncClient") as client:
            result = self.client.post("/api/agent-model/chat/completions", headers=self.headers, json=self.payload())
        self.assertEqual(401, result.status_code)
        client.assert_not_called()

    def test_upstream_error_body_is_not_exposed(self):
        def upstream(request):
            return httpx.Response(401, json={"error": "SYNTHETIC-MODEL-SECRET"})
        real_client = httpx.AsyncClient
        with patch.object(router.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs)):
            result = self.client.post("/api/agent-model/chat/completions", headers=self.headers, json=self.payload())
        self.assertEqual(502, result.status_code)
        self.assertNotIn("SYNTHETIC-MODEL-SECRET", result.text)


class ModelStreamRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_revocation_interrupts_stalled_response_without_waiting_for_next_chunk(self):
        class Stalled:
            async def aiter_bytes(self):
                await asyncio.sleep(60)
                yield b"must not be emitted"

        async def revoked(*args):
            await asyncio.sleep(0.01)
            raise HTTPException(401, "revoked")

        with patch.object(router, "_watch_authority", revoked):
            with self.assertRaises(HTTPException):
                async with asyncio.timeout(1):
                    async for _ in router._authorized_chunks(Stalled(), "fixture", "chat/completions", 0):
                        self.fail("Revoked stream emitted data")
