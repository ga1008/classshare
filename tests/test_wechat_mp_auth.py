from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from classroom_app import dependencies
from classroom_app.db import schema_wechat_mp as schema
from classroom_app.db.postgres import sqlite_sql_to_psycopg
from classroom_app.routers.mp import auth as auth_routes, deps as mp_deps
from classroom_app.services import wechat_mp_service as service


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _seed_accounts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT, created_by_teacher_id INTEGER, department TEXT DEFAULT '')"
    )
    conn.execute(
        """
        CREATE TABLE students (
            id INTEGER PRIMARY KEY, class_id INTEGER, name TEXT,
            student_id_number TEXT, school_code TEXT DEFAULT '',
            department TEXT DEFAULT '', enrollment_status TEXT DEFAULT 'active',
            hashed_password TEXT DEFAULT '', password_reset_required INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, hashed_password TEXT, is_active INTEGER DEFAULT 1)"
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (1, '测试1班', 9)")
    conn.execute(
        "INSERT INTO students (id, class_id, name, student_id_number, school_code, department) "
        "VALUES (11, 1, '测试学生', '20260001', 'gxufl', '信息工程学院')"
    )
    conn.execute(
        "INSERT INTO teachers (id, name, email, hashed_password) VALUES (9, '测试教师', 't@example.com', 'x')"
    )


class MpDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(schema, "_SCHEMA_READY", False))
        self.enterContext(patch.object(schema, "get_configured_db_engine", return_value="sqlite"))
        self.conn = _fresh_conn()
        self.addCleanup(self.conn.close)
        _seed_accounts(self.conn)
        service.ensure_wechat_mp_runtime(self.conn)


class WechatMpSchemaAndBindingTests(MpDatabaseTestCase):
    def test_binding_roundtrip_and_openid_is_natural_key(self) -> None:
        self.assertIsNone(service.find_active_binding(self.conn, "openid-a"))
        binding = service.create_binding(
            self.conn, user_role="student", user_pk=11, openid="openid-a", unionid="u-1"
        )
        self.assertEqual(binding["user_pk"], 11)
        self.assertEqual(binding["status"], "active")

        # Same openid rebound to another account repoints the single row.
        rebound = service.create_binding(
            self.conn, user_role="teacher", user_pk=9, openid="openid-a"
        )
        self.assertEqual(rebound["user_role"], "teacher")
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM wechat_bindings WHERE openid = 'openid-a'"
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_revoke_binding_kills_sessions_too(self) -> None:
        service.create_binding(self.conn, user_role="student", user_pk=11, openid="openid-b")
        token = service.issue_mp_session(
            self.conn, user_role="student", user_pk=11, openid="openid-b"
        )
        self.assertIsNotNone(service.resolve_mp_session(self.conn, token))

        revoked = service.revoke_binding(self.conn, user_role="student", user_pk=11)
        self.assertEqual(revoked, 1)
        self.assertIsNone(service.find_active_binding(self.conn, "openid-b"))
        self.assertIsNone(service.resolve_mp_session(self.conn, token))

    def test_rebinding_revokes_only_this_openid_sessions(self) -> None:
        tokens = {}
        for openid in ("first-phone", "second-phone"):
            service.create_binding(self.conn, user_role="student", user_pk=11, openid=openid)
            tokens[openid] = service.issue_mp_session(
                self.conn, user_role="student", user_pk=11, openid=openid
            )
        service.create_binding(self.conn, user_role="teacher", user_pk=9, openid="first-phone")
        self.assertIsNone(service.resolve_mp_session(self.conn, tokens["first-phone"]))
        self.assertIsNotNone(service.resolve_mp_session(self.conn, tokens["second-phone"]))
        # Also reject old sessions written by a previous server version.
        self.conn.execute("UPDATE mp_sessions SET revoked = 0 WHERE openid = 'first-phone'")
        self.assertIsNone(service.resolve_mp_session(self.conn, tokens["first-phone"]))


class WechatMpSessionTests(MpDatabaseTestCase):
    def test_issue_and_resolve_session(self) -> None:
        token = service.issue_mp_session(self.conn, user_role="student", user_pk=11)
        session = service.resolve_mp_session(self.conn, token)
        self.assertIsNotNone(session)
        self.assertEqual(session["user_pk"], 11)
        # Clear token is never stored.
        stored = self.conn.execute("SELECT token_hash FROM mp_sessions").fetchone()["token_hash"]
        self.assertNotEqual(stored, token)

    def test_expired_session_is_rejected(self) -> None:
        token = service.issue_mp_session(self.conn, user_role="student", user_pk=11)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.conn.execute("UPDATE mp_sessions SET expires_at = ?", (past,))
        self.assertIsNone(service.resolve_mp_session(self.conn, token))

    def test_sliding_renewal_extends_expiry_when_stale(self) -> None:
        token = service.issue_mp_session(self.conn, user_role="student", user_pk=11)
        stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        near_expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.conn.execute(
            "UPDATE mp_sessions SET last_seen_at = ?, expires_at = ?", (stale, near_expiry)
        )
        self.assertIsNotNone(service.resolve_mp_session(self.conn, token))
        refreshed = self.conn.execute("SELECT expires_at FROM mp_sessions").fetchone()["expires_at"]
        refreshed_dt = datetime.fromisoformat(refreshed)
        self.assertGreater(
            refreshed_dt, datetime.now(timezone.utc) + timedelta(days=service.MP_SESSION_TTL_DAYS - 1)
        )

    def test_revoke_session(self) -> None:
        token = service.issue_mp_session(self.conn, user_role="teacher", user_pk=9)
        self.assertTrue(service.revoke_mp_session(self.conn, token))
        self.assertIsNone(service.resolve_mp_session(self.conn, token))

    def test_load_mp_user_shapes(self) -> None:
        student = service.load_mp_user(self.conn, {"user_role": "student", "user_pk": 11})
        self.assertEqual(student["role"], "student")
        self.assertEqual(student["student_id_number"], "20260001")
        self.assertEqual(student["class_name"], "测试1班")
        teacher = service.load_mp_user(self.conn, {"user_role": "teacher", "user_pk": 9})
        self.assertEqual(teacher["role"], "teacher")
        self.assertEqual(teacher["email"], "t@example.com")
        self.assertIsNone(service.load_mp_user(self.conn, {"user_role": "student", "user_pk": 999}))

    def test_loading_student_checks_live_enrollment_using_shared_lifecycle_rules(self):
        for status in ("suspended", "休学"):
            self.conn.execute("UPDATE students SET enrollment_status = ? WHERE id = 11", (status,))
            self.assertIsNone(service.load_mp_user(self.conn, {"user_role": "student", "user_pk": 11}))
        self.conn.execute("UPDATE students SET enrollment_status = NULL WHERE id = 11")
        self.assertIsNotNone(service.load_mp_user(self.conn, {"user_role": "student", "user_pk": 11}))


class WechatMpTicketAndRateLimitTests(MpDatabaseTestCase):
    def test_bind_ticket_roundtrip(self) -> None:
        ticket = service.build_bind_ticket("openid-x", "union-x")
        payload = service.decode_bind_ticket(ticket)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["openid"], "openid-x")
        self.assertEqual(payload["unionid"], "union-x")
        self.assertIsNone(service.decode_bind_ticket("not-a-ticket"))
        self.assertNotEqual(ticket, service.build_bind_ticket("openid-x", "union-x"))

    def test_ticket_consumption_replay_and_transaction_rollback(self):
        self.conn.commit()
        ticket = service.build_bind_ticket("openid-x")
        self.assertIsNotNone(service.consume_bind_ticket(self.conn, ticket))
        self.conn.rollback()
        self.assertIsNotNone(service.consume_bind_ticket(self.conn, ticket))
        self.conn.commit()
        self.assertIsNone(service.consume_bind_ticket(self.conn, ticket))
        self.assertIsNone(service.consume_bind_ticket(self.conn, "invalid"))

    def test_expired_and_legacy_tickets_cannot_be_consumed(self):
        from jose import jwt
        payload = service.decode_bind_ticket(service.build_bind_ticket("openid-x"))
        payload["exp"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp()
        expired = jwt.encode(payload, service.SECRET_KEY, algorithm=service.ALGORITHM)
        self.assertIsNone(service.consume_bind_ticket(self.conn, expired))
        payload["exp"] += 600
        del payload["jti"]
        legacy = jwt.encode(payload, service.SECRET_KEY, algorithm=service.ALGORITHM)
        self.assertIsNone(service.consume_bind_ticket(self.conn, legacy))

    def test_bind_rate_limit_trips_after_max_attempts(self) -> None:
        for _ in range(service.BIND_RATE_MAX_ATTEMPTS):
            service.check_bind_rate_limit(self.conn, "openid:limit-me")
        with self.assertRaises(service.WechatMpError):
            service.check_bind_rate_limit(self.conn, "openid:limit-me")
        # Other keys are unaffected.
        service.check_bind_rate_limit(self.conn, "openid:someone-else")

    def test_bind_rate_limit_expires_and_deduplicates_keys(self):
        for _ in range(service.BIND_RATE_MAX_ATTEMPTS):
            service.check_bind_rate_limit(self.conn, "openid:x", "openid:x")
        old = datetime.now(timezone.utc) - timedelta(minutes=11)
        self.conn.execute("UPDATE mp_bind_rate_limits SET updated_at = ?", (old.isoformat(),))
        service.check_bind_rate_limit(self.conn, "openid:x")


class WechatMpAuthRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="lanshare-mp-auth-"))
        self.db_path = Path(self.directory) / "auth.sqlite3"
        self.enterContext(patch.object(schema, "_SCHEMA_READY", False))
        self.enterContext(patch.object(schema, "get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch.object(dependencies, "_identity_validation_cache", {}))
        self.enterContext(patch.object(dependencies, "invalidate_session_for_user", return_value=True))
        for module in (auth_routes, mp_deps, dependencies):
            self.enterContext(patch.object(module, "get_db_connection", self.connection))
        self.enterContext(patch.object(auth_routes, "build_login_tip_payload_for_student", return_value=None))
        self.enterContext(patch.object(auth_routes, "build_login_tip_payload_for_teacher", return_value=None))
        self.audit = self.enterContext(patch.object(auth_routes, "record_student_login"))
        self.exchange = self.enterContext(patch.object(service, "exchange_code_for_openid"))
        self.exchange.return_value = {"openid": "phone-a", "unionid": ""}
        with self.connection() as conn:
            _seed_accounts(conn)
            service.ensure_wechat_mp_runtime(conn)
            conn.execute(
                "UPDATE teachers SET hashed_password = ? WHERE id = 9",
                (dependencies.get_password_hash("test-password"),),
            )
            conn.commit()
        app = FastAPI()
        app.include_router(auth_routes.router, prefix="/api/mp")

        @app.get("/api/shared")
        def shared(user=Depends(dependencies.get_current_user)):
            return user

        self.client = self.enterContext(TestClient(app, raise_server_exceptions=False))

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def bind_student(self, ticket=None, **fields):
        return self.client.post("/api/mp/auth/bind/student", json={
            "bind_ticket": ticket or service.build_bind_ticket("phone-a"),
            "name": "测试学生", "student_id_number": "20260001", **fields,
        })

    def bind_teacher(self, ticket=None, **fields):
        return self.client.post("/api/mp/auth/bind/teacher", json={
            "bind_ticket": ticket or service.build_bind_ticket("phone-a"),
            "email": "t@example.com", "password": "test-password", **fields,
        })

    def token(self, role="student", openid="phone-a"):
        with self.connection() as conn:
            pk = 11 if role == "student" else 9
            service.create_binding(conn, user_role=role, user_pk=pk, openid=openid)
            token = service.issue_mp_session(conn, user_role=role, user_pk=pk, openid=openid)
            conn.commit()
            return token

    def assert_unauthorized_everywhere(self, token):
        for route in ("/api/mp/auth/me", "/api/shared"):
            response = self.client.get(route, headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(response.status_code, 401, response.text)

    def test_student_bind_me_and_silent_login(self):
        login = self.client.post("/api/mp/auth/login", json={"code": "test"}).json()["data"]
        self.assertEqual(login["status"], "need_bind")
        bound = self.bind_student(login["bind_ticket"])
        self.assertEqual(bound.status_code, 200, bound.text)
        token = bound.json()["data"]["token"]
        for route in ("/api/mp/auth/me", "/api/shared"):
            self.assertEqual(self.client.get(route, headers={"Authorization": f"Bearer {token}"}).status_code, 200)
        self.assertEqual(self.client.post("/api/mp/auth/login", json={"code": "test"}).json()["data"]["status"], "success")
        self.audit.assert_called_once()

    def test_inactive_and_deleted_identities_rejected_on_mp_shared_and_silent_login(self):
        scenarios = (
            ("student", "UPDATE students SET enrollment_status = 'suspended' WHERE id = 11"),
            ("student", "DELETE FROM students WHERE id = 11"),
            ("teacher", "UPDATE teachers SET is_active = 0 WHERE id = 9"),
            ("teacher", "DELETE FROM teachers WHERE id = 9"),
        )
        for role, sql in scenarios:
            with self.subTest(sql=sql):
                # Restore accounts after the preceding scenario, without resetting sessions.
                with self.connection() as conn:
                    conn.execute("INSERT INTO students (id, class_id, name, student_id_number) VALUES (11, 1, '测试学生', '20260001') ON CONFLICT (id) DO UPDATE SET enrollment_status = 'active'")
                    conn.execute("INSERT INTO teachers (id, name, email) VALUES (9, '测试教师', 't@example.com') ON CONFLICT (id) DO UPDATE SET is_active = 1")
                token = self.token(role)
                # Prime the shared 10-second identity cache before live status changes.
                self.assertEqual(self.client.get("/api/shared", headers={"Authorization": f"Bearer {token}"}).status_code, 200)
                with self.connection() as conn:
                    conn.execute(sql)
                self.assert_unauthorized_everywhere(token)
                login = self.client.post("/api/mp/auth/login", json={"code": "test"})
                self.assertEqual(login.json()["data"]["status"], "need_bind")
                with self.connection() as conn:
                    row = conn.execute("SELECT status FROM wechat_bindings WHERE openid = 'phone-a'").fetchone()
                    self.assertEqual(row["status"], "revoked")

    def test_logout_unbinds_current_wechat_but_preserves_other_identity(self):
        first = self.token(openid="phone-a")
        second = self.token(openid="phone-b")
        response = self.client.post("/api/mp/auth/logout", headers={"Authorization": f"Bearer {first}"})
        self.assertTrue(response.json()["data"]["revoked"])
        self.assert_unauthorized_everywhere(first)
        self.assertEqual(self.client.get("/api/mp/auth/me", headers={"Authorization": f"Bearer {second}"}).status_code, 200)
        self.assertEqual(self.client.post("/api/mp/auth/login", json={"code": "test"}).json()["data"]["status"], "need_bind")
        self.assertEqual(self.client.post("/api/mp/auth/logout", headers={"Authorization": f"Bearer {first}"}).status_code, 401)

    def test_expired_logout_requires_fresh_wechat_identity_before_unbinding(self):
        token = self.token()
        with self.connection() as conn:
            conn.execute("UPDATE mp_sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
        response = self.client.post("/api/mp/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("X-LanShare-Error-Code"), "mp_logout_session_expired")
        with self.connection() as conn:
            self.assertIsNotNone(service.find_active_binding(conn, "phone-a"))
        login = self.client.post("/api/mp/auth/login", json={"code": "test"}).json()["data"]
        self.assertEqual(login["status"], "success")
        completed = self.client.post("/api/mp/auth/logout", headers={"Authorization": f"Bearer {login['token']}"})
        self.assertEqual(completed.status_code, 200)
        self.assertTrue(completed.json()["data"]["revoked"])
        self.assertEqual(self.client.post("/api/mp/auth/login", json={"code": "test"}).json()["data"]["status"], "need_bind")

    def test_missing_or_unknown_logout_tokens_request_identity_recovery(self):
        for headers in ({}, {"Authorization": "Bearer unknown"}):
            response = self.client.post("/api/mp/auth/logout", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers.get("X-LanShare-Error-Code"), "mp_logout_session_expired")

    def test_logout_does_not_report_unbinding_success_when_the_binding_changed(self):
        token = self.token()
        revoke = service.revoke_openid_binding
        new_tokens = []

        def rebind_before_revoke(conn, **identity):
            service.create_binding(conn, user_role="teacher", user_pk=9, openid="phone-a")
            new_tokens.append(service.issue_mp_session(conn, user_role="teacher", user_pk=9, openid="phone-a"))
            return revoke(conn, **identity)

        with patch.object(service, "revoke_openid_binding", side_effect=rebind_before_revoke):
            response = self.client.post("/api/mp/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("X-LanShare-Error-Code"), "mp_logout_session_expired")
        with self.connection() as conn:
            self.assertEqual(service.find_active_binding(conn, "phone-a")["user_role"], "teacher")
            self.assertIsNotNone(service.resolve_mp_session(conn, new_tokens[0]))

    def test_teacher_bind_consumes_ticket_and_old_student_token_cannot_logout_new_account(self):
        first = self.token()
        ticket = service.build_bind_ticket("phone-a")
        response = self.bind_teacher(ticket)
        self.assertEqual(response.status_code, 200, response.text)
        second = response.json()["data"]["token"]
        self.assert_unauthorized_everywhere(first)
        replay = self.bind_student(ticket)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.headers.get("X-LanShare-Error-Code"), "mp_bind_ticket_invalid")
        self.client.post("/api/mp/auth/logout", headers={"Authorization": f"Bearer {first}"})
        self.assertEqual(self.client.get("/api/mp/auth/me", headers={"Authorization": f"Bearer {second}"}).status_code, 200)

    def test_invalid_and_expired_tickets_have_a_machine_readable_recovery_code(self):
        from jose import jwt
        payload = service.decode_bind_ticket(service.build_bind_ticket("phone-a"))
        payload["exp"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp()
        expired = jwt.encode(payload, service.SECRET_KEY, algorithm=service.ALGORITHM)
        for bind in (self.bind_student, self.bind_teacher):
            for ticket in ("invalid-ticket", expired):
                response = bind(ticket)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers.get("X-LanShare-Error-Code"), "mp_bind_ticket_invalid")
                self.assertIn("绑定凭证已失效", response.json()["detail"])
        credentials = self.bind_teacher(password="wrong-password")
        self.assertEqual(credentials.status_code, 400)
        self.assertNotIn("X-LanShare-Error-Code", credentials.headers)

    def test_bad_credentials_count_against_shared_limit_without_consuming_ticket(self):
        ticket = service.build_bind_ticket("phone-a")
        for _ in range(service.BIND_RATE_MAX_ATTEMPTS):
            self.assertEqual(self.bind_student(ticket, name="错误姓名").status_code, 400)
        self.assertEqual(self.bind_teacher().status_code, 429)
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mp_consumed_bind_tickets").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wechat_bindings").fetchone()[0], 0)

    def test_inactive_accounts_cannot_bind(self):
        with self.connection() as conn:
            conn.execute("UPDATE students SET enrollment_status = 'suspended'")
            conn.execute("UPDATE teachers SET is_active = 0")
        self.assertEqual(self.bind_student().status_code, 403)
        self.assertEqual(self.bind_teacher().status_code, 400)
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mp_consumed_bind_tickets").fetchone()[0], 0)

    def test_failed_binding_rolls_back_ticket_binding_and_session(self):
        ticket = service.build_bind_ticket("phone-a")
        self.audit.side_effect = RuntimeError("isolated audit failure")
        self.assertEqual(self.bind_student(ticket).status_code, 500)
        with self.connection() as conn:
            for table in ("mp_consumed_bind_tickets", "wechat_bindings", "mp_sessions"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.audit.side_effect = None
        self.assertEqual(self.bind_student(ticket).status_code, 200)

    def test_concurrent_ticket_consumption_has_one_winner(self):
        barrier = Barrier(2)
        ticket = service.build_bind_ticket("phone-a")

        def consume():
            with self.connection() as conn:
                barrier.wait(timeout=5)
                accepted = service.consume_bind_ticket(conn, ticket) is not None
                conn.commit()
                return accepted

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: consume(), range(2)))
        self.assertEqual(sorted(results), [False, True])

    def test_concurrent_rate_limits_are_shared_across_connections(self):
        barrier = Barrier(8)

        def attempt():
            with self.connection() as conn:
                barrier.wait(timeout=5)
                try:
                    service.check_bind_rate_limit(conn, "openid:shared", "ip:shared")
                except service.WechatMpError:
                    return False
                conn.commit()
                return True

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: attempt(), range(8)))
        self.assertEqual(sum(results), service.BIND_RATE_MAX_ATTEMPTS)


class WechatMpPostgresSchemaContractTests(unittest.TestCase):
    def test_runtime_tables_use_postgres_ddl_and_conflict_sql_is_adapter_compatible(self):
        statements = []

        class RecordingConnection:
            def execute(self, sql):
                statements.append(sqlite_sql_to_psycopg(sql))

        with patch.object(schema, "_SCHEMA_READY", False), patch.object(schema, "get_configured_db_engine", return_value="postgres"):
            schema.ensure_wechat_mp_schema(RecordingConnection())
        ddl = "\n".join(statements)
        self.assertIn("BIGINT GENERATED BY DEFAULT AS IDENTITY", ddl)
        self.assertIn("mp_consumed_bind_tickets", ddl)
        self.assertIn("mp_bind_rate_limits", ddl)
        self.assertNotIn("AUTOINCREMENT", ddl)
        sql = sqlite_sql_to_psycopg("INSERT INTO mp_consumed_bind_tickets (ticket_id, expires_at) VALUES (?, ?) ON CONFLICT (ticket_id) DO NOTHING")
        self.assertIn("VALUES (%s, %s) ON CONFLICT (ticket_id) DO NOTHING", sql)


if __name__ == "__main__":
    unittest.main()
