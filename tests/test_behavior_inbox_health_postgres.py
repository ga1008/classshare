"""Real lock recovery and cross-connection arrivals on an owned offline cluster."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_behavior_inbox import ensure_behavior_inbox_schema
from classroom_app.services import behavior_inbox_service as inbox
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class BehaviorInboxHealthPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_health_" + uuid4().hex[:12]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port,
                                    database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    @contextmanager
    def connection(self, connection_class=LanSharePostgresConnection):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        conn = connection_class(raw)
        try:
            conn.execute("SET lock_timeout='2s'")
            conn.execute("SET statement_timeout='5s'")
            conn.commit()
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def setUp(self):
        self.enterContext(patch.object(inbox, "get_configured_db_engine", return_value="postgres"))
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"))
        self.enterContext(patch.object(inbox, "get_db_connection", self.connection))
        self.enterContext(patch.object(inbox, "BEHAVIOR_WRITE_LOCK_TIMEOUT_MS", 100))
        self.enterContext(patch.object(inbox, "BEHAVIOR_WRITE_FLUSH_INTERVAL_MS", 10))
        with self.connection() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute("CREATE TABLE class_offerings(id BIGINT PRIMARY KEY)")
            conn.execute("CREATE TABLE students(id BIGINT PRIMARY KEY)")
            conn.execute("INSERT INTO class_offerings VALUES(11)")
            conn.execute("INSERT INTO students VALUES(7)")
            conn.execute("CREATE TABLE effects(batch_id TEXT PRIMARY KEY)")
            ensure_behavior_inbox_schema(conn, engine="postgres")
            conn.commit()

        def apply(conn, **values):
            conn.execute("INSERT INTO effects VALUES (?)", (values["batch_id"],))
            return {"accepted_event_count": 1, "logged_event_ids": [1]}

        self.pipeline = inbox.BehaviorWritePipeline(apply)
        self.addCleanup(self.pipeline.stop, 3)

    def wait_until(self, predicate, *, seconds=3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.005)
        self.fail("Timed out waiting for the bounded worker state")

    def test_actual_55p03_retains_diagnostics_and_recovers_without_any_business_event(self):
        entering = threading.Event()
        probe = {"pid": None, "sqlstates": []}

        class Probe(LanSharePostgresConnection):
            def execute(inner, sql, params=None):
                if str(sql).startswith("DELETE FROM behavior_write_inbox"):
                    probe["pid"] = inner.raw_connection.info.backend_pid
                    entering.set()
                try:
                    return super().execute(sql, params)
                except Exception as exc:
                    probe["sqlstates"].append(getattr(exc, "sqlstate", None))
                    raise

        with self.connection() as holder, self.connection() as observer, patch.object(
                inbox, "get_db_connection", lambda: self.connection(Probe)):
            holder.execute("LOCK TABLE behavior_write_inbox IN ACCESS EXCLUSIVE MODE")
            held_pid = holder.raw_connection.info.backend_pid
            self.pipeline.start()
            self.assertTrue(entering.wait(2))
            self.wait_until(lambda: observer.execute("SELECT ?=ANY(pg_blocking_pids(?)) AS blocked",
                (held_pid, probe["pid"])).fetchone()["blocked"])
            self.wait_until(lambda: self.pipeline.stats()["last_error"] == "LockNotAvailable:55P03")
            self.assertIn("55P03", probe["sqlstates"])
            self.assertIsNone(self.pipeline.stats()["last_recovered_at"])
            holder.rollback()
            self.wait_until(lambda: bool(self.pipeline.stats()["last_recovered_at"]))
            self.pipeline.stop(3)
        stats = self.pipeline.stats()
        self.assertEqual("", stats["last_error"])
        self.assertEqual("LockNotAvailable:55P03", stats["last_failure_code"])
        self.assertEqual("cleanup", stats["last_failure_phase"])
        self.assertGreaterEqual(stats["retry_count"], 1)
        self.assertEqual(0, stats["committed_batch_count"])
        with self.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM effects").fetchone()["n"])

    def test_other_connection_retry_after_initial_sample_prevents_premature_recovery(self):
        with self.pipeline._lock:
            self.pipeline._record_failure_locked("LockNotAvailable:55P03", phase="drain")
        self.pipeline._refresh_depth()
        request = inbox.BehaviorWriteRequest(11, 7, "student", "Synthetic", "classroom",
            [{"action_type": "page_action", "summary_text": "Synthetic", "payload": {}}])
        with patch.object(self.pipeline, "start"):
            self.pipeline.submit(request)
        with self.connection() as newcomer:
            newcomer.execute("UPDATE behavior_write_inbox SET attempts=1,retry_at='9999-01-01T00:00:00'")
            newcomer.commit()
        self.assertFalse(self.pipeline._drain_once())
        self.assertEqual(0, self.pipeline.stats()["queue_depth"])
        self.pipeline._recover_if_idle()
        self.assertEqual("LockNotAvailable:55P03", self.pipeline.stats()["last_error"])
        self.assertEqual(1, self.pipeline.stats()["retrying_count"])
        self.assertIsNone(self.pipeline.stats()["last_recovered_at"])
        with self.connection() as newcomer:
            newcomer.execute("UPDATE behavior_write_inbox SET retry_at=NULL")
            newcomer.commit()
        self.assertTrue(self.pipeline._drain_once())
        self.assertFalse(self.pipeline._drain_once())
        self.pipeline._refresh_depth()
        self.pipeline._recover_if_idle()
        self.assertEqual("", self.pipeline.stats()["last_error"])
        self.assertIsNotNone(self.pipeline.stats()["last_recovered_at"])
        with self.connection() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) AS n FROM effects").fetchone()["n"])
            self.assertEqual("applied", conn.execute("SELECT state FROM behavior_write_inbox").fetchone()["state"])

    def test_startup_noop_ddl_conflicts_with_skip_locked_drain_then_recovers(self):
        ready, release, entering = threading.Event(), threading.Event(), threading.Event()
        probe = {"pid": None, "sqlstates": []}
        original_select = self.pipeline._select_next_receipt

        def select(conn, *, postgres):
            if not ready.is_set():
                ready.set()  # Cleanup and the initial queue sample have finished.
                if not release.wait(3):
                    raise AssertionError("Test did not release the gated selection")
            return original_select(conn, postgres=postgres)

        class Probe(LanSharePostgresConnection):
            def execute(inner, sql, params=None):
                if str(sql).startswith("WITH actor_heads AS MATERIALIZED"):
                    probe["pid"] = inner.raw_connection.info.backend_pid
                    entering.set()
                try:
                    return super().execute(sql, params)
                except Exception as exc:
                    probe["sqlstates"].append(getattr(exc, "sqlstate", None))
                    raise

        with self.connection() as holder, self.connection() as observer, patch.object(
                inbox, "get_db_connection", lambda: self.connection(Probe)), patch.object(
                self.pipeline, "_select_next_receipt", side_effect=select):
            self.pipeline.start()
            try:
                self.assertTrue(ready.wait(2))
                # This exact idempotent startup migration still takes a table
                # lock even though resolved_at already exists. SKIP LOCKED
                # skips row locks, not the relation locks required by SELECT.
                holder.execute("ALTER TABLE behavior_write_inbox ADD COLUMN IF NOT EXISTS resolved_at TEXT")
                held_pid = holder.raw_connection.info.backend_pid
                release.set()
                self.assertTrue(entering.wait(2))
                self.wait_until(lambda: observer.execute("SELECT ?=ANY(pg_blocking_pids(?)) AS blocked",
                    (held_pid, probe["pid"])).fetchone()["blocked"])
                self.wait_until(lambda: self.pipeline.stats()["last_error"] == "LockNotAvailable:55P03")
                self.assertIn("55P03", probe["sqlstates"])
                self.assertEqual("drain", self.pipeline.stats()["last_failure_phase"])
                holder.commit()
                self.wait_until(lambda: bool(self.pipeline.stats()["last_recovered_at"]))
            finally:
                release.set()
                holder.rollback()
                self.pipeline.stop(3)
        stats = self.pipeline.stats()
        self.assertEqual("", stats["last_error"])
        self.assertEqual("LockNotAvailable:55P03", stats["last_failure_code"])
        self.assertEqual("drain", stats["last_failure_phase"])
        self.assertGreaterEqual(stats["retry_count"], 1)
        self.assertEqual(0, stats["unknown_commit_count"])
        self.assertEqual(0, stats["committed_batch_count"])
        with self.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM behavior_write_inbox").fetchone()["n"])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) AS n FROM effects").fetchone()["n"])
