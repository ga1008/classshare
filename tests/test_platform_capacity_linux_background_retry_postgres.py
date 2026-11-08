"""Opt-in real PG rollback proof on an explicitly owned synthetic cluster."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.services.learning_progress_service import mark_student_learning_progress_dirty
from tools.assessment_postgres_rehearsal import connect_offline
from tools import platform_capacity_linux_background as bg


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"),
                     "Requires an explicitly owned offline PostgreSQL cluster")
class DirtyBatchPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_dirty_" + uuid4().hex[:16]
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

    def connect(self, connection_type=LanSharePostgresConnection, *, checkout_timeout=None):
        if checkout_timeout is not None:
            self.assertGreater(checkout_timeout, 0)
            self.assertLessEqual(checkout_timeout, bg.DIRTY_BUDGET_SECONDS)
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        return connection_type(raw)

    def setUp(self):
        self.manifest = {"student_ids": list(range(1, 201)), "class_offering_id": 12}
        self.events = []
        with self.connect() as conn:
            conn.execute("DROP TABLE IF EXISTS learning_progress_snapshots")
            conn.execute("""CREATE TABLE learning_progress_snapshots (
                class_offering_id INTEGER, student_id INTEGER, score NUMERIC, progress_percent NUMERIC,
                components_json TEXT, metrics_json TEXT, level_key TEXT, calculated_at TEXT,
                dirty INTEGER, dirty_at TEXT, metadata_json TEXT, dirty_version INTEGER,
                PRIMARY KEY(class_offering_id,student_id))""")
            conn.execute("INSERT INTO learning_progress_snapshots(class_offering_id,student_id,dirty_version) "
                         "SELECT 12,g,0 FROM generate_series(1,200) g")

    def record(self, name, **values):
        self.events.append((name, values))

    def apply(self, connect=None, record=None):
        bg._apply_dirty_batch(0, self.manifest, connect or self.connect,
                             mark_student_learning_progress_dirty, record or self.record)

    def generations(self):
        with self.connect() as conn:
            return {row["student_id"]: row["dirty_version"] for row in conn.execute(
                "SELECT student_id,dirty_version FROM learning_progress_snapshots ORDER BY student_id").fetchall()}

    def test_real_lock_timeout_rolls_back_partial_cohort_then_commits_same_ten_once(self):
        retry_seen = threading.Event()
        def record(name, **values):
            self.record(name, **values)
            if name == "projection_dirty_conflict":
                retry_seen.set()
        with self.connect() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
            # Four earlier marks execute before the fifth conflicts; their
            # generations must roll back before the same batch is retried.
            blocker.execute("SELECT student_id FROM learning_progress_snapshots WHERE student_id=5 FOR UPDATE")
            future = executor.submit(self.apply, record=record)
            try:
                self.assertTrue(retry_seen.wait(5), "No real PG conflict was observed")
            finally:
                blocker.rollback()
            future.result(timeout=15)
        self.assertEqual({i: int(i <= 10) for i in range(1, 201)}, self.generations())
        conflicts = [values for name, values in self.events if name == "projection_dirty_conflict"]
        self.assertTrue(conflicts)
        self.assertTrue(all(row["sqlstate"] == "55P03" and row["rollback_confirmed"] for row in conflicts))
        self.assertEqual(1, sum(name == "projection_dirty_batch_committed" for name, _ in self.events))
        self.assertEqual(10, sum(name == "projection_dirty" for name, _ in self.events))

    def test_exhausted_real_lock_conflicts_leave_every_generation_unchanged(self):
        with self.connect() as blocker:
            blocker.execute("SELECT student_id FROM learning_progress_snapshots WHERE student_id=5 FOR UPDATE")
            with self.assertRaises(bg._RolledBackDirtyConflict):
                self.apply()
            blocker.rollback()
        self.assertTrue(all(value == 0 for value in self.generations().values()))
        conflicts = [values for name, values in self.events if name == "projection_dirty_conflict"]
        self.assertEqual(5, len(conflicts))
        self.assertFalse(conflicts[-1]["will_retry"])

    def test_real_commit_with_lost_response_is_not_replayed(self):
        connections = []
        class LostCommitResponse(LanSharePostgresConnection):
            def commit(self):
                super().commit()
                raise ConnectionError("synthetic response lost after actual PG commit")
        def connect(**kwargs):
            conn = self.connect(LostCommitResponse, **kwargs)
            connections.append(conn)
            return conn
        with self.assertRaises(ConnectionError):
            self.apply(connect=connect)
        self.assertEqual(1, len(connections))
        self.assertEqual({i: int(i <= 10) for i in range(1, 201)}, self.generations())
        self.assertTrue(self.events[-1][1]["commit_outcome_unknown"])
        self.assertFalse(any(name == "projection_dirty_batch_committed" for name, _ in self.events))


if __name__ == "__main__":
    unittest.main()
