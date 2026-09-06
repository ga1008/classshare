from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from classroom_app.db.connection import LanShareSQLiteConnection
from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.services import ai_durable_job_service as durable
from classroom_app.services import student_career_job_service as service
from classroom_app.services import student_career_job_worker as worker


class StudentCareerJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-career-jobs-")
        self.path = Path(self.temp.name) / "jobs.db"
        self.handlers = dict(service._HANDLERS)
        self.policies = dict(durable.TASK_POLICIES)
        service._HANDLERS.clear()
        reset_ai_job_schema_guard_for_tests()
        with self.connect() as conn:
            conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE career_test_document (id INTEGER PRIMARY KEY, revision INTEGER, value TEXT, writes INTEGER DEFAULT 0, status TEXT)")
            conn.execute("INSERT INTO career_test_document VALUES (1,1,'old',0,'queued')")
            ensure_ai_job_schema(conn, engine="sqlite")
            conn.commit()
        self.patches = [patch.object(durable, "get_db_connection", self.connect),
                        patch.object(worker, "get_db_connection", self.connect)]
        for target in (durable, service, worker):
            self.patches.append(patch.object(target, "get_configured_db_engine", return_value="sqlite"))
        for p in self.patches:
            p.start()
        async def execute(job, payload):
            return {"value": "new"}
        def apply(conn, job, payload, result):
            return conn.execute(
                "UPDATE career_test_document SET value=?,writes=writes+1,status='ready' WHERE id=1 AND revision=?",
                (result["value"], payload["revision"]),
            ).rowcount == 1
        def fail(conn, job, payload, code, message):
            conn.execute("UPDATE career_test_document SET status='failed' WHERE id=1 AND revision=?", (payload["revision"],))
        service.register_student_career_handler("test_career", execute=execute, apply=apply, fail=fail)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        service._HANDLERS.clear()
        service._HANDLERS.update(self.handlers)
        durable.TASK_POLICIES.clear()
        durable.TASK_POLICIES.update(self.policies)
        reset_ai_job_schema_guard_for_tests()
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, factory=LanShareSQLiteConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, key="career:1:1", owner=7):
        with self.connect() as conn:
            row = service.enqueue_student_career_job(conn, task_type="test_career", dedupe_key=key,
                payload={"revision": 1}, student_id=owner, scope_type="test", scope_id="1")
            conn.commit()
            return row

    def claim(self, name="worker-a"):
        return durable.claim_due_ai_jobs(worker_id=name, task_types=("test_career",),
            max_running=1, concurrency_lock_key=service.career_concurrency_lock_key("test"))

    def delivery(self):
        return durable.claim_result_ready_ai_jobs(worker_id="delivery", task_types=("test_career",))[0]

    def row(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM career_test_document WHERE id=1").fetchone())

    def test_duplicate_request_and_owner_isolation(self):
        first = self.enqueue()
        self.assertEqual(first["id"], self.enqueue()["id"])
        with self.assertRaisesRegex(ValueError, "belongs"):
            self.enqueue(owner=8)
        with self.connect() as conn:
            self.assertEqual({}, service.public_job_state(conn, first["id"], student_id=8))
            self.assertEqual("queued", service.public_job_state(conn, first["id"], student_id=7)["status"])

    def test_lane_capacity_is_atomic_across_workers(self):
        for n in range(4):
            self.enqueue(f"career:{n}:1", owner=n)
        with ThreadPoolExecutor(max_workers=8) as pool:
            batches = list(pool.map(self.claim, [f"worker-{n}" for n in range(8)]))
        self.assertEqual(1, sum(len(batch) for batch in batches))

    def test_result_saved_before_publish_replays_after_restart(self):
        self.enqueue()
        job = self.claim()[0]
        asyncio.run(worker._execute(job))
        self.assertEqual("old", self.row()["value"])
        replay = self.delivery()
        self.assertTrue(worker._apply_result(replay))
        self.assertFalse(worker._apply_result(replay))
        self.assertEqual("new", self.row()["value"])
        self.assertEqual(1, self.row()["writes"])

    def test_old_revision_result_never_overwrites_edit(self):
        self.enqueue()
        asyncio.run(worker._execute(self.claim()[0]))
        with self.connect() as conn:
            conn.execute("UPDATE career_test_document SET revision=2,value='student edit'")
            conn.commit()
        self.assertFalse(worker._apply_result(self.delivery()))
        self.assertEqual("student edit", self.row()["value"])
        self.assertEqual(0, self.row()["writes"])

    def test_cancel_after_candidate_is_persisted_blocks_publication(self):
        job = self.enqueue()
        asyncio.run(worker._execute(self.claim()[0]))
        delivery = self.delivery()
        with self.connect() as conn:
            service.cancel_student_career_job(conn, job["id"], student_id=7)
            conn.commit()
        self.assertFalse(worker._apply_result(delivery))
        self.assertEqual("old", self.row()["value"])

    def test_business_apply_and_job_completion_are_atomic(self):
        self.enqueue()
        asyncio.run(worker._execute(self.claim()[0]))
        original = service._HANDLERS["test_career"]
        def failing_apply(conn, job, payload, result):
            original.apply(conn, job, payload, result)
            raise RuntimeError("simulated failure before commit")
        service.register_student_career_handler("test_career", execute=original.execute, apply=failing_apply)
        with self.assertRaisesRegex(RuntimeError, "before commit"):
            worker._apply_result(self.delivery())
        self.assertEqual("old", self.row()["value"])
        with self.connect() as conn:
            self.assertEqual("result_ready", conn.execute("SELECT status FROM ai_jobs").fetchone()[0])

    def test_failure_budget_and_empty_exception_are_visible(self):
        queued = self.enqueue()
        for attempt in range(3):
            current = self.claim()[0]
            status = worker._fail_job(current, TimeoutError())
            self.assertEqual("dead_letter" if attempt == 2 else "retry_wait", status)
            with self.connect() as conn:
                conn.execute("UPDATE ai_jobs SET available_at='2000-01-01T00:00:00',capacity_reserved_until='2000-01-01T00:00:00' WHERE id=?", (queued["id"],))
                conn.commit()
        self.assertEqual("failed", self.row()["status"])
        self.assertEqual([], self.claim())
        with self.connect() as conn:
            state = service.public_job_state(conn, queued["id"], student_id=7)
            self.assertEqual("TimeoutError", state["error_code"])
            self.assertTrue(state["error_message"])
            self.assertEqual(3, state["attempt_count"])
        self.assertEqual("dead_letter", self.enqueue()["status"])

    def test_supersede_rejects_old_worker_and_expired_lease_can_recover(self):
        queued = self.enqueue()
        old = self.claim()[0]
        with self.connect() as conn:
            conn.execute("UPDATE ai_jobs SET lease_expires_at='2000-01-01T00:00:00' WHERE id=?", (queued["id"],))
            conn.commit()
        replacement = self.claim("replacement")[0]
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            durable.store_ai_job_result(old, {"value": "late"})
        with self.connect() as conn:
            service.supersede_student_career_jobs(conn, scope_type="test", scope_id="1", student_id=7)
            conn.commit()
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            durable.store_ai_job_result(replacement, {"value": "cancelled"})

    def test_capacity_rejects_before_accepting_another_job(self):
        with patch.object(service, "MAX_ACTIVE_PER_STUDENT", 1):
            self.enqueue()
            with self.assertRaises(service.CareerJobCapacityError):
                self.enqueue("career:1:2")
            self.assertEqual(1, self.enqueue()["id"])

    def test_timeout_holds_shared_capacity_for_other_students(self):
        self.enqueue()
        active = self.claim()[0]
        self.enqueue("career:other", owner=8)
        worker._fail_job(active, TimeoutError())
        self.assertEqual([], self.claim())
        with self.connect() as conn:
            conn.execute("UPDATE ai_jobs SET capacity_reserved_until='2000-01-01T00:00:00'")
            conn.commit()
        self.assertEqual(8, self.claim()[0]["owner_user_pk"])

    def test_cancelling_running_work_holds_shared_capacity(self):
        active = self.enqueue()
        self.claim()
        self.enqueue("career:other", owner=8)
        with self.connect() as conn:
            service.cancel_student_career_job(conn, active["id"], student_id=7)
            conn.commit()
        self.assertEqual([], self.claim())
        with self.connect() as conn:
            conn.execute("UPDATE ai_jobs SET capacity_reserved_until='2000-01-01T00:00:00'")
            conn.commit()
        self.assertEqual(8, self.claim()[0]["owner_user_pk"])

    def test_fair_claim_gives_another_student_a_turn(self):
        self.enqueue()
        self.enqueue("career:second", owner=7)
        self.enqueue("career:other", owner=8)
        asyncio.run(worker._execute(self.claim()[0]))
        worker._apply_result(self.delivery())
        picked = durable.claim_due_ai_jobs(task_types=("test_career",), max_running=1,
            concurrency_lock_key=service.career_concurrency_lock_key("test"), fair_owner=True)
        self.assertEqual(8, picked[0]["owner_user_pk"])

    def test_aged_work_is_not_starved_by_new_students(self):
        first = self.enqueue()
        old = self.enqueue("career:second", owner=7)
        self.enqueue("career:other", owner=8)
        asyncio.run(worker._execute(self.claim()[0]))
        worker._apply_result(self.delivery())
        with self.connect() as conn:
            conn.execute("UPDATE ai_jobs SET created_at='2000-01-01T00:00:00' WHERE id=?", (old["id"],))
            conn.commit()
        picked = durable.claim_due_ai_jobs(task_types=("test_career",), max_running=1,
            concurrency_lock_key=service.career_concurrency_lock_key("test"), fair_owner=True)
        self.assertEqual(old["id"], picked[0]["id"])

    def test_expired_unreclaimed_lease_cannot_publish(self):
        self.enqueue()
        running = self.claim()[0]
        with self.connect() as conn:
            conn.execute("UPDATE ai_jobs SET lease_expires_at='2000-01-01T00:00:00'")
            conn.commit()
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            durable.store_ai_job_result(running, {"value": "late"}, require_valid_lease=True)
        asyncio.run(worker._execute(self.claim()[0]))
        delivery = self.delivery()
        with self.connect() as conn:
            conn.execute("UPDATE ai_jobs SET lease_expires_at='2000-01-01T00:00:00'")
            conn.commit()
        self.assertFalse(worker._apply_result(delivery))
        self.assertEqual("old", self.row()["value"])

    def test_one_recovery_failure_does_not_skip_other_domain(self):
        completed = []
        def broken(conn):
            raise RuntimeError("simulated unavailable career recovery")
        def healthy(conn):
            completed.append(True)
        with patch.object(worker, "_load_domains", return_value=[SimpleNamespace(recover_career_jobs=broken, recover_resume_jobs=healthy)]):
            with self.assertRaisesRegex(RuntimeError, "career recovery"):
                worker._recover_domains()
        self.assertEqual([True], completed)

    def test_connection_wait_cannot_store_an_expired_result(self):
        self.enqueue()
        job = self.claim()[0]
        initial = durable._now()
        clock = [initial]
        @contextmanager
        def delayed_connection():
            with self.connect() as conn:
                clock[0] = datetime.fromisoformat(job["lease_expires_at"]) + timedelta(seconds=1)
                yield conn
        with patch.object(durable, "_now", side_effect=lambda: clock[0]), patch.object(durable, "get_db_connection", delayed_connection):
            with self.assertRaisesRegex(RuntimeError, "lease changed"):
                durable.store_ai_job_result(job, {"value": "late"}, require_valid_lease=True)
        with self.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM ai_job_results").fetchone()[0])

    def test_store_work_crossing_lease_rolls_back_candidate_and_attempt(self):
        self.enqueue()
        job = self.claim()[0]
        initial = durable._now()
        clock = [initial]
        finish = durable.record_ai_job_attempt_finished
        def delayed_finish(*args, **kwargs):
            finish(*args, **kwargs)
            clock[0] = datetime.fromisoformat(job["lease_expires_at"]) + timedelta(seconds=1)
        with patch.object(durable, "_now", side_effect=lambda: clock[0]), patch.object(durable, "record_ai_job_attempt_finished", delayed_finish):
            with self.assertRaisesRegex(RuntimeError, "lease changed"):
                durable.store_ai_job_result(job, {"value": "late"}, require_valid_lease=True)
        with self.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM ai_job_results").fetchone()[0])
            self.assertEqual("running", conn.execute("SELECT status FROM ai_jobs").fetchone()[0])

    def test_slow_failure_callback_cannot_commit_after_lease_expiry(self):
        from fastapi import HTTPException
        self.enqueue()
        job = self.claim()[0]
        initial = durable._now()
        clock = [initial]
        handler = service._HANDLERS["test_career"]
        def delayed_fail(conn, *args):
            conn.execute("UPDATE career_test_document SET status='failed'")
            clock[0] = datetime.fromisoformat(job["lease_expires_at"]) + timedelta(seconds=1)
        service.register_student_career_handler("test_career", execute=handler.execute, apply=handler.apply, fail=delayed_fail)
        with patch.object(durable, "_now", side_effect=lambda: clock[0]):
            self.assertEqual("superseded", worker._fail_job(job, HTTPException(415, "Unsupported file")))
        self.assertEqual("queued", self.row()["status"])
        with self.connect() as conn:
            self.assertEqual("running", conn.execute("SELECT status FROM ai_jobs").fetchone()[0])

    def test_local_validation_is_permanent_and_local_busy_preserves_retry_delay(self):
        from fastapi import HTTPException
        from classroom_app.services.libreoffice_service import LibreOfficeBusy
        for code in (400, 413, 415, 422):
            self.assertTrue(worker._safe_failure(HTTPException(code, "Invalid document"))[2])
        self.enqueue()
        job = self.claim()[0]
        before = durable._now()
        self.assertEqual("retry_wait", worker._fail_job(job, HTTPException(429, "busy", headers={"Retry-After": "90"})))
        with self.connect() as conn:
            available = conn.execute("SELECT available_at FROM ai_jobs").fetchone()[0]
        self.assertGreaterEqual(available, durable._iso(before + timedelta(seconds=90)))
        self.assertFalse(worker._safe_failure(LibreOfficeBusy())[2])

    def test_heartbeat_storage_error_stops_computation_without_overwriting_job(self):
        self._check_failed_heartbeat(RuntimeError("storage unavailable"))

    def test_heartbeat_storage_timeout_stops_computation(self):
        def stalled(*args, **kwargs):
            time.sleep(0.04)
            return True
        self._check_failed_heartbeat(stalled)

    def _check_failed_heartbeat(self, renewal):
        cancelled = []
        async def execute(job, payload):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        handler = service._HANDLERS["test_career"]
        service.register_student_career_handler("test_career", execute=execute, apply=handler.apply, fail=handler.fail)
        self.enqueue()
        job = self.claim()[0]
        with patch.object(worker, "HEARTBEAT_SECONDS", 0.001), patch.object(worker, "HEARTBEAT_TIMEOUT_SECONDS", 0.005), patch.object(durable, "renew_ai_job_lease", side_effect=renewal):
            asyncio.run(worker._execute(job))
        self.assertEqual([True], cancelled)
        with self.connect() as conn:
            self.assertEqual("running", conn.execute("SELECT status FROM ai_jobs").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM ai_job_results").fetchone()[0])

    def test_paused_worker_rejects_new_work_but_preserves_idempotent_read(self):
        original = self.enqueue()
        with patch.object(service, "CAREER_JOBS_ENABLED", False):
            self.assertEqual(original["id"], self.enqueue()["id"])
            with self.assertRaises(service.CareerJobCapacityError):
                self.enqueue("career:new")


if __name__ == "__main__":
    unittest.main()
