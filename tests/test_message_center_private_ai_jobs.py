import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import message_center_service as service


class _FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []


class _FakePostgresConnection:
    def __init__(self, *, row=None):
        self.calls = []
        self.commits = 0
        self.row = row or {
            "id": 7,
            "conversation_key": "student:1|assistant:10|scope:10",
            "class_offering_id": 10,
            "request_message_id": 99,
            "requester_identity": "student:1",
            "requester_role": "student",
            "requester_user_pk": 1,
            "status": service.AI_REPLY_JOB_STATUS_PENDING,
            "error_message": "",
            "reply_message_id": None,
            "attempt_count": 0,
            "created_at": "2026-01-01T00:00:00",
            "started_at": "",
            "finished_at": "",
            "updated_at": "2026-01-01T00:00:00",
        }

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO private_message_ai_jobs"):
            return _FakeCursor(self.row, rowcount=1)
        if normalized.startswith("SELECT * FROM private_message_ai_jobs WHERE id"):
            return _FakeCursor(self.row, rowcount=1)
        if normalized.startswith("UPDATE private_message_ai_jobs"):
            claimed = dict(self.row)
            claimed["status"] = service.AI_REPLY_JOB_STATUS_RUNNING
            claimed["attempt_count"] = int(claimed.get("attempt_count") or 0) + 1
            return _FakeCursor(claimed, rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1


class _FakePostgresMessageInsertConnection:
    def __init__(self):
        self.calls = []
        self.notification_id = {"id": 101}
        self.message_id = {"id": 202}
        self.attachment_id = {"id": 303}
        self.attachment_row = {
            "id": 303,
            "message_id": 202,
            "conversation_key": "student:1|teacher:2",
            "class_offering_id": 10,
            "uploaded_by_identity": "student:1",
            "uploaded_by_role": "student",
            "file_hash": "hash-1",
            "original_filename": "note.txt",
            "mime_type": "text/plain",
            "file_size": 42,
            "attachment_kind": "file",
            "image_width": None,
            "image_height": None,
            "thumbnail_file_hash": None,
            "thumbnail_mime_type": None,
            "thumbnail_file_size": 0,
            "thumbnail_width": None,
            "thumbnail_height": None,
            "preview_file_hash": None,
            "preview_mime_type": None,
            "preview_file_size": 0,
            "preview_width": None,
            "preview_height": None,
            "created_at": "2026-01-01T00:00:00",
        }

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO message_center_notifications"):
            return _FakeCursor(self.notification_id, rowcount=1)
        if normalized.startswith("INSERT INTO private_messages"):
            return _FakeCursor(self.message_id, rowcount=1)
        if "information_schema.columns" in normalized:
            return _FakeListCursor(
                [{"column_name": column} for column in service.PRIVATE_MESSAGE_ATTACHMENT_REQUIRED_COLUMNS]
            )
        if normalized.startswith("INSERT INTO private_message_attachments"):
            return _FakeCursor(self.attachment_id, rowcount=1)
        if normalized.startswith("SELECT * FROM private_message_attachments"):
            return _FakeListCursor([self.attachment_row])
        raise AssertionError(f"Unexpected SQL: {normalized}")


class _FakeListCursor:
    def __init__(self, rows):
        self._rows = rows
        self.rowcount = len(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class PrivateMessageAIJobTests(unittest.TestCase):
    def _sqlite_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE private_message_ai_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_key TEXT NOT NULL,
                class_offering_id INTEGER NOT NULL,
                request_message_id INTEGER NOT NULL UNIQUE,
                requester_identity TEXT NOT NULL,
                requester_role TEXT NOT NULL,
                requester_user_pk INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT DEFAULT '',
                reply_message_id INTEGER,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        return conn

    def test_sqlite_create_claim_and_finish_private_ai_reply_job(self):
        conn = self._sqlite_conn()
        try:
            with patch.object(service, "get_configured_db_engine", return_value="sqlite"):
                created = service.create_private_ai_reply_job(
                    conn,
                    {"id": 1, "role": "student"},
                    conversation_key="student:1|assistant:10|scope:10",
                    class_offering_id=10,
                    request_message_id=99,
                )
            claimed = service._claim_private_ai_reply_job(
                conn,
                created["id"],
                engine="sqlite",
            )
            finished = service._finish_private_ai_reply_job(
                conn,
                created["id"],
                status=service.AI_REPLY_JOB_STATUS_COMPLETED,
                reply_message_id=55,
            )
            stale_finish = service._finish_private_ai_reply_job(
                conn,
                created["id"],
                status=service.AI_REPLY_JOB_STATUS_FAILED,
            )
            row = conn.execute(
                "SELECT status, attempt_count, reply_message_id FROM private_message_ai_jobs WHERE id = ?",
                (created["id"],),
            ).fetchone()

            self.assertEqual(service.AI_REPLY_JOB_STATUS_PENDING, created["status"])
            self.assertEqual(service.AI_REPLY_JOB_STATUS_RUNNING, claimed["status"])
            self.assertTrue(finished)
            self.assertFalse(stale_finish)
            self.assertEqual(service.AI_REPLY_JOB_STATUS_COMPLETED, row["status"])
            self.assertEqual(1, row["attempt_count"])
            self.assertEqual(55, row["reply_message_id"])
        finally:
            conn.close()

    def test_postgres_create_private_ai_reply_job_uses_returning(self):
        conn = _FakePostgresConnection()

        with patch.object(service, "get_configured_db_engine", return_value="postgres"):
            created = service.create_private_ai_reply_job(
                conn,
                {"id": 1, "role": "student"},
                conversation_key="student:1|assistant:10|scope:10",
                class_offering_id=10,
                request_message_id=99,
            )

        self.assertEqual(7, created["id"])
        self.assertEqual(2, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("RETURNING id", sql)
        self.assertEqual(
            (
                "student:1|assistant:10|scope:10",
                10,
                99,
                "student:1",
                "student",
                1,
                service.AI_REPLY_JOB_STATUS_PENDING,
                params[7],
                params[8],
            ),
            params,
        )
        self.assertTrue(conn.calls[1][0].startswith("SELECT * FROM private_message_ai_jobs WHERE id"))

    def test_postgres_insert_notification_uses_returning_and_queues_email_with_id(self):
        conn = _FakePostgresMessageInsertConnection()
        payload = {
            "recipient_identity": "student:1",
            "recipient_role": "student",
            "recipient_user_pk": 1,
            "category": service.MESSAGE_CATEGORY_ASSIGNMENT,
            "severity": "important",
            "actor_identity": "teacher:2",
            "actor_role": "teacher",
            "actor_user_pk": 2,
            "actor_display_name": "Teacher",
            "title": "Assignment",
            "body_preview": "Body",
            "link_url": "/assignments/1",
            "class_offering_id": 10,
            "ref_type": "assignment",
            "ref_id": "1",
            "metadata_json": "{}",
            "created_at": "2026-01-01T00:00:00",
        }

        with patch.object(service, "get_configured_db_engine", return_value="postgres"), patch.object(
            service, "queue_notification_email_if_applicable", return_value=False
        ) as queue_email:
            notification_id = service._insert_notification(conn, payload)

        self.assertEqual(101, notification_id)
        sql, _ = conn.calls[0]
        self.assertIn("RETURNING id", sql)
        queue_email.assert_called_once_with(conn, notification_id=101, payload=payload)

    def test_postgres_insert_private_message_uses_returning(self):
        conn = _FakePostgresMessageInsertConnection()

        with patch.object(service, "get_configured_db_engine", return_value="postgres"):
            row = service._insert_private_message(
                conn,
                conversation_key="student:1|teacher:2",
                class_offering_id=10,
                sender_identity="student:1",
                sender_role="student",
                sender_user_pk=1,
                sender_display_name="Student",
                recipient_identity="teacher:2",
                recipient_role="teacher",
                recipient_user_pk=2,
                recipient_display_name="Teacher",
                content="hello",
                created_at="2026-01-01T00:00:00",
            )

        self.assertEqual(202, row["id"])
        sql, params = conn.calls[0]
        self.assertIn("RETURNING id", sql)
        self.assertEqual("student:1|teacher:2", params[0])

    def test_postgres_insert_private_message_attachments_uses_schema_validation_and_returning(self):
        conn = _FakePostgresMessageInsertConnection()
        message_row = {
            "id": 202,
            "conversation_key": "student:1|teacher:2",
            "class_offering_id": 10,
            "sender_identity": "student:1",
            "sender_role": "student",
        }

        with patch.object(service, "get_configured_db_engine", return_value="postgres"):
            attachments = service._insert_private_message_attachments(
                conn,
                message_row,
                [
                    {
                        "file_hash": "hash-1",
                        "original_filename": "note.txt",
                        "mime_type": "text/plain",
                        "file_size": 42,
                    }
                ],
            )

        sqls = [call[0] for call in conn.calls]
        self.assertIn("FROM information_schema.columns", sqls[0])
        self.assertIn("RETURNING id", sqls[1])
        self.assertEqual([303], [item["id"] for item in attachments])

    def test_postgres_claim_private_ai_reply_job_uses_skip_locked_returning(self):
        conn = _FakePostgresConnection()

        claimed = service._claim_private_ai_reply_job(
            conn,
            7,
            engine="postgres",
        )

        self.assertEqual(service.AI_REPLY_JOB_STATUS_RUNNING, claimed["status"])
        self.assertEqual(1, conn.commits)
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING *", sql)
        self.assertEqual(service.AI_REPLY_JOB_STATUS_RUNNING, params[0])
        self.assertEqual(7, params[3])
        self.assertEqual(service.AI_REPLY_JOB_STATUS_PENDING, params[4])

    def test_postgres_batch_schedule_claim_uses_skip_locked_returning(self):
        conn = _FakePostgresConnection()

        claimed = service._claim_pending_private_ai_reply_jobs_for_schedule(
            conn,
            limit=5,
            engine="postgres",
        )

        self.assertEqual([7], [item["id"] for item in claimed])
        self.assertEqual(1, conn.commits)
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING *", sql)
        self.assertEqual(service.AI_REPLY_JOB_STATUS_RUNNING, params[0])
        self.assertEqual(service.AI_REPLY_JOB_STATUS_PENDING, params[3])
        self.assertEqual(5, params[4])

    def test_claim_private_ai_reply_job_unknown_engine_fails_fast(self):
        conn = self._sqlite_conn()
        try:
            with self.assertRaises(ValueError):
                service._claim_private_ai_reply_job(conn, 1, engine="mysql")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
