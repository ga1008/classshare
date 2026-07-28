import asyncio
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services.smart_classroom_checkin_sync_service import (
    ORDINARY_GRADE_ATTENDANCE_CACHE_SECONDS,
    get_classroom_smart_attendance_freshness,
    sync_teacher_smart_classroom_checkins,
)


class SmartClassroomAttendanceFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE smart_classroom_schedule_items (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                class_offering_id INTEGER,
                synced_at TEXT
            );
            CREATE TABLE smart_classroom_checkin_sessions (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                class_offering_id INTEGER,
                session_id INTEGER,
                synced_at TEXT
            );
            CREATE TABLE smart_classroom_checkin_students (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                class_offering_id INTEGER,
                student_id INTEGER
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _seed(self, synced_at: str) -> None:
        self.conn.execute(
            "INSERT INTO smart_classroom_schedule_items VALUES (1, 98765, 30, ?)",
            (synced_at,),
        )
        self.conn.execute(
            "INSERT INTO smart_classroom_checkin_sessions VALUES (2, 98765, 30, 300, ?)",
            (synced_at,),
        )
        self.conn.execute(
            "INSERT INTO smart_classroom_checkin_students VALUES (3, 98765, 30, 101)"
        )
        self.conn.commit()

    def test_freshness_uses_thirty_minute_window(self):
        self._seed(datetime.now().isoformat(timespec="seconds"))
        result = get_classroom_smart_attendance_freshness(
            self.conn,
            teacher_id=98765,
            class_offering_id=30,
        )
        self.assertTrue(result["is_fresh"])
        self.assertEqual(result["session_count"], 1)
        self.assertEqual(result["student_count"], 1)
        self.assertEqual(result["max_age_seconds"], ORDINARY_GRADE_ATTENDANCE_CACHE_SECONDS)

    def test_stale_attendance_is_not_treated_as_cache_hit(self):
        self._seed((datetime.now() - timedelta(minutes=31)).isoformat(timespec="seconds"))
        result = get_classroom_smart_attendance_freshness(
            self.conn,
            teacher_id=98765,
            class_offering_id=30,
        )
        self.assertFalse(result["is_fresh"])
        self.assertGreaterEqual(result["age_seconds"], 31 * 60)

    def test_recent_timetable_refresh_does_not_hide_stale_attendance(self):
        self._seed((datetime.now() - timedelta(minutes=31)).isoformat(timespec="seconds"))
        self.conn.execute(
            """
            UPDATE smart_classroom_schedule_items
            SET synced_at = ?
            WHERE teacher_id = 98765 AND class_offering_id = 30
            """,
            ((datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds"),),
        )
        self.conn.commit()

        result = get_classroom_smart_attendance_freshness(
            self.conn,
            teacher_id=98765,
            class_offering_id=30,
        )

        self.assertFalse(result["is_fresh"])
        self.assertGreaterEqual(result["age_seconds"], 31 * 60)

    def test_sync_short_circuits_before_remote_login_when_cache_is_fresh(self):
        self._seed(datetime.now().isoformat(timespec="seconds"))
        with patch(
            "classroom_app.services.smart_classroom_checkin_sync_service.get_db_connection",
            return_value=self.conn,
        ):
            result = asyncio.run(
                sync_teacher_smart_classroom_checkins(
                    98765,
                    class_offering_id=30,
                    min_refresh_interval_seconds=ORDINARY_GRADE_ATTENDANCE_CACHE_SECONDS,
                )
            )
        self.assertEqual(result["status"], "cached")
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["counts"]["checkin_count"], 1)


if __name__ == "__main__":
    unittest.main()
