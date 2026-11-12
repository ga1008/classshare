"""Disposable SQLite plus MockTransport only; never connects to a school API."""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from classroom_app.db.connection import LanShareSQLiteConnection
from classroom_app.db.schema_smart_classroom_sessions import ensure_smart_classroom_sessions_schema
from classroom_app.services import smart_classroom_integration_service as auth
from classroom_app.services import smart_classroom_session_service as sessions


class SchoolSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "synthetic.sqlite"
        self.logins = 0
        self.unauthorized = False
        self.expired_tokens = set()
        self.login_failure = None
        self.requests = []
        with self.connection() as conn:
            conn.execute("""CREATE TABLE teacher_smart_classroom_credentials(
                id INTEGER PRIMARY KEY,teacher_id INTEGER,platform_code TEXT,platform_name TEXT,adapter_key TEXT,
                auth_method TEXT,base_url TEXT,api_base_url TEXT,login_url TEXT,username TEXT,password_encrypted TEXT,
                display_name TEXT,enabled INTEGER,last_status TEXT,last_status_at TEXT,last_error TEXT,
                last_verified_at TEXT,access_method_json TEXT,created_at TEXT,updated_at TEXT,
                UNIQUE(teacher_id,platform_code,auth_method))""")
            ensure_smart_classroom_sessions_schema(conn)
            conn.commit()
        self.patch(sessions, "get_db_connection", self.connection)
        self.patch(auth, "SmartClassroomTransport", lambda: httpx.MockTransport(self.request))
        self.patch(auth, "SmartClassroomMiniTransport", lambda: httpx.MockTransport(self.request))
        self.access = self.save_teacher(1)

    def patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connection(self):
        conn = sqlite3.connect(self.path, factory=LanShareSQLiteConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def save_teacher(self, teacher, *, username="synthetic-teacher", password="synthetic-password"):
        payload = {"username": username, "password": password}
        with self.connection() as conn:
            auth.save_verified_smart_classroom_credential(conn, teacher, payload, {"status": "verified"})
            conn.commit()
            return auth.load_teacher_smart_classroom_access_method(conn, teacher)

    async def request(self, request):
        self.requests.append(request)
        if request.url.path == "/api/login":
            self.logins += 1
            await asyncio.sleep(.01)
            if self.login_failure:
                return httpx.Response(200, json=self.login_failure)
            return httpx.Response(200, json={"errNo": 0, "token": f"synthetic-token-{self.logins}", "isTeacher": True})
        if request.url.path == "/api/user/getUserInfo":
            return httpx.Response(200, json={"name": "synthetic", "token": "must-not-escape"})
        unauthorized = self.unauthorized or request.headers.get("Authorization") in self.expired_tokens
        return httpx.Response(401 if unauthorized else 200, json={"ok": not unauthorized})

    async def read(self, access=None, **kwargs):
        async with auth.open_authenticated_smart_classroom_client(access or self.access, **kwargs) as (client, _, result):
            response = await client.post("/teaching/teacherSchedule/list")
            return response.status_code, result

    def expire_cooldown(self):
        with self.connection() as conn:
            conn.execute("UPDATE smart_classroom_sessions SET next_login_at_ms=0,last_login_ms=0")
            conn.commit()

    async def test_parallel_requests_and_other_teacher_share_one_login(self):
        other = self.save_teacher(2)
        results = await asyncio.gather(self.read(), self.read(), self.read(other), self.read(api="mini"))
        self.assertEqual([200] * 4, [item[0] for item in results])
        self.assertEqual(1, self.logins)
        with self.connection() as conn:
            row = dict(conn.execute("SELECT * FROM smart_classroom_sessions").fetchone())
        self.assertNotIn("synthetic-token", json.dumps(row))
        self.assertNotIn("synthetic-password", json.dumps(row))
        self.assertNotIn("must-not-escape", json.dumps(results))
        self.assertTrue(all(r.headers.get("Tenant-Id") == "1" for r in self.requests))

    async def test_expired_read_recovers_and_replays_without_per_lesson_grant(self):
        await self.read()
        self.expired_tokens.add("synthetic-token-1")
        self.assertEqual(200, (await self.read())[0])
        self.assertEqual(2, self.logins)
        self.assertEqual(3, sum(r.url.path == "/api/teaching/teacherSchedule/list" for r in self.requests))
        with self.connection() as conn:
            state = sessions.session_public_status(conn, self.access)
        self.assertEqual("active", state["state"])
        self.assertNotIn("token", json.dumps(state))

    async def test_repeated_unauthorized_is_bounded_and_recovers_after_cooldown(self):
        await self.read()
        self.unauthorized = True
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read()
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read()
        self.assertEqual(2, self.logins)
        self.unauthorized = False
        self.expire_cooldown()
        self.assertEqual(200, (await self.read())[0])
        self.assertEqual(3, self.logins)

    async def test_concurrent_expiry_coalesces_one_recovery(self):
        await self.read()
        self.expired_tokens.add("synthetic-token-1")
        results = await asyncio.gather(self.read(), self.read(), self.read(api="mini"))
        self.assertEqual([200] * 3, [value[0] for value in results])
        self.assertEqual(2, self.logins)

    async def test_same_client_delayed_replay_401_cannot_revoke_newer_token(self):
        replay_started, release_replay = asyncio.Event(), asyncio.Event()
        original = self.request
        async def interleaved(request):
            token = request.headers.get("Authorization")
            if request.url.path.endswith("/list") and token == "synthetic-token-1":
                return httpx.Response(401)
            if request.url.path.endswith("/list") and token == "synthetic-token-2":
                replay_started.set()
                await release_replay.wait()
                return httpx.Response(401)
            if request.url.path.endswith("/view") and token == "synthetic-token-2":
                return httpx.Response(401)
            return await original(request)
        self.patch(auth, "SmartClassroomTransport", lambda: httpx.MockTransport(interleaved))
        async with auth.open_authenticated_smart_classroom_client(self.access) as (client, _, _):
            delayed = asyncio.create_task(client.post("/teaching/teacherSchedule/list"))
            try:
                await asyncio.wait_for(replay_started.wait(), timeout=2)
                # Advance the persisted recovery window; a concurrent request
                # now legitimately obtains token 3 while token 2 is in flight.
                self.expire_cooldown()
                self.assertEqual(200, (await client.post("/teaching/teacherSchedule/view")).status_code)
                release_replay.set()
                with self.assertRaises(sessions.SmartClassroomSessionError):
                    await delayed
                with self.connection() as conn:
                    self.assertEqual("active", sessions.session_public_status(conn, self.access)["state"])
                self.assertEqual(200, (await client.post("/teaching/teacherSchedule/view")).status_code)
                self.assertEqual(3, self.logins)
            finally:
                release_replay.set()
                await asyncio.gather(delayed, return_exceptions=True)

    async def test_write_401_recovers_but_never_replays_mutation(self):
        await self.read()
        self.expired_tokens.add("synthetic-token-1")
        path = "/teaching/teacherSchedule/changeStuStatus"
        async with auth.open_authenticated_smart_classroom_client(self.access, api="mini") as (client, _, _):
            with self.assertRaises(sessions.SmartClassroomSessionError) as error:
                await client.post(path, data={"synthetic": "only"})
            self.assertEqual("session_refreshed", error.exception.code)
        self.assertEqual(1, sum(r.url.path == "/api" + path for r in self.requests))
        self.assertEqual(2, self.logins)
        self.assertEqual(200, (await self.read())[0])

    async def test_captcha_is_distinct_and_does_not_hammer_saved_password(self):
        self.login_failure = {"errNo": 1, "msg": "请输入验证码 token=must-not-escape"}
        for _ in range(2):
            with self.assertRaises(sessions.SmartClassroomSessionError) as error:
                await self.read()
            self.assertEqual("captcha_required", error.exception.code)
            self.assertNotIn("must-not-escape", str(error.exception))
        self.assertEqual(1, self.logins)

    async def test_transient_verification_keeps_saved_credential_generation(self):
        await self.read()
        with self.connection() as conn:
            auth.update_smart_classroom_credential_verification_status(conn, 1, self.access["credential_id"], {
                "status": "failed", "error_code": "captcha_required", "message": "需要验证码"})
            conn.commit()
            current = auth.load_teacher_smart_classroom_access_method(conn, 1)
        self.assertEqual(self.access["credential_generation"], current["credential_generation"])
        self.assertEqual(200, (await self.read(current))[0])

    async def test_json_unauthorized_is_recovered_but_other_errors_are_not(self):
        await self.read()
        original = self.request
        async def json_expiry(request):
            if request.url.path.endswith("/list") and request.headers.get("Authorization") == "synthetic-token-1":
                return httpx.Response(200, json={"errNo": "401"})
            return await original(request)
        self.patch(auth, "SmartClassroomTransport", lambda: httpx.MockTransport(json_expiry))
        self.assertEqual(200, (await self.read())[0])
        self.assertEqual(2, self.logins)

    async def test_verify_save_reuses_fresh_token_and_revokes_old_generation(self):
        await self.read()
        payload = {"username": "synthetic-teacher", "password": "新的合成密码"}
        verified = await auth.verify_smart_classroom_credential(payload)
        self.assertTrue(verified["ok"])
        self.assertNotIn("synthetic-token", json.dumps(verified))
        self.assertNotIn("must-not-escape", json.dumps(verified))
        with self.connection() as conn:
            auth.save_verified_smart_classroom_credential(conn, 1, payload, verified)
            conn.commit()
            current = auth.load_teacher_smart_classroom_access_method(conn, 1)
        await self.read(current)
        self.assertEqual(2, self.logins)
        with self.assertRaises(sessions.SmartClassroomSessionError) as error:
            await self.read(self.access)
        self.assertEqual("credential_changed", error.exception.code)
        self.assertEqual(2, self.logins)

    async def test_old_401_does_not_invalidate_new_session(self):
        await self.read()
        with self.connection() as conn:
            old_version = conn.execute("SELECT token_version FROM smart_classroom_sessions").fetchone()[0]
        await self.read(force_reauth=True)
        sessions.mark_session_unauthorized(sessions.SchoolSession(sessions.account_key(self.access), old_version, "old"))
        await self.read()
        self.assertEqual(2, self.logins)

    async def test_generation_change_cannot_inherit_grants(self):
        await self.read()
        with self.connection() as conn:
            sessions.grant_background_login(conn, self.access, "lesson:synthetic", expires_at_ms=int(time.time()*1000)+300000)
            conn.commit()
        current = self.save_teacher(1)
        self.expired_tokens.add("synthetic-token-1")
        await self.read(current, login_scope="lesson:synthetic", allow_login=True)
        self.assertEqual(2, self.logins)
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read(self.access)
        with self.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT count(*) FROM smart_classroom_session_grants").fetchone()[0])

    async def test_delete_fences_inflight_access_and_clears_cache(self):
        await self.read()
        with self.connection() as conn:
            auth.delete_teacher_smart_classroom_credential(conn, 1, self.access["credential_id"])
            conn.commit()
            self.assertEqual("", conn.execute("SELECT token_encrypted FROM smart_classroom_sessions").fetchone()[0])
        with self.assertRaises(sessions.SmartClassroomSessionError) as error:
            await self.read()
        self.assertEqual("credential_changed", error.exception.code)

    async def test_uncertain_login_does_not_bootstrap_again(self):
        fail = AsyncMock(side_effect=httpx.ReadTimeout("synthetic timeout"))
        with self.assertRaises(httpx.ReadTimeout):
            await sessions.acquire_session(self.access, fail)
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await sessions.acquire_session(self.access, fail)
        self.assertEqual(1, fail.await_count)

    async def test_changed_password_for_shared_account_cannot_evict_new_token(self):
        await self.read()
        other = self.save_teacher(2, password="different-old-password")
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read(other)
        await self.read()
        self.assertEqual(1, self.logins)


class MiniTransportContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_mini_uses_normal_hostname_and_never_caller_sni(self):
        transport = auth.SmartClassroomMiniTransport()
        try:
            with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=AsyncMock(return_value=httpx.Response(200))) as send:
                request = httpx.Request("POST", "https://edu-api.gxufl.com/api/fixture",
                                        headers={"Host": "attacker.invalid"}, extensions={"sni_hostname": "gxufl.com"})
                await transport.handle_async_request(request)
                self.assertEqual("edu-api.gxufl.com", request.headers["host"])
                self.assertEqual("edu-api.gxufl.com", request.extensions["sni_hostname"])
                for url in ("http://edu-api.gxufl.com/api", "https://attacker.invalid/api", "https://edu-api.gxufl.com:444/api"):
                    with self.assertRaises(ValueError):
                        await transport.handle_async_request(httpx.Request("GET", url))
                self.assertEqual(1, send.await_count)
        finally:
            await transport.aclose()


if __name__ == "__main__":
    unittest.main()
