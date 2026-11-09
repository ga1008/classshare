"""Real ASGI boundary and configuration ledger, disposable synthetic DB only."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.db.schema_smart_classroom_auto_sync import ensure_smart_classroom_auto_sync_schema
from classroom_app.db.schema_smart_classroom_sessions import ensure_smart_classroom_sessions_schema
from classroom_app.dependencies import get_current_user
from classroom_app.routers import smart_classroom_write as routes
from classroom_app.services import smart_classroom_auto_sync_service as auto
from classroom_app.services import smart_classroom_integration_service as auth
from classroom_app.services import smart_classroom_session_service as sessions
from classroom_app.services.attendance_report_service import sign_attendance_source_option
from classroom_app.services.smart_classroom_session_service import SmartClassroomSessionError
from tests import test_smart_classroom_write as fixtures


class AutoSyncRouterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SmartWriteTests(methodName="test_handoff_diff_external_change_and_readback_conserve_all_people")
        # IsolatedAsyncioTestCase cleanup requires its runner even when the
        # fixture is used from a synchronous ASGI test. Without it, cleanup
        # errors are swallowed by the nested TestCase and its mocks leak.
        self.fixture._setupAsyncioRunner()
        self.addCleanup(self.fixture._tearDownAsyncioRunner)
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        ensure_smart_classroom_auto_sync_schema(self.fixture.conn)
        self.fixture.conn.commit()
        self.fixture.context.update(week_index=11, is_current=True, closed=False)
        self.user = self.fixture.user.copy()
        self.patch(routes, "get_db_connection", self.fixture.database)
        self.patch(auto, "get_db_connection", self.fixture.database)
        self.real_grant = auto._grant
        self.grant = self.patch(auto, "_grant", Mock())
        self.schedule = self.patch(auto, "schedule_due_auto_sync", Mock())
        app = FastAPI()
        app.include_router(routes.router)

        def session():
            if self.user is None:
                raise HTTPException(401, "synthetic anonymous")
            return self.user

        app.dependency_overrides[get_current_user] = session
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.base = "/api/classrooms/11/login-attendance/rounds/31/external"
        self.slot = {
            "kind": "smart_attendance_slot", "local_round_id": "31", "source_id": "opaque-group",
            "external_account_key": self.fixture.account_key, "schedule_id": "actual-slot",
            "session_date": "2026-11-09", "week": 11, "day_of_week": 1,
            "sections": [2, 3], "section": 2, "remote_checkin_id": "synthetic-remote-round",
        }
        self.payload = {
            "source_token": sign_attendance_source_option(1, self.fixture.source),
            "slot_token": sign_attendance_source_option(1, self.slot),
            "acknowledge_session_takeover": True, "allow_create": False,
            "idempotency_key": "synthetic-first-config",
        }

    def patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def configure(self, **overrides):
        return self.client.put(self.base + "/auto-sync", json={**self.payload, **overrides})

    def real_session(self):
        """Use the actual durable cache and school protocol with a fake HTTP wire."""
        conn = self.fixture.conn
        conn.execute("CREATE TABLE teacher_smart_classroom_credentials(id INTEGER PRIMARY KEY,teacher_id INTEGER,"
                     "platform_code TEXT,username TEXT,password_encrypted TEXT,enabled INTEGER,last_status TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO teacher_smart_classroom_credentials VALUES(7,1,'gxufl_smart_classroom','synthetic',?,1,'verified','v1')",
                     (auth.encrypt_academic_secret("synthetic-password"),))
        ensure_smart_classroom_sessions_schema(conn)
        conn.commit()
        row = conn.execute("SELECT * FROM teacher_smart_classroom_credentials").fetchone()
        self.fixture.access.update(password="synthetic-password", credential_generation=auth.credential_generation(row))
        self.patch(sessions, "get_db_connection", self.fixture.database)
        self.patch(auto, "_grant", self.real_grant)
        self.login_count = 0

        async def wire(request):
            self.assertFalse(conn.in_transaction, "School I/O must run outside DB transactions")
            if request.url.path == "/api/login":
                self.login_count += 1
                return httpx.Response(200, json={"errNo": 0, "token": f"synthetic-token-{self.login_count}", "isTeacher": True})
            return httpx.Response(200, json={"name": "synthetic"})

        self.patch(auth, "SmartClassroomMiniTransport", lambda: httpx.MockTransport(wire))

        async def ordinary_read():
            async with auth.open_authenticated_smart_classroom_client(self.fixture.access, api="mini") as (client, _, _):
                await client.get("/synthetic-read")

        asyncio.run(ordinary_read())
        self.assertEqual(200, self.configure().status_code)
        conn.execute("UPDATE smart_classroom_sessions SET token_encrypted='',state='needs_teacher_reauth',"
                     "recovery_used=1,next_login_at_ms=?", (int(time.time() * 1000) + 60000,))
        conn.execute("UPDATE smart_classroom_session_grants SET recovery_count=1")
        conn.commit()
        return ordinary_read

    def test_owner_can_view_and_configure_once_without_leaking_server_identity(self):
        initial = self.client.get(self.base + "/auto-sync")
        self.assertEqual(200, initial.status_code)
        self.assertIsNone(initial.json()["plan"])
        first, repeated = self.configure(), self.configure()
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(first.json(), repeated.json())
        self.assertEqual("no-store", first.headers["cache-control"])
        self.assertEqual(1, self.grant.call_count)
        self.assertEqual(1, self.schedule.call_count)
        self.assertEqual(1, self.fixture.conn.execute("SELECT count(*) FROM smart_attendance_auto_sync").fetchone()[0])
        for key in ("credential_generation", "external_account_key", "source_token", "slot_token", "source_json", "slot_json", "intent_json", "password", "Authorization"):
            self.assertNotIn(key, first.text)
        self.assertEqual(1, first.json()["plan"]["revision"])

    def test_roles_ownership_and_offering_scope_block_before_mutation(self):
        for user, status in (({"role": "teacher", "id": 2}, 403), ({"role": "student", "id": 1}, 403), (None, 401)):
            self.user = user
            self.assertEqual(status, self.client.get(self.base + "/auto-sync").status_code)
            self.assertEqual(status, self.configure().status_code)
        self.user = self.fixture.user.copy()
        self.assertEqual(404, self.client.get(self.base.replace("classrooms/11", "classrooms/12") + "/auto-sync").status_code)
        self.assertEqual(0, self.schedule.call_count)

    def test_every_mutation_rejects_cross_origin_before_school_or_schedule(self):
        remote = AsyncMock(side_effect=AssertionError("school API must not run"))
        self.patch(auto, "list_slot_options", remote)
        cases = [
            ("PUT", "/auto-sync", self.payload),
            ("POST", "/slot-options", {"source_token": self.payload["source_token"]}),
            ("POST", "/auto-sync/pause", {"expected_revision": 1}),
            ("POST", "/auto-sync/retry", {"expected_revision": 1}),
            ("POST", "/auto-sync/reauthorize", {"expected_revision": 1}),
        ]
        for method, suffix, payload in cases:
            for headers in ({"Origin": "https://untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(suffix=suffix, headers=headers):
                    self.assertEqual(403, self.client.request(method, self.base + suffix, json=payload, headers=headers).status_code)
        self.assertEqual(0, remote.await_count)
        self.assertEqual(0, self.schedule.call_count)

    def test_location_and_unknown_fields_are_rejected_before_service(self):
        remote = AsyncMock(return_value={"plan": None})
        self.patch(auto, "configure_auto_sync", remote)
        good = {"latitude": 22.8, "longitude": 108.3, "captured_at_ms": int(time.time() * 1000)}
        invalid = [
            {**good, "latitude": 91}, {**good, "longitude": -181}, {**good, "captured_at_ms": 0},
            {**good, "latitude": "NaN"}, {**good, "longitude": "Infinity"},
            {**good, "latitude": True}, {**good, "longitude": "108.3"},
            {**good, "captured_at_ms": True}, {**good, "source": "spoofed"},
            {"latitude": 22.8, "longitude": 108.3},
        ]
        for value in invalid:
            with self.subTest(location=value):
                self.assertEqual(422, self.configure(location=value).status_code)
        self.assertEqual(422, self.configure(password="synthetic-secret").status_code)
        self.assertEqual(422, self.configure(acknowledge_session_takeover="true").status_code)
        self.assertEqual(0, remote.await_count)
        self.assertEqual(200, self.configure(location=good).status_code)
        self.assertEqual(1, remote.await_count)

    def test_missing_consent_forged_or_wrong_slot_never_schedules(self):
        self.assertEqual(422, self.configure(acknowledge_session_takeover=False).status_code)
        self.assertEqual(409, self.configure(source_token="forged").status_code)
        wrong = sign_attendance_source_option(1, {**self.slot, "day_of_week": 2})
        self.assertEqual(409, self.configure(slot_token=wrong).status_code)
        other_owner = sign_attendance_source_option(2, self.slot)
        self.assertEqual(409, self.configure(slot_token=other_owner).status_code)
        self.assertEqual(0, self.grant.call_count)
        self.assertEqual(0, self.schedule.call_count)

    def test_revision_pause_and_void_guards_preserve_existing_plan(self):
        self.assertEqual(200, self.configure().status_code)
        stale = self.configure(expected_revision=99, idempotency_key="another-config")
        self.assertEqual(409, stale.status_code)
        paused = self.client.post(self.base + "/auto-sync/pause", json={"expected_revision": 1})
        self.assertEqual(200, paused.status_code)
        self.assertEqual("paused", paused.json()["plan"]["state"])
        self.assertFalse(paused.json()["plan"]["enabled"])
        self.assertEqual(409, self.client.post(self.base + "/auto-sync/pause", json={"expected_revision": 1}).status_code)
        self.fixture.context.update(state="void", is_current=False)
        self.assertEqual(409, self.client.post(self.base + "/auto-sync/retry", json={"expected_revision": 2}).status_code)
        self.assertEqual(409, self.client.post(self.base + "/auto-sync/reauthorize", json={"expected_revision": 2}).status_code)
        self.assertEqual(1, self.schedule.call_count)
        self.assertEqual("paused", self.client.get(self.base + "/auto-sync").json()["plan"]["state"])

    def test_session_error_codes_survive_router_and_network_details_are_redacted(self):
        remote = AsyncMock()
        self.patch(auto, "list_slot_options", remote)
        for code, status in (("session_busy", 503), ("needs_teacher_reauth", 409), ("credential_changed", 409)):
            remote.side_effect = SmartClassroomSessionError(code)
            response = self.client.post(self.base + "/slot-options", json={"source_token": self.payload["source_token"]})
            self.assertEqual(status, response.status_code, response.text)
            self.assertEqual(code, response.json()["detail"]["code"])
        remote.side_effect = httpx.ReadTimeout("synthetic-sensitive-token=must-never-leak")
        response = self.client.post(self.base + "/slot-options", json={"source_token": self.payload["source_token"]})
        self.assertEqual(503, response.status_code)
        self.assertEqual("school_connection_unavailable", response.json()["detail"]["code"])
        self.assertNotIn("must-never-leak", response.text)

    def test_explicit_reauthorization_restores_exhausted_session_and_reuses_it(self):
        ordinary_read = self.real_session()
        with self.assertRaises(SmartClassroomSessionError):
            asyncio.run(ordinary_read())
        response = self.client.post(self.base + "/auto-sync/reauthorize", json={"expected_revision": 1})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(2, response.json()["plan"]["revision"])
        self.assertEqual("pending", response.json()["plan"]["state"])
        self.assertEqual(2, self.login_count)
        asyncio.run(ordinary_read())
        self.assertEqual(2, self.login_count)
        self.assertEqual(2, self.schedule.call_count)
        self.assertNotIn("synthetic-token", response.text)

    def test_busy_session_reauthorization_preserves_plan_without_new_login_or_dispatch(self):
        self.real_session()
        self.fixture.conn.execute("UPDATE smart_classroom_sessions SET login_owner='synthetic-other-process',login_until_ms=?",
                                  (int(time.time() * 1000) + 60000,))
        self.fixture.conn.commit()
        with patch.object(sessions.asyncio, "sleep", new=AsyncMock()):
            response = self.client.post(self.base + "/auto-sync/reauthorize", json={"expected_revision": 1})
        self.assertEqual(503, response.status_code, response.text)
        self.assertEqual("session_busy", response.json()["detail"]["code"])
        self.assertEqual(1, self.login_count)
        self.assertEqual(1, self.schedule.call_count)
        self.assertEqual(1, self.client.get(self.base + "/auto-sync").json()["plan"]["revision"])


if __name__ == "__main__":
    unittest.main()
