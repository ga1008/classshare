"""Opt-in schedule business tests in an exclusively created native PG database.

Explicit ASSESSMENT_REHEARSAL_TEST_CLUSTER / ASSESSMENT_REHEARSAL_TEST_PORT
are required; ASSESSMENT_REHEARSAL_TEST_ADMIN_DATABASE can select an existing
rehearsal control connection. Its application schema/data are never changed.
connect_offline verifies loopback, server directory, port, and database prefix.
Application DATABASE_URL is never used.
"""
from __future__ import annotations

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_academic_schedule_predictions import ensure_academic_schedule_prediction_schema
from classroom_app.services.academic_schedule_prediction_service import (
    ScheduleSyncLeaseError, claim_schedule_sync, fail_schedule_sync, load_authorized_prediction_lessons,
    load_teacher_prediction_snapshot, reconcile_and_publish_snapshot, release_schedule_sync,
)
from tests.test_academic_schedule_predictions import SCHEMA, base_snapshot, official, request, slot
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER") and os.environ.get("ASSESSMENT_REHEARSAL_TEST_PORT"),
                     "Requires an explicit isolated loopback PostgreSQL cluster and port")
class AcademicSchedulePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_schedule_" + uuid.uuid4().hex[:12]
        cls.created = False
        admin_database = os.environ.get("ASSESSMENT_REHEARSAL_TEST_ADMIN_DATABASE", "lanshare_assessment_rehearsal")
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database=admin_database)
        cls.admin.autocommit = True
        try:
            # No IF NOT EXISTS, reused DB, production DSN, or restored business data.
            cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')
            cls.created = True
        except Exception:
            cls.admin.close()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.created:
                cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def connection(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        conn = LanSharePostgresConnection(raw)
        if conn.execute("SELECT current_database()").fetchone()[0] != self.database:
            conn.close()
            raise RuntimeError("Refusing to use a database not exclusively created by this test")
        return conn

    def setUp(self):
        self.conn = self.connection()
        self.addCleanup(self.conn.close)
        self.conn.execute("DROP SCHEMA public CASCADE")
        self.conn.execute("CREATE SCHEMA public")
        self.conn.execute(SCHEMA.replace("PRAGMA foreign_keys=ON;", ""))
        ensure_academic_schedule_prediction_schema(self.conn, engine="postgres")
        self.conn.commit()
        self.now = datetime(2026, 9, 19, 2, tzinfo=timezone.utc)

    def publish(self, snapshot):
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        result = reconcile_and_publish_snapshot(self.conn, 1, 1, snapshot, lease["token"], now=self.now)
        release_schedule_sync(self.conn, 1, lease["token"])
        self.conn.commit()
        return result

    def read(self):
        return load_teacher_prediction_snapshot(self.conn, 1, 1)

    def test_pending_approved_then_middle_cancel_preserve_identity_and_material(self):
        self.publish(base_snapshot([request()]))
        pair = [row for row in self.read()["lessons"] if row.get("adjustment")]
        self.assertEqual([101, 101], [row["session_id"] for row in pair])
        self.assertEqual([1, 1], [row["session_no"] for row in pair])
        self.assertEqual(1, sum(row["counts_towards_total"] for row in pair))
        self.assertEqual({"/classroom/10?session_id=101"}, {row["classroom_url"] for row in pair})

        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        self.assertEqual([101], self.publish(snapshot)["updated_session_ids"])
        moved = self.conn.execute("SELECT session_date,order_index,title,content,course_lesson_id,learning_material_id FROM class_offering_sessions WHERE id=101").fetchone()
        self.assertEqual(("2026-10-11", 1, "原第一课", "不可覆盖第一课内容", 11, 51), tuple(moved.values()))
        self.assertFalse(any(row.get("adjustment") for row in self.read()["lessons"]))

        snapshot["requests"].append(request("C2", status="approved", original=slot("2026-09-26", (8, 9)), kind="cancel"))
        snapshot["official"].pop(1)
        self.assertEqual([102], self.publish(snapshot)["cancelled_session_ids"])
        rows = self.conn.execute("SELECT id,order_index,schedule_status FROM class_offering_sessions WHERE class_offering_id=10 ORDER BY id").fetchall()
        self.assertEqual([(101, 1, "scheduled"), (102, 2, "cancelled"), (103, 3, "scheduled")], [tuple(row.values()) for row in rows])
        self.assertEqual([901, 902, 903], [row[0] for row in self.conn.execute("SELECT material_id FROM session_materials ORDER BY session_id").fetchall()])
        student = load_authorized_prediction_lessons(self.conn, [10, 20], semester_id=1)
        self.assertEqual([10], student["covered_offering_ids"])
        self.assertEqual({101, 103}, {row["session_id"] for row in student["lessons"]})

    def test_two_connections_teacher_lease_and_expiry_fence(self):
        barrier = threading.Barrier(2)

        def claim():
            conn = self.connection()
            try:
                barrier.wait(timeout=5)
                result = claim_schedule_sync(conn, 1, now=self.now)
                conn.commit()
                return result
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(["busy", "claimed"], sorted(row["status"] for row in results))
        old_token = next(row["token"] for row in results if row["status"] == "claimed")
        replacement = claim_schedule_sync(self.conn, 1, now=self.now + timedelta(seconds=601))
        self.conn.commit()
        self.assertEqual("claimed", replacement["status"])
        self.assertFalse(release_schedule_sync(self.conn, 1, old_token))
        with self.assertRaises(ScheduleSyncLeaseError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), old_token, now=self.now + timedelta(seconds=602))
        self.assertIsNone(self.read())
        reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), replacement["token"], now=self.now + timedelta(seconds=602))
        with self.assertRaises(ScheduleSyncLeaseError):
            reconcile_and_publish_snapshot(self.conn, 1, 1, base_snapshot(), replacement["token"], now=self.now + timedelta(seconds=602))
        release_schedule_sync(self.conn, 1, replacement["token"])
        self.conn.commit()
        self.assertEqual(1, self.read()["sync_state"]["revision"])

    def test_database_failure_after_schedule_write_rolls_back_snapshot_and_binding(self):
        import psycopg
        self.publish(base_snapshot())
        self.conn.execute("""CREATE FUNCTION fail_schedule_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic publication failure'; END; $$""")
        self.conn.execute("""CREATE TRIGGER fail_schedule_snapshot BEFORE UPDATE ON teacher_academic_schedule_snapshots
            FOR EACH ROW EXECUTE FUNCTION fail_schedule_snapshot()""")
        lease = claim_schedule_sync(self.conn, 1, now=self.now)
        self.conn.commit()
        snapshot = base_snapshot([request(status="approved")])
        snapshot["official"][0] = official("2026-10-11")
        with self.assertRaises(psycopg.errors.RaiseException):
            reconcile_and_publish_snapshot(self.conn, 1, 1, snapshot, lease["token"], now=self.now)
        # SAVEPOINT rollback leaves the caller's outer transaction usable.
        self.assertEqual("2026-09-20", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM academic_schedule_change_session_links").fetchone()[0])
        fail_schedule_sync(self.conn, 1, lease["token"], error="合成失败")
        self.conn.commit()
        self.assertEqual(1, self.read()["sync_state"]["revision"])
        self.assertEqual("failed", self.read()["sync_state"]["status"])
        self.assertEqual(3, len(self.read()["lessons"]))

    def test_merged_coverage_and_conflicting_approved_targets(self):
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-13',academic_section_text='4-5',weekday=6,week_index=2 WHERE id=102")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-12',academic_section_text='6-7',weekday=5,week_index=2 WHERE id=101")
        change = request(status="approved", original=slot("2026-09-12", (6, 7)), proposed=slot("2026-09-13", (6, 7)))
        self.publish({"official": [official("2026-09-13", (4, 5, 6, 7))], "requests": [change]})
        self.assertEqual([(102, [4, 5]), (101, [6, 7])], [(row["session_id"], row["sections"]) for row in self.read()["lessons"]])
        original = slot("2026-09-13", (6, 7))
        changes = [request("M1", status="approved", original=original, proposed=slot("2026-10-10", (6, 7))),
                   request("M2", status="approved", original=original, proposed=slot("2026-10-11", (6, 7)))]
        snapshot = {"official": [official("2026-10-10", (6, 7)), official("2026-10-11", (6, 7))], "requests": changes}
        self.assertEqual([], self.publish(snapshot)["updated_session_ids"])
        self.assertEqual("2026-09-13", self.conn.execute("SELECT session_date FROM class_offering_sessions WHERE id=101").fetchone()[0])
        self.assertIn("approved_conflict", {row["code"] for row in self.read()["warnings"]})


if __name__ == "__main__":
    unittest.main()
