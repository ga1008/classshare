import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.routers import ai as ai_router
from classroom_app.services.ai_grading_service import (
    _mark_submission_grading_with_connection,
    _reset_submission_after_queue_failure_with_connection,
    expire_stale_ai_grading_submissions,
)


class AIGradingServiceTests(unittest.TestCase):
    def _submission_state_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                score REAL,
                grading_started_at TEXT,
                grading_attempt_fingerprint TEXT,
                resubmission_allowed INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO submissions
                (id, status, score, grading_started_at, grading_attempt_fingerprint, resubmission_allowed)
            VALUES
                (1, 'submitted', NULL, NULL, NULL, 0),
                (2, 'grading', NULL, '2026-01-01T00:00:00', 'token-live', 0),
                (3, 'graded', 88, NULL, NULL, 0);
            """
        )
        return conn

    def test_mark_submission_grading_preserves_sqlite_state_guards(self):
        conn = self._submission_state_conn()
        try:
            marked = _mark_submission_grading_with_connection(
                conn,
                submission_id=1,
                started_at="2026-01-01T00:10:00",
                attempt_token="token-1",
                allow_graded=False,
                engine="sqlite",
            )
            already_grading = _mark_submission_grading_with_connection(
                conn,
                submission_id=2,
                started_at="2026-01-01T00:11:00",
                attempt_token="token-2",
                allow_graded=True,
                engine="sqlite",
            )
            graded_blocked = _mark_submission_grading_with_connection(
                conn,
                submission_id=3,
                started_at="2026-01-01T00:12:00",
                attempt_token="token-3",
                allow_graded=False,
                engine="sqlite",
            )

            rows = conn.execute(
                "SELECT id, status, grading_attempt_fingerprint FROM submissions ORDER BY id"
            ).fetchall()

            self.assertTrue(marked)
            self.assertFalse(already_grading)
            self.assertFalse(graded_blocked)
            self.assertEqual("grading", rows[0]["status"])
            self.assertEqual("token-1", rows[0]["grading_attempt_fingerprint"])
            self.assertEqual("token-live", rows[1]["grading_attempt_fingerprint"])
            self.assertIsNone(rows[2]["grading_attempt_fingerprint"])
        finally:
            conn.close()

    def test_queue_failure_reset_requires_matching_attempt_token(self):
        conn = self._submission_state_conn()
        try:
            stale_reset = _reset_submission_after_queue_failure_with_connection(
                conn,
                submission_id=2,
                attempt_fingerprint="token-stale",
            )
            live_reset = _reset_submission_after_queue_failure_with_connection(
                conn,
                submission_id=2,
                attempt_fingerprint="token-live",
            )
            row = conn.execute(
                "SELECT status, grading_attempt_fingerprint FROM submissions WHERE id = 2"
            ).fetchone()

            self.assertIsNone(stale_reset)
            self.assertIsNotNone(live_reset)
            self.assertEqual("submitted", row["status"])
            self.assertIsNone(row["grading_attempt_fingerprint"])
        finally:
            conn.close()

    def test_expire_stale_grading_can_be_scoped_to_assignment_ids(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            old_time = (datetime.now() - timedelta(hours=8)).isoformat()
            try:
                conn.execute(
                    """
                    CREATE TABLE submissions (
                        id INTEGER PRIMARY KEY,
                        assignment_id TEXT,
                        student_pk_id INTEGER,
                        status TEXT,
                        grading_started_at TEXT,
                        submitted_at TEXT,
                        feedback_md TEXT,
                        grading_attempt_fingerprint TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE learning_stage_exam_attempts (
                        id INTEGER PRIMARY KEY,
                        assignment_id TEXT,
                        student_id INTEGER,
                        status TEXT,
                        class_offering_id INTEGER,
                        stage_key TEXT,
                        ai_error TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE learning_stage_status (
                        id INTEGER PRIMARY KEY,
                        class_offering_id INTEGER,
                        student_id INTEGER,
                        stage_key TEXT,
                        status TEXT,
                        last_calculated_at TEXT
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO submissions (
                        id, assignment_id, student_pk_id, status, grading_started_at,
                        submitted_at, feedback_md, grading_attempt_fingerprint
                    ) VALUES (?, ?, ?, 'grading', ?, ?, '', 'fp')
                    """,
                    [
                        (1, "assignment-1", 101, old_time, old_time),
                        (2, "assignment-2", 102, old_time, old_time),
                    ],
                )
                conn.commit()

                with patch(
                    "classroom_app.services.message_center_service.create_teacher_grading_issue_notification",
                    lambda *args, **kwargs: None,
                ):
                    expired_count = expire_stale_ai_grading_submissions(
                        conn,
                        stale_minutes=240,
                        assignment_ids=["assignment-1"],
                    )
                conn.commit()

                rows = {
                    row["assignment_id"]: row["status"]
                    for row in conn.execute(
                        "SELECT assignment_id, status FROM submissions ORDER BY assignment_id"
                    ).fetchall()
                }
            finally:
                conn.close()

            self.assertEqual(expired_count, 1)
            self.assertEqual(rows["assignment-1"], "grading_failed")
            self.assertEqual(rows["assignment-2"], "grading")
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass


class _FakeCursor:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakePostgresAIGradingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("UPDATE submissions"):
            return _FakeCursor(row={"id": 9}, rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")


class AIGradingPostgresSQLTests(unittest.TestCase):
    def test_postgres_mark_submission_grading_uses_returning_and_state_guards(self):
        conn = _FakePostgresAIGradingConnection()

        marked = _mark_submission_grading_with_connection(
            conn,
            submission_id=9,
            started_at="2026-01-01T00:10:00",
            attempt_token="token-pg",
            allow_graded=False,
            engine="postgres",
        )

        self.assertTrue(marked)
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("RETURNING id", sql)
        self.assertIn("AND status != 'grading'", sql)
        self.assertIn("AND status != 'graded'", sql)
        self.assertEqual(("2026-01-01T00:10:00", "token-pg", 9), params)


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return dict(self._payload)


class _FakeCallbackConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT * FROM submissions"):
            return _FakeCursor(
                row={
                    "id": 7,
                    "assignment_id": "assignment-1",
                    "status": "grading",
                    "score": None,
                    "answers_json": "{}",
                    "resubmission_allowed": 0,
                    "grading_attempt_fingerprint": "token-current",
                }
            )
        if normalized.startswith("UPDATE submissions"):
            return _FakeCursor(rowcount=0)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class AIGradingCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_update_requires_current_grading_token(self):
        conn = _FakeCallbackConnection()
        request = _FakeRequest(
            {
                "submission_id": 7,
                "submission_fingerprint": "token-current",
                "status": "grading_failed",
                "feedback_md": "failed",
            }
        )

        with patch.object(ai_router, "get_db_connection", return_value=conn):
            result = await ai_router.handle_ai_grading_callback(request)

        self.assertEqual({"status": "ignored_stale_grading_result"}, result)
        update_sql, update_params = next(call for call in conn.calls if call[0].startswith("UPDATE submissions"))
        self.assertIn("AND status = 'grading'", update_sql)
        self.assertIn("AND COALESCE(resubmission_allowed, 0) = 0", update_sql)
        self.assertIn("AND grading_attempt_fingerprint = ?", update_sql)
        self.assertEqual("token-current", update_params[-1])
        self.assertEqual(1, conn.commits)

    async def test_legacy_callback_without_token_cannot_update_tokened_attempt(self):
        conn = _FakeCallbackConnection()
        request = _FakeRequest(
            {
                "submission_id": 7,
                "status": "grading_failed",
                "feedback_md": "failed",
            }
        )

        with patch.object(ai_router, "get_db_connection", return_value=conn):
            result = await ai_router.handle_ai_grading_callback(request)

        self.assertEqual({"status": "ignored_stale_grading_result"}, result)
        update_sql, _ = next(call for call in conn.calls if call[0].startswith("UPDATE submissions"))
        self.assertIn("AND COALESCE(grading_attempt_fingerprint, '') = ''", update_sql)


if __name__ == "__main__":
    unittest.main()
