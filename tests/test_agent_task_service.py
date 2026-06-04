import sqlite3
import unittest

from classroom_app.services.agent_task_service import get_agent_queue_state


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _ReadMostlyConnection:
    def __init__(self):
        self.rollback_called = False
        self.composer_query = ""
        self.composer_params = ()

    def rollback(self):
        self.rollback_called = True

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("DELETE FROM agent_task_composers"):
            raise sqlite3.OperationalError("attempt to write a readonly database")
        if "SELECT COUNT(*) FROM agent_tasks WHERE status" in normalized:
            return _Cursor((0,))
        if "FROM agent_tasks" in normalized and "WHERE status" in normalized:
            return _Cursor(None)
        if "FROM agent_task_composers" in normalized:
            self.composer_query = normalized
            self.composer_params = params
            return _Cursor(None)
        raise AssertionError(f"Unexpected SQL: {normalized}")


class AgentTaskServiceTests(unittest.TestCase):
    def test_queue_state_tolerates_stale_composer_cleanup_failure_on_read(self):
        conn = _ReadMostlyConnection()

        state = get_agent_queue_state(conn, viewer_teacher_id=42)

        self.assertTrue(conn.rollback_called)
        self.assertEqual(0, state["queued_count"])
        self.assertIsNone(state["running"])
        self.assertIsNone(state["composer"])
        self.assertFalse(state["is_running"])
        self.assertFalse(state["is_composing"])
        self.assertIn("updated_at >=", conn.composer_query)
        self.assertEqual(42, conn.composer_params[0])


if __name__ == "__main__":
    unittest.main()
