import asyncio
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from classroom_app.services import academic_exam_roster_sync_service as service


class AcademicExamRosterCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        service._exam_roster_sync_locks.clear()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teacher_academic_exam_roster_items (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                class_offering_id INTEGER,
                sync_status TEXT,
                synced_at TEXT,
                exam_course_key TEXT
            );
            CREATE TABLE teacher_academic_exam_roster_students (
                id INTEGER PRIMARY KEY,
                exam_roster_item_id INTEGER,
                row_order INTEGER,
                student_number TEXT,
                student_name TEXT
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()
        service._exam_roster_sync_locks.clear()

    def _seed_roster(self, *, synced_at: str, course_key: str = "exam-1") -> None:
        self.conn.execute(
            """
            INSERT INTO teacher_academic_exam_roster_items
                (id, teacher_id, class_offering_id, sync_status, synced_at, exam_course_key)
            VALUES (1, 7, 30, 'active', ?, ?)
            """,
            (synced_at, course_key),
        )
        self.conn.execute(
            """
            INSERT INTO teacher_academic_exam_roster_students
                (id, exam_roster_item_id, row_order, student_number, student_name)
            VALUES (10, 1, 1, '20240001', '学生一')
            """
        )
        self.conn.commit()

    def test_freshness_accepts_recent_matching_roster_and_rejects_other_course(self):
        self._seed_roster(synced_at=datetime.now().isoformat(timespec="seconds"))

        freshness = service.get_classroom_exam_roster_freshness(
            self.conn,
            teacher_id=7,
            class_offering_id=30,
            max_age_seconds=30 * 60,
            exam_course_key="exam-1",
        )
        mismatch = service.get_classroom_exam_roster_freshness(
            self.conn,
            teacher_id=7,
            class_offering_id=30,
            max_age_seconds=30 * 60,
            exam_course_key="exam-2",
        )

        self.assertTrue(freshness["is_fresh"])
        self.assertEqual(freshness["student_count"], 1)
        self.assertGreater(freshness["remaining_seconds"], 1700)
        self.assertFalse(mismatch["is_fresh"])
        self.assertFalse(mismatch["course_matches"])

    def test_freshness_expires_after_thirty_minutes(self):
        self._seed_roster(
            synced_at=(datetime.now() - timedelta(minutes=31)).isoformat(timespec="seconds")
        )

        freshness = service.get_classroom_exam_roster_freshness(
            self.conn,
            teacher_id=7,
            class_offering_id=30,
            max_age_seconds=service.ACADEMIC_EXAM_ROSTER_CACHE_SECONDS,
        )

        self.assertFalse(freshness["is_fresh"])
        self.assertEqual(freshness["remaining_seconds"], 0)

    def test_sync_uses_cache_without_calling_academic_system(self):
        cached_payload = {
            "status": "success",
            "cache_hit": True,
            "sync_mode": "cache",
            "synced_at": "2026-07-30T02:25:39",
        }
        with (
            patch.object(service, "_load_cached_exam_roster_payload", return_value=cached_payload),
            patch.object(
                service,
                "_sync_classroom_exam_roster_from_academic_system_uncached",
                new=AsyncMock(),
            ) as live_sync,
        ):
            result = asyncio.run(
                service.sync_classroom_exam_roster_from_academic_system(
                    7,
                    30,
                    min_refresh_interval_seconds=30 * 60,
                )
            )

        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["sync_mode"], "cache")
        live_sync.assert_not_awaited()

    def test_sync_returns_structured_failure_instead_of_internal_server_error(self):
        with (
            patch.object(
                service,
                "_load_cached_exam_roster_payload",
                side_effect=RuntimeError("database unavailable"),
            ),
            patch.object(service.traceback, "print_exc"),
        ):
            result = asyncio.run(
                service.sync_classroom_exam_roster_from_academic_system(
                    7,
                    30,
                    min_refresh_interval_seconds=30 * 60,
                )
            )

        self.assertEqual(result["status"], "academic_query_failed")
        self.assertEqual(result["sync_mode"], "failed")
        self.assertFalse(result["cache_hit"])
        self.assertIn("未覆盖任何成绩数据", result["message"])


if __name__ == "__main__":
    unittest.main()
