"""学生学习连击服务的单元测试（sqlite）。"""

import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.student_streak_service import (
    ensure_streak_schema,
    get_student_streak,
    record_student_activity,
)

STUDENT_ID = 9401


class StudentStreakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            ensure_streak_schema(conn)
            conn.execute("DELETE FROM student_activity_streaks WHERE student_id = ?", (STUDENT_ID,))
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM student_activity_streaks WHERE student_id = ?", (STUDENT_ID,))
            conn.commit()

    def test_same_day_idempotent_and_consecutive_days_accumulate(self):
        day1 = date(2026, 7, 10)
        with get_db_connection() as conn:
            first = record_student_activity(conn, STUDENT_ID, active_date=day1)
            self.assertEqual(first["current_streak"], 1)
            # 同日重复：幂等
            again = record_student_activity(conn, STUDENT_ID, active_date=day1)
            self.assertEqual(again["current_streak"], 1)
            # 连续三天
            second = record_student_activity(conn, STUDENT_ID, active_date=day1 + timedelta(days=1))
            third = record_student_activity(conn, STUDENT_ID, active_date=day1 + timedelta(days=2))
            self.assertEqual(second["current_streak"], 2)
            self.assertEqual(third["current_streak"], 3)
            self.assertEqual(third["longest_streak"], 3)
            conn.commit()

    def test_gap_resets_current_but_keeps_longest(self):
        day1 = date(2026, 7, 1)
        with get_db_connection() as conn:
            record_student_activity(conn, STUDENT_ID, active_date=day1)
            record_student_activity(conn, STUDENT_ID, active_date=day1 + timedelta(days=1))
            record_student_activity(conn, STUDENT_ID, active_date=day1 + timedelta(days=2))
            # 断两天后再来：current 重置为 1，longest 保留 3。
            resumed = record_student_activity(conn, STUDENT_ID, active_date=day1 + timedelta(days=5))
            self.assertEqual(resumed["current_streak"], 1)
            self.assertEqual(resumed["longest_streak"], 3)
            conn.commit()

    def test_get_folds_stale_streak_to_zero(self):
        stale_day = date(2026, 6, 1)
        with get_db_connection() as conn:
            record_student_activity(conn, STUDENT_ID, active_date=stale_day)
            info = get_student_streak(conn, STUDENT_ID)
            conn.commit()
        # 最后活跃远早于昨天 → current 折算 0，longest 保留。
        self.assertEqual(info["current_streak"], 0)
        self.assertEqual(info["longest_streak"], 1)
        self.assertFalse(info["active_today"])

    def test_get_for_unknown_student_returns_zero(self):
        with get_db_connection() as conn:
            info = get_student_streak(conn, 999999)
        self.assertEqual(info["current_streak"], 0)
        self.assertEqual(info["longest_streak"], 0)


if __name__ == "__main__":
    unittest.main()
