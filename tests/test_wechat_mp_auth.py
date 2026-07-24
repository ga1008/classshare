from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from classroom_app.services import wechat_mp_service as service


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _seed_accounts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT, created_by_teacher_id INTEGER)"
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


class WechatMpSchemaAndBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        import classroom_app.db.schema_wechat_mp as schema

        schema._SCHEMA_READY = False
        service.reset_bind_rate_limit()
        self.conn = _fresh_conn()
        _seed_accounts(self.conn)
        service.ensure_wechat_mp_runtime(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        service.reset_bind_rate_limit()

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


class WechatMpSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        import classroom_app.db.schema_wechat_mp as schema

        schema._SCHEMA_READY = False
        self.conn = _fresh_conn()
        _seed_accounts(self.conn)
        service.ensure_wechat_mp_runtime(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

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


class WechatMpTicketAndRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        service.reset_bind_rate_limit()

    def tearDown(self) -> None:
        service.reset_bind_rate_limit()

    def test_bind_ticket_roundtrip(self) -> None:
        ticket = service.build_bind_ticket("openid-x", "union-x")
        payload = service.decode_bind_ticket(ticket)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["openid"], "openid-x")
        self.assertEqual(payload["unionid"], "union-x")
        self.assertIsNone(service.decode_bind_ticket("not-a-ticket"))

    def test_bind_rate_limit_trips_after_max_attempts(self) -> None:
        for _ in range(service.BIND_RATE_MAX_ATTEMPTS):
            service.check_bind_rate_limit("openid:limit-me")
        with self.assertRaises(service.WechatMpError):
            service.check_bind_rate_limit("openid:limit-me")
        # Other keys are unaffected.
        service.check_bind_rate_limit("openid:someone-else")


if __name__ == "__main__":
    unittest.main()
