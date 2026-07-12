from __future__ import annotations

import sqlite3
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.db.connection import LanShareSQLiteConnection
from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.services import ai_durable_job_service as jobs


class AIDurableJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.db"
        reset_ai_job_schema_guard_for_tests()
        with self._connect() as conn:
            conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, score REAL, feedback_md TEXT)")
            conn.execute(
                """
                CREATE TABLE exam_papers (
                    id TEXT PRIMARY KEY,
                    teacher_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    questions_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'generating',
                    ai_gen_task_id TEXT,
                    ai_gen_status TEXT,
                    ai_gen_error TEXT,
                    updated_at TEXT
                )
                """
            )
            ensure_ai_job_schema(conn, engine="sqlite")
            conn.commit()
        self.patches = [
            patch.object(jobs, "get_db_connection", side_effect=self._connect),
            patch.object(jobs, "get_configured_db_engine", return_value="sqlite"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()
        reset_ai_job_schema_guard_for_tests()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, factory=LanShareSQLiteConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _create(self, key: str = "grading:7:revision-a") -> tuple[dict, bool]:
        with self._connect() as conn:
            row, created = jobs.create_ai_job(
                conn,
                task_type="ai_grading",
                dedupe_key=key,
                payload={"submission_id": 7, "submission_fingerprint": "revision-a"},
                scope_type="class_offering",
                scope_id="3",
                source_ref="submission:7",
            )
            conn.commit()
            return row, created

    def test_create_is_idempotent_and_schema_adds_submission_revision_columns(self):
        first, first_created = self._create()
        second, second_created = self._create()

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])
        with self._connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(submissions)")}
        self.assertIn("grading_revision_hash", columns)
        self.assertIn("active_grade_revision_id", columns)
        self.assertIn("grading_job_id", columns)

    def test_claim_retry_result_and_completion_state_machine(self):
        created, _ = self._create()

        claimed = jobs.claim_due_ai_jobs(limit=2, worker_id="worker-a", lease_seconds=120)

        self.assertEqual(1, len(claimed))
        job = claimed[0]
        self.assertEqual(created["id"], job["id"])
        self.assertEqual("running", job["status"])
        self.assertEqual(1, job["attempt_count"])
        self.assertTrue(job["lease_token"])
        self.assertEqual([], jobs.claim_due_ai_jobs(limit=1, worker_id="worker-b", lease_seconds=120))

        with self._connect() as conn:
            jobs.record_ai_job_attempt_started(conn, job)
            conn.commit()
        self.assertEqual(
            "retry_wait",
            jobs.reschedule_ai_job(job, error_code="provider_503", error_message="temporary"),
        )
        with self._connect() as conn:
            conn.execute("UPDATE ai_jobs SET available_at = '2000-01-01T00:00:00' WHERE id = ?", (job["id"],))
            conn.commit()

        retried = jobs.claim_due_ai_jobs(limit=1, worker_id="worker-b", lease_seconds=120)[0]
        self.assertEqual(2, retried["attempt_count"])
        with self._connect() as conn:
            jobs.record_ai_job_attempt_started(conn, retried)
            conn.commit()
        result = jobs.store_ai_job_result(
            retried,
            {"status": "graded", "submission_id": 7, "score": 88},
            provider="qwen",
            model="qwen3.7-plus",
        )
        self.assertTrue(jobs.mark_ai_job_succeeded(retried["id"], result["id"]))

        with self._connect() as conn:
            final = dict(conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (retried["id"],)).fetchone())
            attempts = [dict(row) for row in conn.execute("SELECT * FROM ai_job_attempts ORDER BY attempt_no")]
        self.assertEqual("succeeded", final["status"])
        self.assertEqual(2, len(attempts))
        self.assertEqual(["error", "success"], [item["status"] for item in attempts])

    def test_expired_lease_is_reclaimed_and_old_worker_cannot_commit(self):
        self._create()
        first = jobs.claim_due_ai_jobs(limit=1, worker_id="worker-a", lease_seconds=120)[0]
        with self._connect() as conn:
            jobs.record_ai_job_attempt_started(conn, first)
            conn.execute(
                "UPDATE ai_jobs SET lease_expires_at = '2000-01-01T00:00:00' WHERE id = ?",
                (first["id"],),
            )
            conn.commit()

        reclaimed = jobs.claim_due_ai_jobs(limit=1, worker_id="worker-b", lease_seconds=120)[0]

        self.assertNotEqual(first["lease_token"], reclaimed["lease_token"])
        self.assertEqual(2, reclaimed["attempt_count"])
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            jobs.store_ai_job_result(first, {"status": "graded", "score": 10})

    def test_cancellation_invalidates_running_lease_and_blocks_late_result(self):
        created, _ = self._create()
        claimed = jobs.claim_due_ai_jobs(limit=1, worker_id="worker-a", lease_seconds=120)[0]

        with self._connect() as conn:
            cancelled = jobs.cancel_ai_jobs_for_source(
                conn,
                task_type="ai_grading",
                source_ref="submission:7",
                reason="manual_grade_override",
            )
            conn.commit()

        self.assertEqual(1, cancelled)
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            jobs.store_ai_job_result(claimed, {"status": "graded", "score": 99})
        with self._connect() as conn:
            final = dict(conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (created["id"],)).fetchone())
        self.assertEqual("cancelled", final["status"])
        self.assertEqual("manual_grade_override", final["last_error_code"])

    def test_worker_task_type_filter_prevents_cross_service_claims(self):
        self._create()
        with self._connect() as conn:
            local, _ = jobs.create_ai_job(
                conn,
                task_type="document_import",
                dedupe_key="local-import:1",
                payload={"target_type": "lesson_plan", "target_id": "p1"},
            )
            conn.commit()

        ai_claims = jobs.claim_due_ai_jobs(
            limit=5,
            worker_id="ai-only",
            lease_seconds=120,
            task_types=("ai_grading", "exam_generation"),
        )
        self.assertEqual(["ai_grading"], [item["task_type"] for item in ai_claims])
        local_claims = jobs.claim_due_ai_jobs(
            limit=5,
            worker_id="main-only",
            lease_seconds=120,
            task_types=("document_import", "document_generation"),
        )
        self.assertEqual([local["id"]], [item["id"] for item in local_claims])

    def test_artifact_is_hash_verified_before_worker_reads_it(self):
        artifact_root = Path(self.temp_dir.name) / "data"
        with patch.object(jobs, "DATA_DIR", artifact_root):
            reference = jobs.persist_ai_job_artifact(
                "exam-task-a",
                {"image_inputs": [{"media_type": "image/png", "data": "YWJj"}]},
            )
            loaded = jobs.load_ai_job_artifact(reference)
            self.assertEqual("YWJj", loaded["image_inputs"][0]["data"])

            artifact_path = artifact_root / reference["relative_path"]
            artifact_path.write_text('{"image_inputs":[]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                jobs.load_ai_job_artifact(reference)

    def test_input_files_survive_enqueue_and_are_cleaned_safely(self):
        artifact_root = Path(self.temp_dir.name) / "data"
        with patch.object(jobs, "DATA_DIR", artifact_root):
            references = jobs.persist_ai_job_input_files(
                "document-import-a",
                [{"name": "../plan.docx", "data": b"durable-input"}],
            )
            loaded = jobs.load_ai_job_input_files(references)
            self.assertEqual("plan.docx", loaded[0]["name"])
            self.assertEqual(b"durable-input", Path(loaded[0]["path"]).read_bytes())
            jobs.cleanup_ai_job_input_files(references)
            self.assertFalse(Path(loaded[0]["path"]).exists())

    def test_terminal_job_can_be_requeued_without_erasing_attempt_history(self):
        created, _ = self._create()
        claimed = jobs.claim_due_ai_jobs(limit=1, worker_id="worker-a", lease_seconds=120)[0]
        with self._connect() as conn:
            jobs.record_ai_job_attempt_started(conn, claimed)
            conn.commit()
        self.assertEqual(
            "review_required",
            jobs.reschedule_ai_job(
                {**claimed, "max_attempts": 1},
                error_code="provider_down",
                error_message="all providers unavailable",
            ),
        )
        with self._connect() as conn:
            requeued = jobs.requeue_ai_job(conn, created["id"])
            conn.commit()
        self.assertEqual("queued", requeued["status"])
        self.assertGreater(int(requeued["max_attempts"]), int(requeued["attempt_count"]))

    def test_embedded_worker_persists_result_before_delivery(self):
        import ai_assistant

        with self._connect() as conn:
            row, _ = jobs.create_ai_job(
                conn,
                task_type="ai_grading",
                dedupe_key="grading:embedded:1",
                payload={
                    "submission_id": 7,
                    "rubric_md": "rubric",
                    "submission_fingerprint": "attempt-token",
                    "grading_revision_hash": "revision-hash",
                },
            )
            conn.commit()
        claimed = jobs.claim_due_ai_jobs(limit=1, worker_id="embedded", lease_seconds=120)[0]

        async def fake_build(job, *, raise_on_failure=False):
            self.assertTrue(raise_on_failure)
            return {
                "submission_id": job.submission_id,
                "status": "graded",
                "score": 93,
                "feedback_md": "ok",
                "review_required": False,
                "quality_audit": {"score_sum_delta": 0},
                "requested_provider": "qwen",
                "requested_model": "qwen3.7-plus",
                "submission_fingerprint": job.submission_fingerprint,
                "grading_revision_hash": job.grading_revision_hash,
            }

        delivered = []

        async def fake_post(payload, submission_id):
            with self._connect() as conn:
                persisted = conn.execute(
                    "SELECT COUNT(*) AS count FROM ai_job_results WHERE job_id = ?",
                    (claimed["id"],),
                ).fetchone()["count"]
            self.assertEqual(1, persisted)
            delivered.append((payload, submission_id))

        with (
            patch.object(ai_assistant, "get_db_connection", side_effect=self._connect),
            patch.object(ai_assistant, "_build_grading_callback_data", side_effect=fake_build),
            patch.object(ai_assistant, "_post_grading_callback_with_retry", side_effect=fake_post),
        ):
            asyncio.run(ai_assistant._execute_durable_ai_job(claimed))

        with self._connect() as conn:
            final = dict(conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (row["id"],)).fetchone())
        self.assertEqual("succeeded", final["status"])
        self.assertEqual(1, len(delivered))
        self.assertEqual(7, delivered[0][1])

    def test_exhausted_grading_attempt_becomes_persisted_review_not_failure(self):
        import ai_assistant

        with self._connect() as conn:
            row, _ = jobs.create_ai_job(
                conn,
                task_type="ai_grading",
                dedupe_key="grading:terminal-review:1",
                max_attempts=1,
                payload={
                    "submission_id": 8,
                    "rubric_md": "rubric",
                    "submission_fingerprint": "attempt-terminal",
                    "grading_revision_hash": "revision-terminal",
                },
            )
            conn.commit()
        claimed = jobs.claim_due_ai_jobs(limit=1, worker_id="embedded", lease_seconds=120)[0]

        async def fail_build(job, *, raise_on_failure=False):
            raise RuntimeError("all providers unavailable")

        delivered = []

        async def fake_post(payload, submission_id):
            delivered.append((payload, submission_id))

        with (
            patch.object(ai_assistant, "get_db_connection", side_effect=self._connect),
            patch.object(ai_assistant, "_build_grading_callback_data", side_effect=fail_build),
            patch.object(ai_assistant, "_post_grading_callback_with_retry", side_effect=fake_post),
        ):
            asyncio.run(ai_assistant._execute_durable_ai_job(claimed))

        with self._connect() as conn:
            final = dict(conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (row["id"],)).fetchone())
            result = dict(conn.execute("SELECT * FROM ai_job_results WHERE job_id = ?", (row["id"],)).fetchone())
            attempt = dict(conn.execute("SELECT * FROM ai_job_attempts WHERE job_id = ?", (row["id"],)).fetchone())
        self.assertEqual("review_required", final["status"])
        self.assertEqual("review_required", result["status"])
        self.assertEqual("error", attempt["status"])
        self.assertEqual("grading_review_required", delivered[0][0]["status"])
        self.assertEqual(8, delivered[0][1])

    def test_exam_worker_persists_and_applies_result_before_completion(self):
        import ai_assistant

        paper_id = "paper-durable-1"
        task_id = "task-durable-1"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exam_papers
                    (id, teacher_id, title, ai_gen_task_id, ai_gen_status, updated_at)
                VALUES (?, 5, 'Durable exam', ?, 'pending', '2026-07-12T00:00:00')
                """,
                (paper_id, task_id),
            )
            row, _ = jobs.create_ai_job(
                conn,
                task_type="exam_generation",
                dedupe_key=f"exam-generation:{task_id}",
                payload={
                    "paper_id": paper_id,
                    "task_id": task_id,
                    "prompt": "generate one question",
                    "teacher_id": 5,
                    "source_type": "manual",
                },
                source_ref=f"exam_paper:{paper_id}",
                owner_user_pk=5,
            )
            conn.commit()
        claimed = jobs.claim_due_ai_jobs(limit=1, worker_id="embedded", lease_seconds=120)[0]

        async def fake_generate(_request):
            return {
                "status": "success",
                "exam_data": {
                    "description": "persisted exam",
                    "grading": {"total_score": 10, "description": "rubric", "style": "medium"},
                    "pages": [
                        {
                            "name": "Part 1",
                            "questions": [
                                {
                                    "id": "q1",
                                    "type": "text",
                                    "question": "1+1=?",
                                    "answer": "2",
                                    "points": 10,
                                    "grading_guidance": "answer is 2",
                                    "deduction_points": "wrong answer loses 10",
                                }
                            ],
                        }
                    ],
                },
            }

        with (
            patch.object(ai_assistant, "get_db_connection", side_effect=self._connect),
            patch.object(ai_assistant, "generate_exam_task", side_effect=fake_generate),
        ):
            asyncio.run(ai_assistant._execute_durable_ai_job(claimed))

        with self._connect() as conn:
            final = dict(conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (row["id"],)).fetchone())
            paper = dict(conn.execute("SELECT * FROM exam_papers WHERE id = ?", (paper_id,)).fetchone())
            result_count = conn.execute(
                "SELECT COUNT(*) AS count FROM ai_job_results WHERE job_id = ?", (row["id"],)
            ).fetchone()["count"]
        self.assertEqual("succeeded", final["status"])
        self.assertEqual("completed", paper["ai_gen_status"])
        self.assertEqual("ready", paper["status"])
        self.assertEqual(1, result_count)
        self.assertIn("persisted exam", paper["questions_json"])


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _FakePostgresConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(str(sql).split()), tuple(params)))
        return _FakeCursor([])


class AIDurableJobPostgresSQLTests(unittest.TestCase):
    def test_claim_uses_skip_locked_and_returning(self):
        conn = _FakePostgresConnection()

        rows = jobs._claim_postgres(
            conn,
            limit=3,
            worker_id="pg-worker",
            lease_expires_at="2026-07-12T12:15:00",
            now="2026-07-12T12:00:00",
        )

        self.assertEqual([], rows)
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING jobs.*", sql)
        self.assertIn("lease_token", sql)
        self.assertEqual(3, params[2])


if __name__ == "__main__":
    unittest.main()
