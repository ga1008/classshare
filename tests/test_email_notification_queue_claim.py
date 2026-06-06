import sqlite3
import unittest
from unittest.mock import patch

import classroom_app.services.email_notification_service as email_service
from classroom_app.services.email_notification_service import (
    _claim_due_jobs_with_connection,
    _insert_email_outbox_job_if_absent,
    email_worker_health_snapshot,
)


class _FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakePostgresConnection:
    def __init__(self):
        self.calls = []
        self.committed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _FakeCursor(
            rows=[
                {
                    "id": 7,
                    "status": "sending",
                    "recipient_email": "student@example.test",
                }
            ],
            rowcount=1,
        )

    def commit(self):
        self.committed = True


class _FakePostgresOutboxInsertConnection:
    def __init__(self, *, inserted=True):
        self.calls = []
        self.inserted = inserted

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO email_outbox"):
            return _FakeCursor(rows=[{"id": 17}] if self.inserted else [])
        if normalized.startswith("SELECT id FROM email_outbox"):
            return _FakeCursor(rows=[{"id": 11}])
        raise AssertionError(f"Unexpected SQL: {normalized}")


class _FakePostgresHealthConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("SELECT * FROM email_worker_heartbeats"):
            return _FakeCursor(
                rows=[
                    {
                        "worker_id": "mailer-compose",
                        "status": "running",
                        "queue_depth": 0,
                        "last_error": "",
                        "updated_at": email_service._now_iso(),
                    }
                ]
            )
        if normalized.startswith("SELECT COUNT(*) AS row_count FROM email_outbox"):
            return _FakeCursor(rows=[{"row_count": 2}])
        raise AssertionError(f"Unexpected SQL: {normalized}")


