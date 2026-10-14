"""Overdue assignment closing: read-only probe first, scheduler sweep (audit B7)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from tests.sqlite_database_fixture import isolated_sqlite_database
from classroom_app.database import get_db_connection
from classroom_app.services import assignment_lifecycle_service as lifecycle


def _iso(delta: timedelta) -> str:
    return (datetime.now() + delta).replace(microsecond=0).isoformat()


class AssignmentCloseSweepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        with get_db_connection() as conn:
            conn.execute("INSERT INTO teachers (id, name, email, hashed_password, is_active) VALUES (9801, 'T', 't9801@example.test', 'x', 1)")
            conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (9802, 'C', 9801)")
            conn.commit()

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM assignments")
            rows = [
                ("9803", "published", _iso(-timedelta(hours=1)), 0, None),   # overdue → closes
                ("9804", "published", _iso(timedelta(hours=1)), 0, None),    # still open
                ("9805", "published", _iso(-timedelta(hours=1)), 1, _iso(timedelta(hours=2))),  # in late window
            ]
            for aid, status, due, late, until in rows:
                conn.execute(
                    "INSERT INTO assignments (id, course_id, title, status, due_at, late_submission_enabled, "
                    "late_submission_until, created_at) VALUES (?, 9802, 'A', ?, ?, ?, ?, ?)",
                    (aid, status, due, late, until, datetime.now().isoformat()),
                )
            conn.commit()

    def _statuses(self):
        with get_db_connection() as conn:
            return {str(row["id"]): row["status"] for row in conn.execute("SELECT id, status FROM assignments")}

    def test_closes_only_overdue_and_second_call_is_a_no_op(self):
        with get_db_connection() as conn:
            self.assertEqual(lifecycle.close_overdue_assignments(conn), 1)
            conn.commit()
            self.assertEqual(lifecycle.close_overdue_assignments(conn), 0)
        self.assertEqual(self._statuses(), {"9803": "closed", "9804": "published", "9805": "published"})

    def test_sweep_task_handler_closes_and_is_registered(self):
        from classroom_app.services import scheduled_task_handlers  # noqa: F401  registers handlers
        from classroom_app.services.scheduled_task_service import _HANDLERS

        self.assertIn(lifecycle.TASK_KIND_ASSIGNMENT_CLOSE_SWEEP, _HANDLERS)
        self.assertEqual(lifecycle.handle_assignment_close_sweep_task({}), "closed=1")
        self.assertEqual(self._statuses()["9803"], "closed")


if __name__ == "__main__":
    unittest.main()
