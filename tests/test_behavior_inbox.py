from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from classroom_app.db.schema_behavior_inbox import ensure_behavior_inbox_schema
from classroom_app.db.schema_behavior_collection_health import ensure_behavior_collection_health_schema
from classroom_app.db.errors import DatabaseBusyError
from classroom_app.services import behavior_inbox_service as inbox


class BehaviorInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "synthetic.db"
        self.fail_commit = False
        owner = self

        class Connection(sqlite3.Connection):
            def commit(self):
                super().commit()
                if owner.fail_commit:
                    owner.fail_commit = False
                    raise ConnectionError("synthetic response lost after commit")

        @contextmanager
        def connect():
            conn = sqlite3.connect(self.path, factory=Connection)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

        self.connect = connect
        self.guard = patch.object(inbox, "get_db_connection", connect)
        self.guard.start()
        with connect() as conn:
            ensure_behavior_inbox_schema(conn)
            ensure_behavior_collection_health_schema(conn)
            conn.execute("CREATE TABLE class_offerings (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO class_offerings VALUES (1)")
            conn.execute("INSERT INTO class_offerings VALUES (2)")
            conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO students VALUES (10)")
            conn.execute("CREATE TABLE effects (actor INTEGER, kind TEXT, event_time TEXT)")
        self.request = inbox.BehaviorWriteRequest(1, 10, "student", "Synthetic", "test", [{"action_type": "click"}])
        self.pipeline = inbox.BehaviorWritePipeline(self.apply)
        self.start_guard = patch.object(self.pipeline, "start")
        self.start_guard.start()

    def tearDown(self):
        self.pipeline.stop(1)
        self.start_guard.stop()
        self.guard.stop()
        self.temp.cleanup()

    def apply(self, conn, **kwargs):
        for event in kwargs["events"]:
            conn.execute("INSERT INTO effects VALUES (?, ?, ?)",
                         (kwargs["user_pk"], event["action_type"], kwargs["recorded_at"]))
        return {"accepted_event_count": len(kwargs["events"]), "logged_event_ids": [1]}

    def count(self, table):
        with self.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_durable_acceptance_duplicate_payload_conflict_and_restart_replay(self):
        receipt = self.pipeline.submit(self.request)
        self.assertEqual("durable_queued", receipt["write_mode"])
        self.assertEqual(0, receipt["accepted_event_count"])
        self.assertEqual(1, receipt["durable_event_count"])
        self.assertEqual(0, self.count("effects"))
        self.pipeline.submit(self.request)
        self.assertEqual(1, self.count("behavior_write_inbox"))
        with self.assertRaises(inbox.BehaviorBatchConflict):
            self.pipeline.submit(replace(self.request, events=[{"action_type": "changed"}]))
        recovered = inbox.BehaviorWritePipeline(self.apply)
        self.assertTrue(recovered._drain_once())
        self.assertFalse(recovered._drain_once())
        self.assertEqual(1, self.count("effects"))
        self.assertEqual("synced", self.pipeline.submit(self.request)["write_mode"])

    def test_unknown_intake_and_apply_commit_are_reconciled_without_duplicate_effects(self):
        self.fail_commit = True
        self.pipeline.submit(self.request)
        self.assertEqual(1, self.count("behavior_write_inbox"))
        self.fail_commit = True
        with self.assertRaises(ConnectionError):
            self.pipeline._drain_once()
        self.assertFalse(self.pipeline._drain_once())
        self.assertEqual(1, self.count("effects"))
        self.assertEqual(2, self.pipeline.stats()["unknown_commit_count"])

    def test_failed_sql_rolls_back_effect_and_retains_retry_then_preserves_actor_order(self):
        self.pipeline.submit(self.request)
        self.pipeline.submit(replace(self.request, class_offering_id=2, batch_id=inbox.new_batch_id(), events=[{"action_type": "second"}]))

        def broken(conn, **kwargs):
            self.apply(conn, **kwargs)
            conn.execute("SELECT missing_synthetic_column")

        self.pipeline._apply_batch = broken
        self.assertTrue(self.pipeline._drain_once())
        self.assertEqual(0, self.count("effects"))
        self.pipeline._apply_batch = self.apply
        self.assertFalse(self.pipeline._drain_once())  # Later actor event cannot overtake its retry.
        with self.connect() as conn:
            conn.execute("UPDATE behavior_write_inbox SET retry_at = NULL")
        self.assertTrue(self.pipeline._drain_once())
        self.assertTrue(self.pipeline._drain_once())
        with self.connect() as conn:
            rows = conn.execute("SELECT kind FROM effects ORDER BY rowid").fetchall()
        self.assertEqual(["click", "second"], [row[0] for row in rows])

    def test_full_inbox_rejects_new_intake_but_confirms_existing_id(self):
        with patch.object(inbox, "BEHAVIOR_WRITE_QUEUE_SIZE", 1):
            self.pipeline.submit(self.request)
            self.pipeline.submit(self.request)
            with self.assertRaises(DatabaseBusyError):
                self.pipeline.submit(replace(self.request, batch_id=inbox.new_batch_id()))
        self.assertEqual(1, self.count("behavior_write_inbox"))

    def test_worker_start_commits_scoring_dependencies_before_starting_thread(self):
        from classroom_app.services import behavior_tracking_service as behavior, student_points_service, student_streak_service
        pipeline = Mock()
        pipeline.stats.return_value = {"queue_capacity": 20}

        def verify_committed():
            with self.connect() as conn:
                tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"student_activity_streaks", "student_activity_days", "student_point_ledger"} <= tables)

        pipeline.start.side_effect = verify_committed
        with patch.object(behavior, "get_db_connection", self.connect), patch.object(
            behavior, "_get_behavior_write_pipeline", return_value=pipeline
        ), patch.object(student_points_service, "_SCHEMA_READY", False), patch.object(student_streak_service, "_SCHEMA_READY", False):
            behavior.start_behavior_write_pipeline()
        pipeline.start.assert_called_once()

    def test_caller_transaction_owns_intake_rollback_and_failure_savepoint(self):
        with self.connect() as conn:
            conn.execute("BEGIN")
            receipt = self.pipeline.submit(self.request, conn=conn)
            self.assertEqual("transaction_pending", receipt["write_mode"])
            self.assertEqual(0, receipt["durable_event_count"])
            conn.rollback()
        self.assertEqual(0, self.count("behavior_write_inbox"))
        with self.connect() as conn:
            conn.execute("BEGIN")
            self.pipeline.submit(self.request, conn=conn)
            with self.assertRaises(inbox.BehaviorBatchConflict):
                self.pipeline.submit(replace(self.request, events=[]), conn=conn)
            conn.execute("INSERT INTO effects VALUES (20, 'business', '')")
        self.assertEqual(1, self.count("effects"))

    def test_expired_tombstone_cleanup_cannot_make_old_batch_new_again(self):
        self.pipeline.submit(self.request)
        self.pipeline._drain_once()
        old_id = f"b1_{int((time.time() - 12 * 86400) * 1000)}_expired"
        with self.connect() as conn:
            old_time = (datetime.now() - timedelta(days=12)).isoformat()
            conn.execute("UPDATE behavior_write_inbox SET batch_id = ?, received_at = ?, applied_at = ?, resolved_at = ?",
                         (old_id, old_time, old_time, old_time))
        self.pipeline._cleanup_receipts()
        self.assertEqual(0, self.count("behavior_write_inbox"))
        with self.assertRaises(inbox.BehaviorBatchConflict):
            self.pipeline.submit(replace(self.request, batch_id=old_id))
        self.assertEqual(1, self.count("effects"))

    def test_actor_deletion_waits_for_accepted_work_and_retires_receipt(self):
        self.pipeline.submit(self.request)
        with self.assertRaises(inbox.BehaviorDeletionPending), self.connect() as conn:
            inbox.prepare_behavior_deletion(conn, user_role="student", user_pk=10)
        self.assertEqual(1, self.count("students"))
        self.pipeline._drain_once()
        with self.connect() as conn:
            inbox.prepare_behavior_deletion(conn, user_role="student", user_pk=10)
            conn.execute("DELETE FROM students WHERE id = 10")
        self.assertTrue(self.pipeline.submit(self.request)["retired"])
        with self.assertRaises(inbox.BehaviorBatchConflict):
            self.pipeline.submit(replace(self.request, batch_id=inbox.new_batch_id()))
        self.assertEqual(1, self.count("effects"))

    def test_classroom_deletion_keeps_pending_and_rejects_new_work_after_removal(self):
        self.pipeline.submit(self.request)
        with self.assertRaises(inbox.BehaviorDeletionPending), self.connect() as conn:
            inbox.prepare_behavior_deletion(conn, class_offering_id=1)
        self.pipeline._drain_once()
        with self.connect() as conn:
            inbox.prepare_behavior_deletion(conn, class_offering_id=1)
            conn.execute("DELETE FROM class_offerings WHERE id = 1")
            row = conn.execute("SELECT display_name, session_started_at FROM behavior_write_inbox").fetchone()
            self.assertEqual("", row["display_name"])
            self.assertIsNone(row["session_started_at"])
        with self.assertRaises(inbox.BehaviorBatchConflict):
            self.pipeline.submit(replace(self.request, batch_id=inbox.new_batch_id()))

    def test_supervision_survives_pool_failure_and_stop_keeps_durable_work(self):
        self.pipeline.submit(self.request)
        self.start_guard.stop()
        with patch.object(self.pipeline, "_drain_batch", side_effect=[DatabaseBusyError("synthetic pool"), 1]) as drain:
            self.pipeline.start()
            deadline = time.monotonic() + 2
            while drain.call_count < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(drain.call_count, 2)
        self.pipeline._wake.set()
        self.pipeline.flush(3)
        self.pipeline.stop(1)
        self.assertFalse(self.pipeline.stats()["alive"])
        with self.assertRaises(DatabaseBusyError):
            self.pipeline.submit(replace(self.request, batch_id=inbox.new_batch_id()))
        self.assertEqual(1, self.count("effects"))

    def run_worker_cycles(self, count):
        waits = 0

        def wait(_timeout):
            nonlocal waits
            waits += 1
            if waits >= count:
                self.pipeline._stop_event.set()

        with patch.object(self.pipeline._wake, "wait", side_effect=wait):
            self.pipeline._run()

    def test_empty_worker_recovers_startup_lock_failure_without_new_business_data(self):
        class LockNotAvailable(Exception):
            sqlstate = "55P03"

        cleanup = self.pipeline._cleanup_receipts
        attempts = 0

        def transient_cleanup():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise LockNotAvailable("private database details")
            cleanup()

        with patch.object(self.pipeline, "_cleanup_receipts", side_effect=transient_cleanup):
            self.run_worker_cycles(2)
        stats = self.pipeline.stats()
        self.assertEqual("", stats["last_error"])
        self.assertEqual("LockNotAvailable:55P03", stats["last_failure_code"])
        self.assertEqual("cleanup", stats["last_failure_phase"])
        self.assertIsNotNone(stats["last_failure_at"])
        self.assertIsNotNone(stats["last_recovered_at"])
        self.assertEqual(1, stats["retry_count"])
        self.assertEqual(0, stats["committed_batch_count"])
        self.assertEqual(0, self.count("effects"))
        self.assertEqual(0, self.count("behavior_write_inbox"))
        self.assertNotIn("private database details", str(stats))

    def test_failed_sample_cannot_recover_from_pre_failure_empty_snapshot(self):
        self.pipeline._refresh_depth()
        with patch.object(self.pipeline, "_refresh_depth", side_effect=DatabaseBusyError("still blocked")):
            self.run_worker_cycles(2)
        self.pipeline._recover_if_idle()
        stats = self.pipeline.stats()
        self.assertEqual("DatabaseBusyError:", stats["last_error"])
        self.assertEqual("queue_sample", stats["last_failure_phase"])
        self.assertIsNone(stats["last_recovered_at"])
        self.assertEqual(2, stats["retry_count"])

    def test_delayed_retry_and_quarantine_do_not_clear_current_error(self):
        self.pipeline.submit(self.request)
        self.pipeline._apply_batch = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("retry"))
        self.pipeline._drain_once()
        for state in ("pending", "quarantined"):
            with self.subTest(state=state):
                with self.connect() as conn:
                    conn.execute("UPDATE behavior_write_inbox SET state = ?", (state,))
                self.pipeline._refresh_depth()
                self.pipeline._recover_if_idle()
                self.assertEqual("ValueError:", self.pipeline.stats()["last_error"])
                self.assertIsNone(self.pipeline.stats()["last_recovered_at"])
        self.assertEqual(0, self.count("effects"))

    def test_unknown_apply_commit_reconciles_and_recovers_when_queue_is_empty(self):
        self.pipeline.submit(self.request)
        self.fail_commit = True
        with patch.object(self.pipeline, "_cleanup_receipts"):
            self.run_worker_cycles(2)
        # The first commit succeeded remotely despite its lost acknowledgement.
        # Refresh after the failure, just as the bounded worker sample does.
        self.pipeline._refresh_depth()
        self.pipeline._recover_if_idle()
        stats = self.pipeline.stats()
        self.assertEqual("", stats["last_error"])
        self.assertEqual("ConnectionError:", stats["last_failure_code"])
        self.assertEqual("drain", stats["last_failure_phase"])
        self.assertEqual(1, stats["unknown_commit_count"])
        self.assertEqual(1, self.count("effects"))
        with self.connect() as conn:
            self.assertEqual("applied", conn.execute("SELECT state FROM behavior_write_inbox").fetchone()[0])

    def test_recovery_rechecks_arrival_after_empty_sample_without_normal_poll_overhead(self):
        with self.pipeline._lock:
            self.pipeline._record_failure_locked("LockNotAvailable:55P03", phase="drain")
        self.pipeline._refresh_depth()
        self.pipeline.submit(self.request)
        with self.connect() as conn:
            conn.execute("UPDATE behavior_write_inbox SET attempts=1,retry_at='9999-01-01T00:00:00'")
        self.assertFalse(self.pipeline._drain_once())
        self.pipeline._recover_if_idle()
        self.assertEqual("LockNotAvailable:55P03", self.pipeline.stats()["last_error"])
        self.assertEqual(1, self.pipeline.stats()["retrying_count"])
        with patch.object(self.pipeline, "_refresh_depth") as sample:
            self.pipeline._recover_if_idle()  # Known retry needs no recovery query.
            self.pipeline._update_stats(last_error="", queue_depth=0, retrying_count=0)
            self.pipeline._recover_if_idle()  # Normal healthy poll needs none either.
        sample.assert_not_called()
