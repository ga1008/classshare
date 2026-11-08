"""Weekly worker boundaries using real SQLite transactions and score effects."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from classroom_app.db import schema_scheduler
from classroom_app.services import learning_progress_service as progress
from classroom_app.services import scheduled_task_handlers as handlers, scheduled_task_service as scheduler
from tests import test_learning_progress_snapshots as support


class WeeklySnapshotTransactionsTests(unittest.TestCase):
    def setUp(self):
        support.LearningProgressSnapshotTests.setUp(self)
        self.addCleanup(self.conn.close)
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="weekly-worker-transactions-"))
        self.path = Path(self.directory) / "weekly.sqlite"
        self.enterContext(patch.object(progress, "get_configured_db_engine", return_value="sqlite"))
        self.enterContext(patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite"))
        with patch.object(schema_scheduler, "_SCHEMA_READY", False):
            schema_scheduler.ensure_scheduler_schema(self.conn)
        self.conn.executescript("""INSERT INTO class_offerings(id,class_id,teacher_id) VALUES(1,10,30),(2,10,30);
            INSERT INTO students(id,class_id,name,student_id_number,enrollment_status) VALUES
            (1,10,'Synthetic one','S1','active'),(2,10,'Synthetic two','S2','active'),
            (3,11,'Synthetic linked','S3','active'),(4,10,'Synthetic inactive','S4','inactive');
            INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id) VALUES(1,10,30),(1,11,30);
            """)
        for offering, student in ((1, 1), (1, 2), (1, 3), (2, 1), (2, 2)):
            progress.mark_student_learning_progress_dirty(self.conn, offering, student)
        self.conn.commit()
        target = sqlite3.connect(self.path)
        try:
            self.conn.backup(target)
        finally:
            target.close()
        self.enterContext(patch.object(progress, "get_db_connection", side_effect=self.connection))
        self.enterContext(patch.object(handlers, "get_db_connection", side_effect=self.connection))
        self.enterContext(patch.object(scheduler, "get_db_connection", side_effect=self.connection))
        self.enterContext(patch.object(progress, "_build_learning_metrics", return_value=support._metrics(20, material=20)))
        self.enterContext(patch.object(progress, "now_iso", return_value="2026-11-08T23:59:50"))

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def rows(self, table):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM " + table + " ORDER BY id")]

    def task(self, *, payload=None, owner="owned-claim", locked_at="2026-11-08T23:59:40"):
        with self.connection() as conn:
            conn.execute("""INSERT INTO scheduled_tasks(id,task_kind,status,run_at,locked_at,locked_by,payload_json,
                recurrence_seconds,max_attempts) VALUES(70,?,'running',?,?,?, ?,604800,1)""",
                (progress.CULTIVATION_WEEKLY_SNAPSHOT_TASK_KIND, locked_at, locked_at, owner,
                 json.dumps(payload or {"class_offering_id": 1, "refresh_current": True, "unrelated": {"keep": True}})))
            return dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=70").fetchone())

    def reclaim(self, owner):
        with self.connection() as conn:
            conn.execute("UPDATE scheduled_tasks SET status='running',locked_by=?,locked_at=? WHERE id=70",
                         (owner, owner + "-time"))
            return dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=70").fetchone())

    def test_caller_capture_retains_whole_transaction_and_rollback(self):
        with self.connection() as conn:
            result = progress.capture_cultivation_weekly_snapshots(conn, week_start="2026-11-08")
            self.assertEqual(5, result["captured"])
            self.assertEqual(5, conn.execute("SELECT COUNT(*) FROM cultivation_weekly_snapshots").fetchone()[0])
            conn.rollback()
        for table in ("cultivation_weekly_snapshots", "cultivation_score_events", "learning_stage_status"):
            self.assertEqual([], self.rows(table))

    def test_job_matches_caller_capture_all_columns_and_does_not_duplicate_memberships(self):
        expected = progress.capture_cultivation_weekly_snapshots(self.conn, week_start="2026-11-08")
        actual = progress.capture_cultivation_weekly_snapshots_job(week_start="2026-11-08",
                                                                 captured_at="2026-11-08T23:59:50")
        self.assertEqual(expected, actual)
        self.assertEqual(5, actual["captured"])
        for table in ("learning_progress_snapshots", "learning_stage_status", "cultivation_score_events",
                      "cultivation_weekly_snapshots"):
            old = [dict(row) for row in self.conn.execute("SELECT * FROM " + table + " ORDER BY id")]
            self.assertEqual(old, self.rows(table), table)

    def test_failed_actor_rolls_back_all_effects_while_committed_prefix_replays_without_extra_score(self):
        original = progress._capture_weekly_snapshot_actor
        def fail_after_real_effects(conn, offering, student, **kwargs):
            result = original(conn, offering, student, **kwargs)
            if student == 2:
                raise RuntimeError("Synthetic actor failure after real stage/event/weekly writes")
            return result
        with patch.object(progress, "_capture_weekly_snapshot_actor", side_effect=fail_after_real_effects):
            with self.assertRaisesRegex(RuntimeError, "Synthetic actor"):
                progress.capture_cultivation_weekly_snapshots_job(week_start="2026-11-08")
        self.assertEqual([1], [row["student_id"] for row in self.rows("cultivation_weekly_snapshots")])
        self.assertEqual([1], [row["student_id"] for row in self.rows("cultivation_score_events")])
        self.assertEqual({1}, {row["student_id"] for row in self.rows("learning_stage_status")})
        progress.capture_cultivation_weekly_snapshots_job(week_start="2026-11-08")
        self.assertEqual(5, len(self.rows("cultivation_weekly_snapshots")))
        self.assertEqual(5, len(self.rows("cultivation_score_events")))
        # Existing same-week rows must still update after a real score change.
        with self.connection() as conn:
            progress.mark_student_learning_progress_dirty(conn, 1, 1)
        with patch.object(progress, "_build_learning_metrics", return_value=support._metrics(25, material=25)):
            progress.capture_cultivation_weekly_snapshots_job(week_start="2026-11-08")
        self.assertEqual(5, len(self.rows("cultivation_weekly_snapshots")))
        row = next(row for row in self.rows("cultivation_weekly_snapshots")
                   if row["class_offering_id"] == 1 and row["student_id"] == 1)
        self.assertEqual(25, row["score"])
        self.assertEqual([20, 5], [row["delta"] for row in self.rows("cultivation_score_events")
                                 if row["class_offering_id"] == 1 and row["student_id"] == 1])

    def test_cross_week_final_failure_rearm_and_success_next_recurrence_keep_the_right_window(self):
        current = {"date": datetime(2026, 11, 8, 23, 59, 50)}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return current["date"]
        task = self.task(payload={"class_offering_id": 1, "refresh_current": True,
                                  "week_start": "   ", "unrelated": {"keep": True}})
        with patch.object(progress, "datetime", Clock), patch.object(progress, "now_iso", side_effect=lambda: current["date"].isoformat()):
            first, _ = handlers._weekly_capture_execution(task)
            self.assertEqual("2026-11-02", first["week_start"])
            scheduler._mark_failure(task, "synthetic final failure")
            current["date"] = datetime(2026, 11, 9, 0, 1)
            resumed = self.reclaim("new-claim")
            second, _ = handlers._weekly_capture_execution(resumed)
            self.assertEqual(first, second)
            result = handlers.handle_cultivation_weekly_snapshot(resumed)
            self.assertLess(len(result), 240)
            self.assertEqual("", self.rows("scheduled_tasks")[0]["last_result"], "Only the scheduler finalizes success")
            scheduler._mark_success(resumed, result)
            current["date"] = datetime(2026, 11, 16, 0, 1)
            next_cycle = self.reclaim("next-recurrence")
            third, _ = handlers._weekly_capture_execution(next_cycle)
            self.assertEqual("2026-11-16", third["week_start"])
            self.assertNotEqual(first["token"], third["token"])
            stored = json.loads(self.rows("scheduled_tasks")[0]["payload_json"])
            self.assertEqual({"keep": True}, stored["unrelated"])

    def test_stale_claim_cannot_bind_or_write_an_actor_after_binding(self):
        task = self.task()
        replacement = self.reclaim("replacement")
        with self.assertRaisesRegex(RuntimeError, "claim"):
            handlers.handle_cultivation_weekly_snapshot(task)
        original = handlers.capture_cultivation_weekly_snapshots_job
        def changed_before_actor(**kwargs):
            self.reclaim("operator-change")
            return original(**kwargs)
        with patch.object(handlers, "capture_cultivation_weekly_snapshots_job", side_effect=changed_before_actor):
            with self.assertRaisesRegex(RuntimeError, "claim"):
                handlers.handle_cultivation_weekly_snapshot(replacement)
        self.assertEqual([], self.rows("cultivation_weekly_snapshots"))
        self.assertEqual([], self.rows("cultivation_score_events"))

    def test_explicit_historical_week_and_unknown_success_ack_do_not_invent_another_cycle(self):
        task = self.task(payload={"class_offering_id": 1, "week_start": "2026-06-10", "refresh_current": True})
        result = handlers.handle_cultivation_weekly_snapshot(task)
        execution = json.loads(self.rows("scheduled_tasks")[0]["payload_json"])[handlers._WEEKLY_CAPTURE_EXECUTION]
        self.assertEqual("2026-06-08", execution["week_start"])
        real_connection = self.connection
        @contextmanager
        def success_ack_lost():
            with real_connection() as conn:
                yield conn
            raise ConnectionError("Synthetic lost scheduler commit ACK")
        with patch.object(scheduler, "get_db_connection", side_effect=success_ack_lost):
            with self.assertRaises(ConnectionError):
                scheduler._mark_success(task, result)
        next_task = self.reclaim("after-real-success")
        following, _ = handlers._weekly_capture_execution(next_task)
        self.assertEqual("2026-06-08", following["week_start"])
        self.assertNotEqual(execution["token"], following["token"])


if __name__ == "__main__":
    unittest.main()
