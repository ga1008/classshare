import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.routers import ai as ai_router
from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.services.ai_grading_service import (
    _mark_submission_grading_with_connection,
    _reset_submission_after_queue_failure_with_connection,
    expire_stale_ai_grading_submissions,
    submit_submission_for_ai_grading,
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


class _DurableEnqueueConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("UPDATE submissions SET grading_revision_hash"):
            return _FakeCursor(rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class AIDurableGradingEnqueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_enqueue_and_submission_marker_commit_together(self):
        from classroom_app.services import ai_grading_service as service

        conn = _DurableEnqueueConnection()
        submission = {
            "id": 7,
            "assignment_id": "a1",
            "student_pk_id": 4,
            "status": "submitted",
            "score": None,
            "answers_json": '{"answers": [{"question_id": "q1", "answer": "A"}]}',
            "submitted_at": "2026-07-12T12:00:00",
            "requirements_md": "",
            "rubric_md": "rubric",
            "exam_paper_id": None,
            "exam_questions_json": None,
            "allowed_file_types_json": "[]",
            "class_offering_id": 3,
            "offering_teacher_id": 9,
            "created_by_teacher_id": 9,
            "resubmission_allowed": 0,
            "late_policy_snapshot_json": "{}",
        }
        with (
            patch.object(service, "AI_DURABLE_JOBS_ENABLED", True),
            patch.object(service, "get_db_connection", return_value=conn),
            patch.object(service, "_load_submission_for_grading", return_value=submission),
            patch.object(service, "_load_submission_files_for_grading", return_value=[]),
            patch.object(service, "_prepare_grading_inputs", return_value=([], False, True)),
            patch.object(service, "_resolve_grading_rubric", return_value="rubric"),
            patch.object(service, "_build_hidden_student_profile_context", return_value=""),
            patch.object(service, "_mark_submission_grading_with_connection", return_value=True),
            patch.object(
                service,
                "create_ai_job",
                return_value=({"id": 41, "status": "queued"}, True),
            ) as create_job,
            patch.object(service, "ai_gateway_post") as legacy_gateway,
        ):
            result = await submit_submission_for_ai_grading(7, teacher_id=9)

        self.assertEqual({"status": "queued", "submission_id": 7, "job_id": 41, "durable": True}, result)
        self.assertEqual(1, conn.commits)
        self.assertEqual(1, create_job.call_count)
        legacy_gateway.assert_not_called()
        update_sql, update_params = conn.calls[0]
        self.assertIn("grading_revision_hash", update_sql)
        self.assertEqual(41, update_params[1])


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
    def test_grade_revision_activation_is_append_only_and_switches_active_pointer(self):
        reset_ai_job_schema_guard_for_tests()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE submissions (id INTEGER PRIMARY KEY, score REAL, feedback_md TEXT)"
        )
        conn.execute("INSERT INTO submissions (id, score, feedback_md) VALUES (7, 80, 'old')")
        ensure_ai_job_schema(conn, engine="sqlite")
        submission = dict(conn.execute("SELECT * FROM submissions WHERE id = 7").fetchone())
        try:
            with patch(
                "classroom_app.services.grading_revision_service.get_configured_db_engine",
                return_value="sqlite",
            ):
                first_id = ai_router._activate_submission_grade_revision(
                    conn,
                    submission=submission,
                    data={"grading_revision_hash": "revision-1", "quality_audit": {"ok": True}},
                    score=88,
                    feedback_md="first",
                )
                second_id = ai_router._activate_submission_grade_revision(
                    conn,
                    submission=submission,
                    data={"grading_revision_hash": "revision-2", "quality_audit": {"ok": True}},
                    score=91,
                    feedback_md="second",
                )
            rows = [dict(row) for row in conn.execute(
                "SELECT id, status, score FROM submission_grade_revisions ORDER BY id"
            )]
            active_pointer = conn.execute(
                "SELECT active_grade_revision_id FROM submissions WHERE id = 7"
            ).fetchone()["active_grade_revision_id"]
        finally:
            conn.close()
            reset_ai_job_schema_guard_for_tests()

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(["superseded", "active"], [row["status"] for row in rows])
        self.assertEqual([88, 91], [row["score"] for row in rows])
        self.assertEqual(second_id, active_pointer)

    def test_failed_regrade_preserves_previous_score_and_feedback(self):
        status, score, feedback, preserved = ai_router._preserve_previous_grade_on_failed_regrade(
            {"score": 86, "feedback_md": "原有效评语"},
            incoming_status="grading_failed",
            incoming_score=None,
            incoming_feedback="provider unavailable",
        )

        self.assertEqual("graded", status)
        self.assertEqual(86, score)
        self.assertEqual("原有效评语", feedback)
        self.assertTrue(preserved)

    def test_first_grading_failure_remains_failed(self):
        status, score, feedback, preserved = ai_router._preserve_previous_grade_on_failed_regrade(
            {"score": None, "feedback_md": None},
            incoming_status="grading_failed",
            incoming_score=None,
            incoming_feedback="provider unavailable",
        )

        self.assertEqual("grading_failed", status)
        self.assertIsNone(score)
        self.assertEqual("provider unavailable", feedback)
        self.assertFalse(preserved)

    async def test_review_required_callback_creates_teacher_notice(self):
        conn = object()
        with patch.object(ai_router, "create_teacher_grading_issue_notification") as notify:
            created = ai_router._notify_teacher_if_ai_review_required(
                conn,
                7,
                {
                    "review_required": True,
                    "review_reason_codes": ["model_requested_review", "low_confidence"],
                },
            )
        self.assertTrue(created)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["ref_suffix"], "grading_review_required")
        self.assertIn("low_confidence", notify.call_args.kwargs["issue_detail"])

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
