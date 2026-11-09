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
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.sqlite"
        self.logins = 0
        self.unauthorized = False
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
            return httpx.Response(200, json={"errNo": 0, "token": f"synthetic-token-{self.logins}", "isTeacher": True})
        if request.url.path == "/api/user/getUserInfo":
            return httpx.Response(200, json={"name": "synthetic", "token": "must-not-escape"})
        return httpx.Response(401 if self.unauthorized else 200, json={"ok": not self.unauthorized})

    async def read(self, access=None, **kwargs):
        async with auth.open_authenticated_smart_classroom_client(access or self.access, **kwargs) as (client, _, result):
            response = await client.post("/synthetic-read")
            return response.status_code, result

    def expire_cooldown(self):
        with self.connection() as conn:
            conn.execute("UPDATE smart_classroom_sessions SET next_login_at_ms=0")
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

    async def test_401_is_persistent_and_does_not_replay_or_relogin(self):
        await self.read()
        self.unauthorized = True
        with self.assertRaises(sessions.SmartClassroomSessionError) as error:
            await self.read()
        self.assertEqual("needs_teacher_reauth", error.exception.code)
        self.unauthorized = False
        for _ in range(2):
            with self.assertRaises(sessions.SmartClassroomSessionError):
                await self.read()
        self.assertEqual(1, self.logins)
        self.assertEqual(2, sum(r.url.path == "/api/synthetic-read" for r in self.requests))
        with self.connection() as conn:
            state = sessions.session_public_status(conn, self.access)
        self.assertEqual("needs_teacher_reauth", state["state"])
        self.assertNotIn("token", json.dumps(state))

    async def test_scoped_recovery_is_bounded_and_cooldown_persisted(self):
        await self.read()
        with self.connection() as conn:
            sessions.grant_background_login(conn, self.access, "lesson:synthetic", expires_at_ms=int(time.time()*1000)+300000)
            conn.commit()
        self.unauthorized = True
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read()
        self.unauthorized = False
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read(login_scope="lesson:synthetic", allow_login=True)
        self.assertEqual(1, self.logins)
        self.expire_cooldown()
        await self.read(login_scope="lesson:synthetic", allow_login=True)
        self.assertEqual(2, self.logins)
        self.unauthorized = True
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read()
        self.unauthorized = False
        self.expire_cooldown()
        # A different classroom or teacher sharing the account cannot reset the global budget.
        with self.connection() as conn:
            sessions.grant_background_login(conn, self.access, "lesson:other", expires_at_ms=int(time.time()*1000)+300000)
            conn.commit()
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read(login_scope="lesson:other", allow_login=True)
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
        self.unauthorized = True
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read(current)
        self.unauthorized = False
        self.expire_cooldown()
        with self.assertRaises(sessions.SmartClassroomSessionError):
            await self.read(current, login_scope="lesson:synthetic", allow_login=True)
        self.assertEqual(1, self.logins)
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
