import asyncio
import unittest
import threading
from unittest.mock import patch
from datetime import datetime, timedelta

from classroom_app.database import get_db_connection
from tests.sqlite_database_fixture import isolated_sqlite_database
from classroom_app.services import scheduled_task_service as sts
from classroom_app.services import learning_progress_service as progress


def _run(coro):
    return asyncio.run(coro)


class ScheduledTaskServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())

    def setUp(self):
        # Isolate each test by clearing scheduler rows.
        with get_db_connection() as conn:
            sts.ensure_scheduler_schema(conn)
            conn.execute("DELETE FROM scheduled_tasks")
            conn.commit()
        sts._HANDLERS.pop("unit_test_kind", None)

    def test_schedule_claim_and_complete_one_shot(self):
        seen = []
        sts.register_task_handler("unit_test_kind", lambda task: seen.append(task["payload"]) or "ok")
        with get_db_connection() as conn:
            task_id = sts.schedule_task(
                conn,
                task_kind="unit_test_kind",
                run_at=datetime.now() - timedelta(minutes=1),
                payload={"n": 1},
                dedupe_key="unit:1",
            )
            conn.commit()
        result = _run(sts.process_due_scheduled_tasks_once())
        self.assertEqual(result["claimed"], 1)
        self.assertEqual(result["done"], 1)
        self.assertEqual(seen, [{"n": 1}])
        with get_db_connection() as conn:
            row = conn.execute("SELECT status FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        self.assertEqual(row["status"], "done")

    def test_future_task_not_claimed(self):
        sts.register_task_handler("unit_test_kind", lambda task: "ok")
        with get_db_connection() as conn:
            sts.schedule_task(
                conn,
                task_kind="unit_test_kind",
                run_at=datetime.now() + timedelta(hours=2),
                dedupe_key="unit:future",
            )
            conn.commit()
        result = _run(sts.process_due_scheduled_tasks_once())
        self.assertEqual(result["claimed"], 0)

    def test_dedupe_replace_updates_run_at(self):
        sts.register_task_handler("unit_test_kind", lambda task: "ok")
        with get_db_connection() as conn:
            first = sts.schedule_task(
                conn, task_kind="unit_test_kind",
                run_at=datetime.now() + timedelta(hours=1), dedupe_key="unit:dup",
            )
            second = sts.schedule_task(
                conn, task_kind="unit_test_kind",
                run_at=datetime.now() + timedelta(hours=5), dedupe_key="unit:dup",
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM scheduled_tasks WHERE dedupe_key = 'unit:dup'"
            ).fetchone()["c"]
        self.assertEqual(first, second)
        self.assertEqual(count, 1)

    def test_recurring_task_reschedules(self):
        sts.register_task_handler("unit_test_kind", lambda task: "tick")
        with get_db_connection() as conn:
            task_id = sts.schedule_task(
                conn, task_kind="unit_test_kind",
                run_at=datetime.now() - timedelta(minutes=1),
                recurrence_seconds=3600, dedupe_key="unit:recurring",
            )
            conn.commit()
        result = _run(sts.process_due_scheduled_tasks_once())
        self.assertEqual(result["rescheduled"], 1)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT status, run_at FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertGreater(datetime.fromisoformat(row["run_at"]), datetime.now())

    def test_failure_retries_then_fails(self):
        def boom(task):
            raise RuntimeError("nope")

        sts.register_task_handler("unit_test_kind", boom)
        with get_db_connection() as conn:
            task_id = sts.schedule_task(
                conn, task_kind="unit_test_kind",
                run_at=datetime.now() - timedelta(minutes=1),
                dedupe_key="unit:fail", max_attempts=1,
            )
            conn.commit()
        result = _run(sts.process_due_scheduled_tasks_once())
        self.assertEqual(result["failed"], 1)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT status, last_error FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("nope", row["last_error"])

    def test_late_completion_and_failure_cannot_resurrect_canceled_subscription(self):
        with get_db_connection() as conn:
            task_id = sts.schedule_task(conn, task_kind="unit_test_kind", run_at=datetime.now() - timedelta(minutes=1),
                                        recurrence_seconds=3600, dedupe_key="unit:cancel")
            conn.commit()
        claimed = sts._claim_due_tasks(1)[0]
        with get_db_connection() as conn:
            sts.cancel_tasks_by_dedupe(conn, "unit:cancel")
            conn.commit()
        sts._mark_success(claimed, "late success")
        sts._mark_failure(claimed, "late failure")
        with get_db_connection() as conn:
            row = conn.execute("SELECT status,last_result,last_error FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual("cancelled", row["status"])
        self.assertNotEqual("late success", row["last_result"])
        self.assertNotEqual("late failure", row["last_error"])

    def test_old_claim_cannot_finalize_a_new_attempt_or_replaced_schedule(self):
        with get_db_connection() as conn:
            task_id = sts.schedule_task(conn, task_kind="unit_test_kind", run_at=datetime.now() - timedelta(minutes=1), dedupe_key="unit:fence")
            conn.commit()
        old = sts._claim_due_tasks(1)[0]
        with get_db_connection() as conn:
            sts.schedule_task(conn, task_kind="unit_test_kind", run_at=datetime.now() - timedelta(minutes=1), dedupe_key="unit:fence")
            conn.commit()
        current = sts._claim_due_tasks(1)[0]
        self.assertNotEqual(old["locked_by"], current["locked_by"])
        sts._mark_success(old, "old")
        with get_db_connection() as conn:
            self.assertEqual("running", conn.execute("SELECT status FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()[0])
        sts._mark_success(current, "current")
        with get_db_connection() as conn:
            self.assertEqual("current", conn.execute("SELECT last_result FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()[0])

    def test_sync_handler_does_not_block_deadline_lane_or_preclaim_next_work(self):
        started, release = threading.Event(), threading.Event()
        def slow(task):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test did not release handler")
            return "slow complete"
        sts.register_task_handler("unit_test_slow", slow)
        sts.register_task_handler("todo_due_reminder", lambda task: "deadline complete")
        with get_db_connection() as conn:
            for kind, priority in (("unit_test_slow", 120), ("unit_test_slow", 120), ("todo_due_reminder", 100)):
                sts.schedule_task(conn, task_kind=kind, priority=priority, run_at=datetime.now()-timedelta(seconds=10))
        async def check():
            elastic = asyncio.create_task(sts.process_due_scheduled_tasks_once(limit=1, lane="elastic"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                deadline = await sts.process_due_scheduled_tasks_once(limit=1, lane="deadline")
                self.assertEqual(1, deadline["done"])
                self.assertFalse(elastic.done())
                with get_db_connection() as conn:
                    statuses = [row[0] for row in conn.execute("SELECT status FROM scheduled_tasks WHERE task_kind='unit_test_slow' ORDER BY id")]
                self.assertEqual(["running", "pending"], statuses)
            finally:
                release.set()
                await elastic
        _run(check())

    def test_long_handler_lease_renews_and_cancellation_waits_for_thread(self):
        started, release = threading.Event(), threading.Event()
        def slow(task):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test did not release handler")
            return "completed once"
        sts.register_task_handler("unit_test_kind", slow)
        with get_db_connection() as conn:
            task_id = sts.schedule_task(conn, task_kind="unit_test_kind", run_at=datetime.now()-timedelta(seconds=10))
        async def check():
            operation = asyncio.create_task(sts.process_due_scheduled_tasks_once(limit=1))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                with get_db_connection() as conn:
                    claim = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
                future = datetime.now()+timedelta(minutes=2)
                with patch.object(sts, "_now", return_value=future):
                    await asyncio.sleep(0.1)
                    self.assertEqual([], await asyncio.to_thread(sts._claim_due_tasks, 1))
                    with get_db_connection() as conn:
                        renewed = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
                    self.assertEqual(claim["locked_at"], renewed["locked_at"])
                    self.assertGreater(renewed["updated_at"], claim["updated_at"])
                    operation.cancel()
                    await asyncio.sleep(0.06)
                    self.assertFalse(operation.done(), "a live sync handler must retain its slot and lease")
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await operation
            finally:
                release.set()
        with patch.object(sts, "SCHEDULER_STALE_MINUTES", 1), patch.object(sts, "SCHEDULER_LEASE_HEARTBEAT_SECONDS", 0.02):
            _run(check())
        with get_db_connection() as conn:
            row = conn.execute("SELECT status,last_result FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual(("done", "completed once"), tuple(row))

    def test_dead_attendance_scan_reclaimed_without_shortening_other_job_leases(self):
        clock = datetime.now().replace(microsecond=0)
        with get_db_connection() as conn:
            scan_id = sts.schedule_task(conn, task_kind="login_attendance_scan", run_at=clock-timedelta(seconds=1))
            other_id = sts.schedule_task(conn, task_kind="unit_test_kind", run_at=clock-timedelta(seconds=1))
        with patch.object(sts, "_now", return_value=clock):
            old = {task["id"]: task for task in sts._claim_due_tasks(2)}
        with patch.object(sts, "_now", return_value=clock+timedelta(seconds=89)):
            self.assertEqual([], sts._claim_due_tasks(2))
        with patch.object(sts, "_now", return_value=clock+timedelta(seconds=91)):
            recovered = sts._claim_due_tasks(2)
            self.assertEqual([scan_id], [task["id"] for task in recovered])
            self.assertNotEqual(old[scan_id]["locked_by"], recovered[0]["locked_by"])
            self.assertFalse(sts._renew_task_lease(old[scan_id]))
            sts._mark_success(old[scan_id], "stale success")
            sts._mark_failure(old[scan_id], "stale failure")
            sts._mark_success(recovered[0], "recovered")
        with get_db_connection() as conn:
            scan = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (scan_id,)).fetchone())
            other = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (other_id,)).fetchone())
        self.assertEqual(("done", "recovered", ""), (scan["status"], scan["last_result"], scan["last_error"]))
        self.assertEqual(("running", old[other_id]["locked_by"]), (other["status"], other["locked_by"]))

    def test_renewed_attendance_scan_is_not_reclaimed_from_its_original_start_time(self):
        clock = datetime.now().replace(microsecond=0)
        with get_db_connection() as conn:
            sts.schedule_task(conn, task_kind="login_attendance_scan", run_at=clock-timedelta(seconds=1))
        with patch.object(sts, "_now", return_value=clock):
            claim = sts._claim_due_tasks(1, lane="deadline")[0]
        with patch.object(sts, "_now", return_value=clock+timedelta(seconds=60)):
            self.assertTrue(sts._renew_task_lease(claim))
        with patch.object(sts, "_now", return_value=clock+timedelta(seconds=91)):
            self.assertEqual([], sts._claim_due_tasks(1, lane="deadline"))
        with patch.object(sts, "_now", return_value=clock+timedelta(seconds=151)):
            self.assertEqual([claim["id"]], [task["id"] for task in sts._claim_due_tasks(1, lane="deadline")])

    def test_lanes_reserve_projection_and_old_waiters_age_ahead_of_new_priority(self):
        with get_db_connection() as conn:
            generation = sts.schedule_task(conn, task_kind="stage_exam_generation", priority=20, run_at=datetime.now()-timedelta(seconds=10))
            old = sts.schedule_task(conn, task_kind="unit_test_kind", priority=95, run_at=datetime.now()-timedelta(minutes=10))
            sts.schedule_task(conn, task_kind="unit_test_kind", priority=10, run_at=datetime.now()-timedelta(seconds=10))
        self.assertEqual(old, sts._claim_due_tasks(1, lane="normal")[0]["id"])
        self.assertEqual(generation, sts._claim_due_tasks(1, lane="elastic")[0]["id"])

    def test_projection_timer_recovers_only_its_own_exhausted_job(self):
        timestamp = datetime.now().replace(microsecond=0)
        old = (timestamp-timedelta(minutes=5)).isoformat()
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            other_id = sts.schedule_task(conn, task_kind="unit_test_kind", run_at=old, dedupe_key="unit:unrelated-failed")
            conn.execute("UPDATE scheduled_tasks SET status='failed',attempt_count=3,last_error='synthetic DB outage',finished_at=?,updated_at=?",
                         (old, old))
            progress.ensure_cultivation_snapshot_refresh_task(conn)
            recovered = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
            other = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (other_id,)).fetchone())
        self.assertEqual(("pending", 0, "synthetic DB outage"), (recovered["status"], recovered["attempt_count"], recovered["last_error"]))
        self.assertEqual(("failed", 3), (other["status"], other["attempt_count"]))

    def test_projection_timer_respects_cooldown_cancellation_and_running_claim(self):
        timestamp = datetime.now().replace(microsecond=0).isoformat()
        with get_db_connection() as conn:
            task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
            for status in ("failed", "cancelled", "running"):
                conn.execute("UPDATE scheduled_tasks SET status=?,attempt_count=3,finished_at=?,updated_at=?,locked_at=?,locked_by='immutable-owner' WHERE id=?",
                             (status, timestamp, timestamp, timestamp, task_id))
                before = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
                progress.ensure_cultivation_snapshot_refresh_task(conn)
                after = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
                for field in ("status", "attempt_count", "finished_at", "updated_at", "locked_at", "locked_by", "started_at"):
                    self.assertEqual(before[field], after[field], f"{status}: {field}")

    def test_projection_timer_does_not_repurpose_a_wrong_kind_with_its_key(self):
        with get_db_connection() as conn:
            task_id = sts.schedule_task(conn, task_kind="unit_test_kind", run_at="2000-01-01", dedupe_key="cultivation:snapshot-refresh")
            conn.execute("UPDATE scheduled_tasks SET status='failed',finished_at='2000-01-01' WHERE id=?", (task_id,))
            before = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
            progress.ensure_cultivation_snapshot_refresh_task(conn)
            self.assertEqual(before, dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()))

    def test_running_worker_recovers_exhausted_projection_without_web_restart(self):
        from classroom_app.services import scheduled_task_handlers  # Register real worker entrypoints.

        clock = [datetime.now().replace(microsecond=0)]
        calls = []
        def transient(_task):
            calls.append(clock[0])
            if len(calls) == 1:
                raise RuntimeError("synthetic transient database outage")
            return "projection recovered"
        with get_db_connection() as conn:
            task_id = sts.schedule_task(conn, task_kind=progress.CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND,
                dedupe_key="cultivation:snapshot-refresh", run_at=clock[0]-timedelta(seconds=1),
                recurrence_seconds=20, max_attempts=1, priority=70)
        sleep = asyncio.sleep
        async def short_sleep(delay):
            await sleep(min(float(delay), 0.01))
        async def check():
            async def tick():
                while True:
                    clock[0] += timedelta(seconds=1)
                    await sleep(0.01)
            worker = asyncio.create_task(sts.run_scheduler_worker_forever("unit-projection-recovery", poll_seconds=5))
            ticker = asyncio.create_task(tick())
            try:
                deadline = asyncio.get_running_loop().time()+5
                while asyncio.get_running_loop().time() < deadline:
                    with get_db_connection() as conn:
                        row = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
                    if row["last_result"] == "projection recovered" and row["status"] == "pending":
                        break
                    await sleep(0.015)
                else:
                    self.fail("the real running worker did not restore and execute its exhausted projection timer")
            finally:
                worker.cancel()
                ticker.cancel()
                await asyncio.gather(worker, ticker, return_exceptions=True)
        with patch.object(sts, "_now", side_effect=lambda: clock[0]), patch.object(progress, "datetime", wraps=datetime) as dates:
            dates.now.side_effect = lambda: clock[0]
            with patch.dict(sts._HANDLERS, {progress.CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND: transient}), patch.object(sts.asyncio, "sleep", short_sleep):
                _run(check())
        self.assertEqual(2, len(calls))
        self.assertGreaterEqual((calls[1]-calls[0]).total_seconds(), 60)


if __name__ == "__main__":
    unittest.main()