class EmailNotificationQueueClaimTests(unittest.TestCase):
    def _sqlite_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE email_outbox (
                id INTEGER PRIMARY KEY,
                status TEXT,
                next_attempt_at TEXT,
                locked_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                recipient_email TEXT
            );
            INSERT INTO email_outbox
                (id, status, next_attempt_at, locked_at, created_at, updated_at, recipient_email)
            VALUES
                (1, 'queued', NULL, NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'a@example.test'),
                (2, 'queued', '2099-01-01T00:00:00', NULL, '2026-01-01T00:01:00', '2026-01-01T00:01:00', 'b@example.test');
            """
        )
        return conn

    def _sqlite_insert_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE email_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id INTEGER,
                teacher_id INTEGER,
                notification_id INTEGER,
                dedupe_key TEXT UNIQUE,
                recipient_identity TEXT,
                recipient_role TEXT,
                recipient_user_pk INTEGER,
                recipient_email TEXT,
                subject TEXT,
                body_text TEXT,
                body_html TEXT,
                category TEXT,
                severity TEXT,
                status TEXT,
                next_attempt_at TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        return conn

    def test_sqlite_claim_due_jobs_preserves_existing_claim_behavior(self):
        conn = self._sqlite_conn()
        try:
            claimed = _claim_due_jobs_with_connection(
                conn,
                limit=10,
                now="2026-01-01T00:10:00",
                stale_cutoff="2026-01-01T00:05:00",
                engine="sqlite",
            )

            self.assertEqual([1], [item["id"] for item in claimed])
            rows = conn.execute("SELECT id, status, locked_at FROM email_outbox ORDER BY id").fetchall()
            self.assertEqual("sending", rows[0]["status"])
            self.assertEqual("2026-01-01T00:10:00", rows[0]["locked_at"])
            self.assertEqual("queued", rows[1]["status"])
        finally:
            conn.close()

    def test_postgres_claim_due_jobs_uses_skip_locked_returning(self):
        conn = _FakePostgresConnection()

        claimed = _claim_due_jobs_with_connection(
            conn,
            limit=3,
            now="2026-01-01T00:10:00",
            stale_cutoff="2026-01-01T00:05:00",
            engine="postgres",
        )

        self.assertEqual([{"id": 7, "status": "sending", "recipient_email": "student@example.test"}], claimed)
        self.assertTrue(conn.committed)
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING *", sql)
        self.assertEqual(
            ("2026-01-01T00:10:00", "2026-01-01T00:10:00", "2026-01-01T00:10:00", "2026-01-01T00:05:00", 3),
            params,
        )

    def test_sqlite_insert_email_outbox_job_preserves_idempotent_behavior(self):
        conn = self._sqlite_insert_conn()
        try:
            first_id = _insert_email_outbox_job_if_absent(
                conn,
                config_id=1,
                teacher_id=2,
                notification_id=3,
                dedupe_key="notification:3:student@example.test",
                recipient_identity="student:4",
                recipient_role="student",
                recipient_user_pk=4,
                recipient_email="student@example.test",
                subject="Subject",
                body_text="Text",
                body_html="<p>Text</p>",
                category="assignment",
                severity="important",
                now="2026-01-01T00:00:00",
                engine="sqlite",
            )
            second_id = _insert_email_outbox_job_if_absent(
                conn,
                config_id=1,
                teacher_id=2,
                notification_id=3,
                dedupe_key="notification:3:student@example.test",
                recipient_identity="student:4",
                recipient_role="student",
                recipient_user_pk=4,
                recipient_email="student@example.test",
                subject="Subject",
                body_text="Text",
                body_html="<p>Text</p>",
                category="assignment",
                severity="important",
                now="2026-01-01T00:00:00",
                engine="sqlite",
            )
            count = conn.execute("SELECT COUNT(*) AS row_count FROM email_outbox").fetchone()["row_count"]

            self.assertEqual(1, first_id)
            self.assertEqual(first_id, second_id)
            self.assertEqual(1, count)
        finally:
            conn.close()

    def test_postgres_insert_email_outbox_job_uses_on_conflict_returning(self):
        conn = _FakePostgresOutboxInsertConnection(inserted=True)

        job_id = _insert_email_outbox_job_if_absent(
            conn,
            config_id=1,
            teacher_id=2,
            notification_id=3,
            dedupe_key="notification:3:student@example.test",
            recipient_identity="student:4",
            recipient_role="student",
            recipient_user_pk=4,
            recipient_email="student@example.test",
            subject="Subject",
            body_text="Text",
            body_html="<p>Text</p>",
            category="assignment",
            severity="important",
            now="2026-01-01T00:00:00",
            engine="postgres",
        )

        self.assertEqual(17, job_id)
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("ON CONFLICT (dedupe_key) DO NOTHING", sql)
        self.assertIn("RETURNING id", sql)
        self.assertEqual("notification:3:student@example.test", params[3])

    def test_postgres_insert_email_outbox_job_returns_existing_conflict_row(self):
        conn = _FakePostgresOutboxInsertConnection(inserted=False)

        job_id = _insert_email_outbox_job_if_absent(
            conn,
            config_id=1,
            teacher_id=2,
            notification_id=3,
            dedupe_key="notification:3:student@example.test",
            recipient_identity="student:4",
            recipient_role="student",
            recipient_user_pk=4,
            recipient_email="student@example.test",
            subject="Subject",
            body_text="Text",
            body_html="<p>Text</p>",
            category="assignment",
            severity="important",
            now="2026-01-01T00:00:00",
            engine="postgres",
        )

        self.assertEqual(11, job_id)
        self.assertEqual(2, len(conn.calls))
        self.assertTrue(conn.calls[1][0].startswith("SELECT id FROM email_outbox"))

    def test_email_worker_health_snapshot_accepts_postgres_dict_count_row(self):
        with patch.object(email_service, "get_db_connection", return_value=_FakePostgresHealthConnection()):
            snapshot = email_worker_health_snapshot()

        self.assertTrue(snapshot["ok"])
        self.assertEqual(2, snapshot["queue_depth"])
        self.assertEqual("running", snapshot["status"])

    def test_unsupported_claim_engine_fails_explicitly(self):
        conn = self._sqlite_conn()
        try:
            with self.assertRaises(ValueError):
                _claim_due_jobs_with_connection(
                    conn,
                    limit=1,
                    now="2026-01-01T00:10:00",
                    stale_cutoff="2026-01-01T00:05:00",
                    engine="mysql",
                )
        finally:
            conn.close()

    def test_unsupported_insert_engine_fails_explicitly(self):
        conn = self._sqlite_insert_conn()
        try:
            with self.assertRaises(ValueError):
                _insert_email_outbox_job_if_absent(
                    conn,
                    config_id=1,
                    teacher_id=2,
                    notification_id=3,
                    dedupe_key="notification:3:student@example.test",
                    recipient_identity="student:4",
                    recipient_role="student",
                    recipient_user_pk=4,
                    recipient_email="student@example.test",
                    subject="Subject",
                    body_text="Text",
                    body_html="<p>Text</p>",
                    category="assignment",
                    severity="important",
                    now="2026-01-01T00:00:00",
                    engine="mysql",
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
