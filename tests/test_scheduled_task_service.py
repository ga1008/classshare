import asyncio
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services import scheduled_task_service as sts


def _run(coro):
    return asyncio.run(coro)


class ScheduledTaskServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

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


if __name__ == "__main__":
    unittest.main()
