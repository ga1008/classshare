from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.db.connection import LanShareSQLiteConnection
from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.services import ai_durable_job_service as jobs
from classroom_app.services import ai_grading_service as grading


class InterruptSubmissionGradingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "grading.db"
        reset_ai_job_schema_guard_for_tests()
        self.patches = [
            patch.object(jobs, "get_configured_db_engine", return_value="sqlite"),
            patch("classroom_app.db.schema_ai_jobs.get_configured_db_engine", return_value="sqlite"),
        ]
        for item in self.patches:
            item.start()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE submissions (
                    id INTEGER PRIMARY KEY, status TEXT NOT NULL, score REAL, feedback_md TEXT,
                    grading_started_at TEXT, grading_attempt_fingerprint TEXT,
                    resubmission_allowed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            ensure_ai_job_schema(conn, engine="sqlite")
            conn.commit()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()
        reset_ai_job_schema_guard_for_tests()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, factory=LanShareSQLiteConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _seed(self, conn, submission_id: int, status: str, score):
        row, _created = jobs.create_ai_job(
            conn,
            task_type="ai_grading",
            dedupe_key=f"ai-grading:{submission_id}:token",
            payload={"submission_id": submission_id},
            source_ref=f"submission:{submission_id}",
        )
        conn.execute(
            """
            INSERT INTO submissions (id, status, score, grading_started_at, grading_attempt_fingerprint, grading_job_id)
            VALUES (?, ?, ?, '2026-10-13T08:00:00', 'fp:token', ?)
            """,
            (submission_id, status, score, row["id"]),
        )
        conn.commit()
        return int(row["id"])

    def _job_status(self, conn, job_id: int) -> str:
        return conn.execute("SELECT status FROM ai_jobs WHERE id = ?", (job_id,)).fetchone()["status"]

    def test_interrupt_cancels_job_and_restores_ungraded_submission(self):
        with self._connect() as conn:
            job_id = self._seed(conn, 1, "grading", None)
            submission = dict(conn.execute("SELECT * FROM submissions WHERE id = 1").fetchone())
            resumed = grading.interrupt_submission_ai_grading_with_connection(conn, submission, reason="stopped_by_teacher")
            conn.commit()
            row = conn.execute("SELECT * FROM submissions WHERE id = 1").fetchone()
            self.assertEqual(resumed, "submitted")
            self.assertEqual(row["status"], "submitted")
            self.assertIsNone(row["grading_attempt_fingerprint"])
            self.assertIsNone(row["grading_job_id"])
            self.assertIsNone(row["grading_started_at"])
            self.assertEqual(self._job_status(conn, job_id), "cancelled")

    def test_interrupt_keeps_previous_grade_visible(self):
        with self._connect() as conn:
            job_id = self._seed(conn, 2, "grading", 76)
            submission = dict(conn.execute("SELECT * FROM submissions WHERE id = 2").fetchone())
            resumed = grading.interrupt_submission_ai_grading_with_connection(conn, submission, reason="stopped_by_teacher")
            self.assertEqual(resumed, "graded")
            self.assertEqual(self._job_status(conn, job_id), "cancelled")

    def test_interrupt_is_noop_when_not_grading(self):
        with self._connect() as conn:
            job_id = self._seed(conn, 3, "graded", 90)
            submission = dict(conn.execute("SELECT * FROM submissions WHERE id = 3").fetchone())
            self.assertIsNone(grading.interrupt_submission_ai_grading_with_connection(conn, submission, reason="x"))
            self.assertNotEqual(self._job_status(conn, job_id), "cancelled")

    def test_stop_route_service_cancels_durable_job(self):
        with self._connect() as conn:
            job_id = self._seed(conn, 4, "grading", None)
        with patch.object(grading, "get_db_connection", side_effect=self._connect), \
             patch.object(grading, "_load_submission_for_grading",
                          side_effect=lambda conn, sid, teacher_id=None: dict(
                              conn.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone())), \
             patch.object(grading, "_release_stage_exam_grading_attempts"):
            result = grading.stop_submission_ai_grading(4, teacher_id=1)
        self.assertEqual(result["status"], "stopped")
        with self._connect() as conn:
            self.assertEqual(self._job_status(conn, job_id), "cancelled")


class LeaseHeartbeatAbortTests(unittest.TestCase):
    def test_lost_lease_triggers_abort_callback(self):
        import asyncio

        import ai_assistant

        calls = []

        async def run():
            stop = asyncio.Event()
            with patch.object(ai_assistant, "renew_ai_job_lease", return_value=False):
                await ai_assistant._durable_lease_heartbeat(
                    {"id": 1, "lease_token": "t"}, stop, on_lease_lost=lambda: calls.append("lost"), interval=0.01,
                )

        asyncio.run(run())
        self.assertEqual(calls, ["lost"])

    def test_renewed_lease_does_not_abort(self):
        import asyncio

        import ai_assistant

        calls = []

        async def run():
            stop = asyncio.Event()
            with patch.object(ai_assistant, "renew_ai_job_lease", return_value=True):
                task = asyncio.create_task(ai_assistant._durable_lease_heartbeat(
                    {"id": 1, "lease_token": "t"}, stop, on_lease_lost=lambda: calls.append("lost"), interval=0.01,
                ))
                await asyncio.sleep(0.05)
                stop.set()
                await task

        asyncio.run(run())
        self.assertEqual(calls, [])


class DurableJobAbortTests(unittest.TestCase):
    def test_lost_lease_aborts_only_the_job_and_worker_continues(self):
        import asyncio

        import ai_assistant

        state = {"body_cancelled": False}

        async def slow_body(job):
            try:
                await asyncio.sleep(30)  # an in-flight provider request
            except asyncio.CancelledError:
                state["body_cancelled"] = True
                raise

        async def losing_heartbeat(job, stop_event, *, on_lease_lost=None, interval=None):
            await asyncio.sleep(0.01)
            on_lease_lost()

        async def run():
            with patch.object(ai_assistant, "_execute_durable_ai_job_body", slow_body), \
                 patch.object(ai_assistant, "_durable_lease_heartbeat", losing_heartbeat):
                await ai_assistant._execute_durable_ai_job({"id": 1})
                return "worker-alive"

        self.assertEqual(asyncio.run(run()), "worker-alive")
        self.assertTrue(state["body_cancelled"])

    def test_worker_shutdown_still_propagates(self):
        import asyncio

        import ai_assistant

        async def slow_body(job):
            await asyncio.sleep(30)

        async def quiet_heartbeat(job, stop_event, *, on_lease_lost=None, interval=None):
            await stop_event.wait()

        async def run():
            with patch.object(ai_assistant, "_execute_durable_ai_job_body", slow_body), \
                 patch.object(ai_assistant, "_durable_lease_heartbeat", quiet_heartbeat):
                task = asyncio.create_task(ai_assistant._execute_durable_ai_job({"id": 1}))
                await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
