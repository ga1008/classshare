import asyncio
import contextlib
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers.manage_parts import common, system_config


class BackgroundTaskPermissionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    @contextlib.contextmanager
    def _conn_context(self):
        yield self.conn

    def test_super_admin_can_read_background_task_ledger(self):
        with patch.object(system_config, "get_db_connection", return_value=self._conn_context()), patch.object(
            common,
            "is_super_admin_teacher",
            return_value=True,
        ):
            payload = asyncio.run(system_config.api_get_background_tasks(user={"id": 1, "role": "teacher"}))

        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(payload["items"]), 8)

    def test_regular_teacher_cannot_read_background_task_ledger(self):
        with patch.object(system_config, "get_db_connection", return_value=self._conn_context()), patch.object(
            common,
            "is_super_admin_teacher",
            return_value=False,
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(system_config.api_get_background_tasks(user={"id": 1, "role": "teacher"}))

        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
