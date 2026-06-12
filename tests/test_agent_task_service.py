import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services.agent_task_service import (
    _estimate_wait_seconds,
    _claim_next_agent_task_postgres,
    _claim_next_agent_task_sqlite,
    claim_next_agent_task,
    get_agent_task,
    get_agent_queue_state,
    list_agent_tasks,
)


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _RowsCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

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


class _FakePostgresAgentConnection:
    def __init__(self, *, lock_acquired=True, claimed=True):
        self.lock_acquired = lock_acquired
        self.claimed = claimed
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if "pg_try_advisory_xact_lock" in normalized:
            return _RowsCursor({"acquired": self.lock_acquired})
        if normalized.startswith("WITH candidate AS"):
            if not self.claimed:
                return _RowsCursor(None)
            return _RowsCursor({"id": 11, "status": "running", "worker_id": "agent-worker-1"})
        if normalized.startswith("INSERT INTO agent_task_events"):
            return _RowsCursor(rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1


class AgentTaskServiceTests(unittest.TestCase):
    def _sqlite_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE agent_tasks (
                id INTEGER PRIMARY KEY,
                status TEXT,
                priority INTEGER DEFAULT 0,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT,
                worker_id TEXT
            );
            CREATE TABLE agent_task_events (
                id INTEGER PRIMARY KEY,
                task_id INTEGER,
                event_type TEXT,
                message TEXT,
                detail_json TEXT,
                created_at TEXT
            );
            INSERT INTO agent_tasks (id, status, priority, created_at, updated_at)
            VALUES
                (1, 'queued', 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00'),
                (2, 'queued', 5, '2026-01-01T00:01:00', '2026-01-01T00:01:00');
            """
        )
        return conn

    def _sqlite_queue_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE agent_tasks (
                id INTEGER PRIMARY KEY,
                task_uuid TEXT DEFAULT '',
                teacher_id INTEGER,
                teacher_name TEXT DEFAULT 'Teacher',
                task_type TEXT DEFAULT 'general_teaching_task',
                title TEXT DEFAULT 'Task',
                public_summary TEXT DEFAULT '教学事务',
                private_instruction TEXT DEFAULT '',
                context_snapshot_json TEXT DEFAULT '{}',
                result_detail_json TEXT DEFAULT '{}',
                attachments_json TEXT DEFAULT '[]',
                status TEXT,
                priority INTEGER DEFAULT 0,
                parent_task_id INTEGER,
                origin TEXT DEFAULT 'manual',
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT,
                worker_id TEXT,
                runtime_provider TEXT,
                runtime_status TEXT,
                runtime_task_id TEXT,
                runtime_thread_id TEXT,
                runtime_turn_id TEXT,
                result_summary TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0
            );
            CREATE TABLE agent_task_events (
                id INTEGER PRIMARY KEY,
                task_id INTEGER,
                event_type TEXT,
                message TEXT,
                detail_json TEXT,
                created_at TEXT
            );
            CREATE TABLE agent_task_composers (
                teacher_id INTEGER PRIMARY KEY,
                teacher_name TEXT,
                page_label TEXT,
                updated_at TEXT
            );
            """
        )
        return conn

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

    def test_sqlite_agent_claim_preserves_single_running_behavior(self):
        conn = self._sqlite_conn()
        try:
            with patch("classroom_app.config.AGENT_TASK_GLOBAL_CONCURRENCY", 1):
                claimed = _claim_next_agent_task_sqlite(conn, worker_id="agent-worker-1", now="2026-01-01T00:10:00")

                self.assertEqual(2, claimed["id"])
                self.assertEqual("running", claimed["status"])
                self.assertEqual("agent-worker-1", claimed["worker_id"])
                running_count = conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE status = 'running'").fetchone()[0]
                event_count = conn.execute("SELECT COUNT(*) FROM agent_task_events WHERE task_id = 2").fetchone()[0]
                self.assertEqual(1, running_count)
                self.assertEqual(1, event_count)

                second_claim = _claim_next_agent_task_sqlite(conn, worker_id="agent-worker-2", now="2026-01-01T00:11:00")
                self.assertIsNone(second_claim)
        finally:
            conn.close()

    def test_queue_positions_match_priority_and_teacher_fairness(self):
        conn = self._sqlite_queue_conn()
        try:
            conn.executemany(
                """
                INSERT INTO agent_tasks (
                    id, teacher_id, teacher_name, status, priority, created_at, updated_at,
                    started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, 7, "Teacher 7", "queued", 0, "2026-01-01T00:00:00", "2026-01-01T00:00:00", None, None),
                    (2, 7, "Teacher 7", "queued", 0, "2026-01-01T00:01:00", "2026-01-01T00:01:00", None, None),
                    (3, 8, "Teacher 8", "queued", 0, "2026-01-01T00:02:00", "2026-01-01T00:02:00", None, None),
                    (4, 9, "Teacher 9", "queued", 2, "2026-01-01T00:03:00", "2026-01-01T00:03:00", None, None),
                    (
                        5,
                        10,
                        "Teacher 10",
                        "completed",
                        0,
                        "2026-01-01T00:04:00",
                        "2026-01-01T00:08:00",
                        "2026-01-01T00:04:00",
                        "2026-01-01T00:08:00",
                    ),
                ],
            )
            conn.commit()

            with patch("classroom_app.config.AGENT_TASK_GLOBAL_CONCURRENCY", 1):
                payload = list_agent_tasks(conn, viewer_teacher_id=7, limit=20)
                positions = {int(task["id"]): int(task["queue_position"]) for task in payload["tasks"]}

                self.assertEqual({1: 2, 2: 4, 3: 3, 4: 1}, {key: positions[key] for key in (1, 2, 3, 4)})
                self.assertEqual("预计 5 分钟内开始处理", get_agent_task(conn, 4, teacher_id=9)["estimated_wait_label"])
                self.assertEqual("预计需要 5~15 分钟", get_agent_task(conn, 2, teacher_id=7)["estimated_wait_label"])
        finally:
            conn.close()

    def test_wait_estimate_accounts_for_available_global_slots(self):
        self.assertEqual(
            0,
            _estimate_wait_seconds(240, 2, running_count=0, global_concurrency=2),
        )
        self.assertEqual(
            240,
            _estimate_wait_seconds(240, 3, running_count=0, global_concurrency=2),
        )
        self.assertEqual(
            240,
            _estimate_wait_seconds(240, 2, running_count=1, global_concurrency=2),
        )
        self.assertEqual(
            720,
            _estimate_wait_seconds(240, 4, running_count=0, global_concurrency=1),
        )
        self.assertEqual(
            960,
            _estimate_wait_seconds(240, 4, running_count=1, global_concurrency=1),
        )

    def test_sqlite_agent_claim_allows_global_concurrency_but_keeps_one_per_teacher(self):
        conn = self._sqlite_queue_conn()
        try:
            conn.executemany(
                """
                INSERT INTO agent_tasks (id, teacher_id, teacher_name, status, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, 7, "Teacher 7", "running", 0, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
                    (2, 7, "Teacher 7", "queued", 0, "2026-01-01T00:01:00", "2026-01-01T00:01:00"),
                    (3, 8, "Teacher 8", "queued", 0, "2026-01-01T00:02:00", "2026-01-01T00:02:00"),
                ],
            )
            conn.commit()

            with patch("classroom_app.config.AGENT_TASK_GLOBAL_CONCURRENCY", 2):
                state = get_agent_queue_state(conn, viewer_teacher_id=7)
                claimed = _claim_next_agent_task_sqlite(
                    conn,
                    worker_id="agent-worker-2",
                    now="2026-01-01T00:10:00",
                )
                second_claim = _claim_next_agent_task_sqlite(
                    conn,
                    worker_id="agent-worker-3",
                    now="2026-01-01T00:11:00",
                )

            self.assertEqual(1, state["running_count"])
            self.assertEqual(2, state["global_concurrency"])
            self.assertEqual(1, state["available_slots"])
            self.assertEqual(3, claimed["id"])
            self.assertIsNone(second_claim)
            self.assertEqual(
                2,
                conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE status = 'running'").fetchone()[0],
            )
            self.assertEqual(
                "queued",
                conn.execute("SELECT status FROM agent_tasks WHERE id = 2").fetchone()["status"],
            )
        finally:
            conn.close()

    def test_postgres_agent_claim_uses_advisory_lock_skip_locked_and_returning(self):
        conn = _FakePostgresAgentConnection()

        with patch("classroom_app.config.AGENT_TASK_GLOBAL_CONCURRENCY", 2):
            claimed = _claim_next_agent_task_postgres(conn, worker_id="agent-worker-1", now="2026-01-01T00:10:00")

        self.assertEqual({"id": 11, "status": "running", "worker_id": "agent-worker-1"}, claimed)
        self.assertEqual(1, conn.commits)
        sql_text = "\n".join(call[0] for call in conn.calls)
        self.assertIn("pg_try_advisory_xact_lock", sql_text)
        self.assertIn("FOR UPDATE SKIP LOCKED", sql_text)
        self.assertIn("NOT EXISTS", sql_text)
        self.assertIn("RETURNING agent_tasks.*", sql_text)
        self.assertNotIn("BEGIN IMMEDIATE", sql_text)
        self.assertEqual(2, conn.calls[1][1][-1])

    def test_postgres_agent_claim_returns_none_when_singleton_lock_is_busy(self):
        conn = _FakePostgresAgentConnection(lock_acquired=False)

        self.assertIsNone(_claim_next_agent_task_postgres(conn, worker_id="agent-worker-1", now="2026-01-01T00:10:00"))
        self.assertEqual(1, conn.commits)
        self.assertEqual(1, len(conn.calls))

    def test_agent_claim_fails_fast_for_unknown_engine(self):
        conn = self._sqlite_conn()
        try:
            original = claim_next_agent_task.__globals__["get_configured_db_engine"]
            claim_next_agent_task.__globals__["get_configured_db_engine"] = lambda: "mysql"
            with self.assertRaises(ValueError):
                claim_next_agent_task(conn, worker_id="agent-worker-1")
        finally:
            claim_next_agent_task.__globals__["get_configured_db_engine"] = original
            conn.close()


if __name__ == "__main__":
    unittest.main()
