from contextlib import contextmanager
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from classroom_app.db import schema_scheduler
from classroom_app.services import smart_classroom_alignment_jobs as jobs


class AlignmentJobsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory(prefix="school-alignment-jobs-"))
        self.database = Path(folder) / "fixture.db"
        self.open_connections = 0
        self.fingerprints = {}
        self.enterContext(patch.object(schema_scheduler, "_SCHEMA_READY", False))
        self.enterContext(patch.object(jobs, "get_db_connection", self.connection))
        self.enterContext(patch.object(jobs, "_context", side_effect=self.context))
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,teacher_id INTEGER,semester_id INTEGER);
                CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,end_date TEXT);
                CREATE TABLE smart_attendance_course_links(class_offering_id INTEGER PRIMARY KEY,state TEXT DEFAULT 'matched',source_json TEXT DEFAULT '{}');
                CREATE TABLE teacher_smart_classroom_credentials(teacher_id INTEGER,enabled INTEGER,last_status TEXT);
                INSERT INTO academic_semesters VALUES(1,'2099-06-30'),(2,'2000-01-01');
                INSERT INTO teacher_smart_classroom_credentials VALUES(1,1,'verified'),(2,0,'verified');
            """)
            conn.executemany("INSERT INTO class_offerings VALUES(?,1,1)", [(i,) for i in range(1, 26)])
            conn.execute("INSERT INTO class_offerings VALUES(26,2,1),(27,1,2)")
            schema_scheduler.ensure_scheduler_schema(conn)
            conn.commit()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        self.open_connections += 1
        try:
            yield conn
        finally:
            conn.close()
            self.open_connections -= 1

    def context(self, conn, teacher_id, offering_id):
        return {"fingerprint": self.fingerprints.get(offering_id, f"input-{teacher_id}-{offering_id}")}

    def task(self, conn, task_id):
        row = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
        row["payload"] = json.loads(row["payload_json"])
        return row

    def test_signal_is_committed_or_rolled_back_with_classroom(self):
        with self.connection() as conn:
            conn.execute("UPDATE class_offerings SET semester_id=2 WHERE id=1")
            jobs.signal_offering_alignment(conn, 1)
            with self.connection() as observer:
                self.assertEqual(observer.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0], 0)
            conn.rollback()
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT semester_id FROM class_offerings WHERE id=1").fetchone()[0], 1)
            jobs.signal_offering_alignment(conn, 1)
            conn.commit()
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0], 1)

    def test_same_input_does_not_steal_running_lease_and_new_input_gets_new_task(self):
        with self.connection() as conn:
            first = jobs.signal_offering_alignment(conn, 1)
            conn.execute("UPDATE scheduled_tasks SET status='running',locked_by='owner',locked_at='lease',attempt_count=3 WHERE id=?", (first,))
            conn.commit()
            self.assertEqual(jobs.signal_offering_alignment(conn, 1), first)
            same = self.task(conn, first)
            self.assertEqual((same["status"], same["locked_by"], same["locked_at"], same["attempt_count"]), ("running", "owner", "lease", 3))
            self.fingerprints[1] = "replanned"
            second = jobs.signal_offering_alignment(conn, 1)
            self.assertNotEqual(first, second)
            self.assertEqual(self.task(conn, second)["payload"]["fingerprint"], "replanned")
            conn.commit()

    async def test_stale_or_transferred_work_does_not_read_school(self):
        with self.connection() as conn:
            task = self.task(conn, jobs.signal_offering_alignment(conn, 1))
            conn.commit()
        with patch("classroom_app.services.smart_classroom_workflow_service.align_offering", new_callable=AsyncMock, create=True) as align:
            self.fingerprints[1] = "changed"
            result = await jobs.handle_alignment(task)
            self.assertEqual(result["reason"], "classroom_changed")
            self.fingerprints.clear()
            with self.connection() as conn:
                conn.execute("UPDATE class_offerings SET teacher_id=3 WHERE id=1")
                conn.commit()
            result = await jobs.handle_alignment(task)
            self.assertEqual(result["reason"], "offering_owner_changed")
            align.assert_not_awaited()

    async def test_network_batch_begins_after_database_connection_closes(self):
        with self.connection() as conn:
            task = self.task(conn, jobs.signal_offering_alignment(conn, 1))
            conn.commit()
        async def align(user, offering_id, *, background):
            self.assertEqual(self.open_connections, 0)
            self.assertTrue(background)
            self.assertEqual((user, offering_id), ({"role": "teacher", "id": 1}, 1))
            return {"alignment": {"state": "matched"}}
        with patch("classroom_app.services.smart_classroom_workflow_service.align_offering", side_effect=align, create=True):
            result = await jobs.handle_alignment(task)
        self.assertEqual(result["alignment_state"], "matched")

    async def test_superseded_signal_preserves_last_committed_input_without_stealing_running_work(self):
        with self.connection() as conn:
            old = self.task(conn, jobs.signal_offering_alignment(conn, 1))
            conn.commit()
        self.fingerprints[1] = "roster-imported-after-first-signal"
        with patch("classroom_app.services.smart_classroom_workflow_service.align_offering", new_callable=AsyncMock, create=True) as align:
            result = await jobs.handle_alignment(old)
            self.assertTrue(result["replacement_queued"])
            with self.connection() as conn:
                latest = conn.execute("SELECT id FROM scheduled_tasks WHERE id<>?", (old["id"],)).fetchone()[0]
                conn.execute("UPDATE scheduled_tasks SET status='running',locked_by='latest-owner',locked_at='latest-lease' WHERE id=?", (latest,))
                conn.commit()
            await jobs.handle_alignment(old)
            align.assert_not_awaited()
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0], 2)
            latest_task = self.task(conn, latest)
            self.assertEqual((latest_task["status"], latest_task["locked_by"], latest_task["locked_at"]),
                             ("running", "latest-owner", "latest-lease"))

    def test_discovery_is_bounded_and_cursor_skips_already_bound_disabled_and_expired(self):
        with self.connection() as conn:
            conn.execute("INSERT INTO smart_attendance_course_links(class_offering_id) VALUES(2)")
            scan_id = jobs.ensure_tasks(conn)
            conn.execute("UPDATE scheduled_tasks SET status='running',locked_by='owner',locked_at='lease' WHERE id=?", (scan_id,))
            task = self.task(conn, scan_id)
            conn.commit()
        first = jobs.handle_bootstrap_scan(task)
        self.assertEqual((first["examined"], first["after_offering_id"]), (20, 21))
        with self.connection() as conn:
            task = self.task(conn, scan_id)
        second = jobs.handle_bootstrap_scan(task)
        self.assertEqual((second["examined"], second["after_offering_id"]), (4, 25))
        with self.connection() as conn:
            task = self.task(conn, scan_id)
            queued = [json.loads(row[0])["offering_id"] for row in conn.execute("SELECT payload_json FROM scheduled_tasks WHERE task_kind=?", (jobs.TASK_KIND,))]
        self.assertEqual(set(queued), set(range(1, 26)) - {2})
        third = jobs.handle_bootstrap_scan(task)
        self.assertEqual((third["examined"], third["after_offering_id"]), (0, 0))

    def test_lost_discovery_lease_rolls_back_all_signals(self):
        with self.connection() as conn:
            scan_id = jobs.ensure_tasks(conn)
            conn.execute("UPDATE scheduled_tasks SET status='running',locked_by='new-owner',locked_at='new-lease' WHERE id=?", (scan_id,))
            task = self.task(conn, scan_id)
            conn.commit()
        task["locked_by"] = "old-owner"
        with self.assertRaisesRegex(RuntimeError, "lease changed"):
            jobs.handle_bootstrap_scan(task)
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE task_kind=?", (jobs.TASK_KIND,)).fetchone()[0], 0)

    def test_later_configured_account_discovers_unmatched_target_without_touching_reviews(self):
        with self.connection() as conn:
            conn.execute("INSERT INTO smart_attendance_course_links VALUES(1,'unmatched','{}'),(2,'review','{}'),(26,'unmatched','{}')")
            conn.commit()
        jobs.handle_bootstrap_scan({"payload": {}})
        with self.connection() as conn:
            queued = [json.loads(row[0])["offering_id"] for row in conn.execute("SELECT payload_json FROM scheduled_tasks WHERE task_kind=?", (jobs.TASK_KIND,))]
            self.assertIn(1, queued)
            self.assertNotIn(2, queued)
            self.assertNotIn(26, queued)
            conn.execute("UPDATE teacher_smart_classroom_credentials SET enabled=1 WHERE teacher_id=2")
            conn.commit()
        jobs.handle_bootstrap_scan({"payload": {"after_offering_id": 25}})
        with self.connection() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM scheduled_tasks WHERE owner_user_pk=2").fetchone())

    def test_existing_startup_task_keeps_its_cursor_and_running_owner(self):
        with self.connection() as conn:
            scan_id = jobs.ensure_tasks(conn)
            conn.execute("UPDATE scheduled_tasks SET status='running',locked_by='owner',payload_json=? WHERE id=?", ('{"after_offering_id":19}', scan_id))
            self.assertEqual(jobs.ensure_tasks(conn), scan_id)
            task = self.task(conn, scan_id)
            self.assertEqual((task["status"], task["locked_by"], task["payload"]["after_offering_id"]), ("running", "owner", 19))

    def test_schema_initialization_and_legacy_fixtures_do_not_gain_jobs(self):
        with self.connection() as conn:
            conn.execute("DROP TABLE smart_attendance_course_links")
            self.assertIsNone(jobs.signal_offering_alignment(conn, 1))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0], 0)


class AlignmentJobsWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_smart_classroom_workflow_router import WorkflowRouterTests
        self.fixture = WorkflowRouterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.enterContext(patch.object(jobs, "get_db_connection", self.fixture.database))
        self.enterContext(patch.object(schema_scheduler, "_SCHEMA_READY", False))
        schema_scheduler.ensure_scheduler_schema(self.fixture.conn)
        self.fixture.conn.commit()

    def signal(self):
        task_id = jobs.signal_offering_alignment(self.fixture.conn, 10)
        self.fixture.conn.commit()
        row = dict(self.fixture.conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
        row["payload"] = json.loads(row["payload_json"])
        return row

    def test_real_context_missing_account_still_queues_then_later_credential_has_new_signal(self):
        f = self.fixture
        f.account.side_effect = lambda *args: None
        missing = self.signal()
        result = asyncio.run(jobs.handle_alignment(missing))
        self.assertEqual(result["status"], "processed")
        f.groups.assert_not_awaited()
        saved = f.conn.execute("SELECT alignment_json FROM smart_attendance_course_links WHERE class_offering_id=10").fetchone()
        self.assertIn("missing_credential", saved[0])
        f.account.side_effect = lambda *args: f.access
        configured = self.signal()
        self.assertNotEqual(missing["id"], configured["id"])
        self.assertNotEqual(missing["payload"]["fingerprint"], configured["payload"]["fingerprint"])
        self.assertEqual(asyncio.run(jobs.handle_alignment(missing))["status"], "superseded")
        self.assertEqual(asyncio.run(jobs.handle_alignment(configured))["alignment_state"], "matched")

    def test_class_membership_hook_and_replan_signal_use_real_identity(self):
        from classroom_app.services.offering_membership_service import replace_offering_class_links
        f = self.fixture
        replace_offering_class_links(f.conn, offering_id=10, teacher_id=7, class_ids=[1])
        f.conn.commit()
        first = self.signal()
        self.assertEqual(f.conn.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE task_kind=?", (jobs.TASK_KIND,)).fetchone()[0], 1)
        f.conn.execute("UPDATE class_offering_sessions SET session_date='2026-11-16',week_index=12 WHERE id=21")
        changed = self.signal()
        self.assertNotEqual(first["id"], changed["id"])
        self.assertEqual(asyncio.run(jobs.handle_alignment(first))["reason"], "classroom_changed")
        f.groups.assert_not_awaited()

    def test_one_background_course_batch_binds_whole_term_with_one_remote_snapshot(self):
        from classroom_app.services import smart_classroom_miniapp_adapter as mini
        f = self.fixture
        f.conn.execute("UPDATE courses SET total_hours=6 WHERE id=1")
        for sid, ordinal, day, week in ((22, 2, "2026-11-16", 12), (23, 3, "2026-11-23", 13)):
            f.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,weekday,week_index,academic_section_text,academic_course_code) VALUES(?,10,?,'Synthetic lesson',?,0,?,'2-3','E1')", (sid, ordinal, day, week))
        f.groups.return_value[0]["total_hours"] = 6
        mini.list_schedules.return_value["items"][0]["weeks"] = [11, 12, 13]
        task = self.signal()
        result = asyncio.run(jobs.handle_alignment(task))
        self.assertEqual(result["alignment_state"], "matched")
        self.assertEqual(f.conn.execute("SELECT COUNT(*) FROM smart_attendance_session_links WHERE state='matched'").fetchone()[0], 3)
        f.groups.assert_awaited_once()
        mini.list_schedules.assert_awaited_once()
        mini.read_slot.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
