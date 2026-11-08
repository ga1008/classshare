"""Actual v2 transaction retries against capacity locks on an owned PG cluster."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from classroom_app.db.errors import DatabaseBusyError
from classroom_app.db.postgres import LanSharePostgresConnection
from classroom_app.routers import learning
from classroom_app.services import behavior_inbox_service as inbox, behavior_tracking_service as tracking
from classroom_app.services import material_progress_collection_service as collection
from tests import test_cultivation_weekly_snapshot_postgres as schema_support


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class MaterialProgressRetryPostgresTests(unittest.TestCase):
    # Reuse only the canonical, empty-schema fixture; do not inherit/import a
    # TestCase class into this module and accidentally duplicate its tests.
    _foundation_connection = classmethod(schema_support.WeeklySnapshotPostgresTests._foundation_connection.__func__)

    @classmethod
    def setUpClass(cls):
        schema_support.WeeklySnapshotPostgresTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        schema_support.WeeklySnapshotPostgresTests.tearDownClass.__func__(cls)

    def connect(self, connection_class=LanSharePostgresConnection):
        return schema_support.WeeklySnapshotPostgresTests.connect(self, connection_class)

    def setUp(self):
        schema_support.WeeklySnapshotPostgresTests.setUp(self)
        self.received = datetime.now()
        test = self
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return test.received
        self.enterContext(patch.object(collection, "datetime", Clock))
        self.pipeline = inbox.BehaviorWritePipeline(tracking._record_behavior_batch_in_connection)
        self.enterContext(patch.object(self.pipeline, "start", return_value=None))
        self.enterContext(patch.object(tracking, "_get_behavior_write_pipeline", return_value=self.pipeline))
        self.user = {"role": "student", "id": self.students[0], "name": "Synthetic material retry",
                     "login_time": (self.received - timedelta(seconds=180)).isoformat()}
        end = int((self.received - timedelta(seconds=1)).timestamp() * 1000)
        self.body = {"material_id": self.material, "progress_version": 2, "page_key": "material_viewer",
            "user_key": f"student:{self.students[0]}", "batch_id": f"b1_{int(self.received.timestamp()*1000)}_{uuid4().hex}",
            "scroll_ratio": .5, "completed": False,
            "events": [{"action_type": "presence_heartbeat", "summary_text": "", "payload": {
                "presence_version": 2, "presence_observed_ms": end, "visibility_state": "visible",
                "focused": True, "idle_seconds": 0, "ai_panel_open": False,
                "interval_start_ms": end - 60000, "interval_end_ms": end}}]}

    def call(self, *, body=None, user=None):
        return learning.post_material_progress(self.offering, learning.MaterialProgressPayload(**(body or self.body)), user=user or self.user)

    def state(self, conn=None):
        def load(conn):
            return {table: [dict(row) for row in conn.execute("SELECT * FROM " + table + " WHERE class_offering_id=?" +
                (" ORDER BY batch_key" if table == "behavior_write_inbox" else " ORDER BY id"), (self.offering,))]
                for table in ("learning_material_progress", "learning_progress_snapshots", "behavior_write_inbox")}
        if conn is not None: return load(conn)
        with self.connect() as connection: return load(connection)

    def test_capacity_timeout_rolls_back_and_closes_before_retrying_the_original_batch(self):
        baseline, first_failure, release_retry = self.state(), Event(), Event()
        opened, received_times = [], []
        real = collection.collect_material_progress
        def observed_collect(conn, **kwargs):
            received_times.append(kwargs["received_at"])
            return real(conn, **kwargs)
        def factory():
            conn = self.connect()
            opened.append(conn)
            return conn
        def pause(_delay):
            self.assertIsNone(opened[-1].raw_connection, "Backoff must not retain a pooled connection")
            first_failure.set()
            if not release_retry.wait(5): raise AssertionError("Native test did not release the confirmed retry")
        with self.connect() as holder, ThreadPoolExecutor(max_workers=1) as workers, \
                patch.object(learning, "get_db_connection", side_effect=factory), \
                patch.object(collection, "collect_material_progress", side_effect=observed_collect), \
                patch.object(inbox.time, "sleep", side_effect=pause):
            holder.execute("SELECT pg_advisory_xact_lock(?,?)", inbox.CAPACITY_LOCK)
            future = workers.submit(self.call)
            try:
                self.assertTrue(first_failure.wait(10))
                self.assertEqual(baseline, self.state(), "Material, dirty version and receipt must roll back together")
                # Even if wall time advances during backoff, the accepted sample
                # keeps the original server receive instant and immutable body.
                first_received = self.received
                self.received += timedelta(seconds=2)
                holder.rollback()
            finally:
                release_retry.set()
            result = future.result(timeout=10)
        self.assertEqual("durable_queued", result["write_mode"])
        self.assertEqual(2, len(opened))
        self.assertEqual([first_received, first_received], received_times)
        current = self.state()
        row = current["learning_material_progress"][0]
        old = baseline["learning_material_progress"][0]
        self.assertEqual((old["accumulated_seconds"] + 60, old["active_seconds"] + 60, old["view_count"]),
                         (row["accumulated_seconds"], row["active_seconds"], row["view_count"]))
        self.assertEqual(1, len(current["behavior_write_inbox"]))
        self.assertEqual(first_received.isoformat(), current["behavior_write_inbox"][0]["received_at"])

    def test_actual_unknown_commit_reconciles_material_dirty_version_and_receipt_without_reapplying(self):
        once, before = {"lost": True}, {}
        test = self
        class CommitOnceLost(LanSharePostgresConnection):
            captured = False
            def execute(inner, sql, params=None):
                if "INSERT INTO behavior_write_inbox" in sql: inner.captured = True
                return super().execute(sql, params)
            def commit(inner):
                if inner.captured and once["lost"]:
                    before.update(test.state(inner))
                    super().commit()
                    once["lost"] = False
                    raise ConnectionError("Synthetic material COMMIT succeeded but ACK was lost")
                super().commit()
        with patch.object(learning, "get_db_connection", side_effect=lambda: self.connect(CommitOnceLost)):
            result = self.call()
        self.assertTrue(result["duplicate"])
        self.assertEqual(before, self.state())
        self.assertEqual(1, self.pipeline.stats()["unknown_commit_count"])
        self.assertEqual(1, len(before["behavior_write_inbox"]))
        # A changed immutable body remains a conflict even after receipt ACK.
        with self.assertRaises(HTTPException) as raised: self.call(body={**self.body, "scroll_ratio": .9})
        self.assertEqual(409, raised.exception.status_code)
        self.assertEqual(before, self.state())

    def test_exhaustion_permission_loss_and_deleted_parent_never_retain_partial_material_writes(self):
        baseline, opened = self.state(), []
        def factory():
            conn = self.connect()
            opened.append(conn)
            return conn
        with self.connect() as holder, patch.object(learning, "get_db_connection", side_effect=factory):
            holder.execute("SELECT pg_advisory_xact_lock(?,?)", inbox.CAPACITY_LOCK)
            with self.assertRaises(DatabaseBusyError): self.call()
            self.assertEqual(3, len(opened))
            self.assertTrue(all(conn.raw_connection is None for conn in opened))
            self.assertEqual(baseline, self.state())
            holder.rollback()
        with patch.object(learning, "_ensure_classroom_access", side_effect=HTTPException(403, "Synthetic denied")) as access:
            with self.assertRaises(HTTPException) as raised: self.call()
            self.assertEqual(403, raised.exception.status_code)
            self.assertEqual(1, access.call_count)
        self.assertEqual(baseline, self.state())
        actor = self.students[1]
        user = {**self.user, "id": actor}
        body = {**self.body, "user_key": f"student:{actor}",
                "batch_id": f"b1_{int(self.received.timestamp()*1000)}_{uuid4().hex}"}
        deferred, resume = Event(), Event()
        def pause(_delay):
            deferred.set()
            if not resume.wait(5): raise AssertionError("Native test did not resume after parent deletion")
        with self.connect() as holder, ThreadPoolExecutor(max_workers=1) as workers, \
                patch.object(inbox.time, "sleep", side_effect=pause):
            holder.execute("SELECT pg_advisory_xact_lock(?,?)", inbox.CAPACITY_LOCK)
            future = workers.submit(self.call, body=body, user=user)
            try:
                self.assertTrue(deferred.wait(10))
                holder.rollback()
                with self.connect() as conn: conn.execute("DELETE FROM students WHERE id=?", (actor,))
            finally:
                resume.set()
            with self.assertRaises(HTTPException) as raised: future.result(timeout=10)
            self.assertEqual(404, raised.exception.status_code)
        expected = {table: [row for row in rows if row.get("student_id") != actor] for table, rows in baseline.items()}
        self.assertEqual(expected, self.state())


if __name__ == "__main__":
    unittest.main()
