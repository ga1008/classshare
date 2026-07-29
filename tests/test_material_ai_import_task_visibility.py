import asyncio
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.routers.materials_parts import ai_import


class MaterialAiImportTaskVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                parent_material_id INTEGER,
                parse_status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def _insert(self, record_id, *, status, updated_at, teacher_id=7):
        self.conn.execute(
            """
            INSERT INTO material_ai_import_records
                (id, teacher_id, parent_material_id, parse_status, updated_at)
            VALUES (?, ?, NULL, ?, ?)
            """,
            (record_id, teacher_id, status, updated_at),
        )
        self.conn.commit()

    def test_active_endpoint_hides_completed_background_records(self):
        now = datetime.now()
        recent = now.isoformat()
        stale = (now - timedelta(hours=2)).isoformat()
        self._insert(1, status="queued", updated_at=stale)
        self._insert(2, status="running", updated_at=stale)
        self._insert(3, status="completed", updated_at=recent)
        self._insert(4, status="failed", updated_at=recent)
        self._insert(5, status="quality_failed", updated_at=recent)
        self._insert(6, status="ai_failed", updated_at=stale)
        self._insert(7, status="unsupported", updated_at=recent, teacher_id=8)

        @contextmanager
        def connection():
            yield self.conn

        enqueued = []
        with (
            patch.object(ai_import, "get_db_connection", connection),
            patch.object(ai_import, "_recover_stale_material_ai_import_tasks", return_value=0),
            patch.object(ai_import, "_enqueue_material_ai_import_task", side_effect=lambda task_id: enqueued.append(task_id)),
            patch.object(ai_import, "_serialize_material_ai_import_task", side_effect=lambda _conn, row, _user: dict(row)),
        ):
            result = asyncio.run(
                ai_import.list_ai_import_records(
                    parent_id=None,
                    recent_minutes=30,
                    user={"id": 7},
                )
            )

        task_ids = [task["id"] for task in result["tasks"]]
        self.assertEqual(task_ids, [2, 1, 5, 4])
        self.assertNotIn(3, task_ids)
        self.assertNotIn(6, task_ids)
        self.assertNotIn(7, task_ids)
        self.assertEqual(enqueued, [1])


if __name__ == "__main__":
    unittest.main()
