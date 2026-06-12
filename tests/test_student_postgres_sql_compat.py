from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.services import learning_progress_service, message_center_service, profile_service


class _RecordingConnection:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.executed.append((str(sql), tuple(params)))
        return self

    def fetchone(self):
        return None


class StudentPostgresSqlCompatTests(unittest.TestCase):
    def test_profile_today_condition_uses_postgres_date_cast(self):
        with patch.object(profile_service, "get_configured_db_engine", return_value="postgres"):
            condition = profile_service._today_date_condition("logs.logged_at")

        self.assertEqual("logs.logged_at::date = CURRENT_DATE", condition)

    def test_message_center_today_condition_uses_postgres_date_cast(self):
        with patch.object(message_center_service, "get_configured_db_engine", return_value="postgres"):
            condition = message_center_service._created_today_condition("created_at")

        self.assertEqual("created_at::date = CURRENT_DATE", condition)

    def test_material_progress_upsert_uses_postgres_interval(self):
        conn = _RecordingConnection()
        with patch.object(learning_progress_service, "get_configured_db_engine", return_value="postgres"), patch.object(
            learning_progress_service,
            "refresh_student_learning_state",
            return_value={"score": 0, "progress_percent": 0, "eligible_stage": None},
        ):
            learning_progress_service.record_material_learning_progress(
                conn,
                class_offering_id=1,
                student_id=2,
                material_id=3,
                duration_seconds=30,
                active_seconds=20,
                scroll_ratio=0.4,
            )

        sql = next(item[0] for item in conn.executed if "INSERT INTO learning_material_progress" in item[0])
        self.assertIn("INTERVAL '1800 seconds'", sql)
        self.assertNotIn("julianday(", sql)


if __name__ == "__main__":
    unittest.main()
