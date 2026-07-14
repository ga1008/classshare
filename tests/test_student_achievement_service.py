"""学生成就徽章系统的单元测试（sqlite）。"""

import os
import unittest
from datetime import datetime

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.student_achievement_service import (
    build_achievement_wall,
    ensure_achievement_schema,
    evaluate_and_award,
)
from classroom_app.services.student_streak_service import ensure_streak_schema

STUDENT_ID = 9501
TEACHER_ID = 947
CLASS_ID = 941
COURSE_ID = 941
OFFERING_ID = 941
ASSIGNMENT_ID = 9041


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM student_achievements WHERE student_id = ?", (STUDENT_ID,)),
        ("DELETE FROM student_activity_streaks WHERE student_id = ?", (STUDENT_ID,)),
        ("DELETE FROM message_center_notifications WHERE ref_type = 'achievement' AND recipient_user_pk = ?", (STUDENT_ID,)),
        ("DELETE FROM classroom_behavior_states WHERE user_pk = ? AND user_role = 'student'", (STUDENT_ID,)),
        ("DELETE FROM submissions WHERE assignment_id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM assignments WHERE id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM students WHERE id = ?", (STUDENT_ID,)),
        ("DELETE FROM class_offerings WHERE id = ?", (OFFERING_ID,)),
        ("DELETE FROM courses WHERE id = ?", (COURSE_ID,)),
        ("DELETE FROM classes WHERE id = ?", (CLASS_ID,)),
        ("DELETE FROM teachers WHERE id = ?", (TEACHER_ID,)),
    ):
        try:
            conn.execute(sql, params)
        except Exception:
            pass


def _seed(conn):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TEACHER_ID, "Teacher", "teacher947@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_ID, "成就班", TEACHER_ID))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_ID, "操作系统", TEACHER_ID))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        (STUDENT_ID, "S9501", "Dave", CLASS_ID),
    )
    conn.execute(
        """
        INSERT INTO assignments (id, course_id, class_offering_id, title, status, created_at)
        VALUES (?, ?, ?, ?, 'published', ?)
        """,
        (ASSIGNMENT_ID, COURSE_ID, OFFERING_ID, "第1次作业", now),
    )
    # 事实：streak 最长 7 天、一次 92 分提交。
    conn.execute(
        """
        INSERT INTO student_activity_streaks (student_id, current_streak, longest_streak, last_active_date, updated_at)
        VALUES (?, 2, 7, ?, ?)
        """,
        (STUDENT_ID, now[:10], now),
    )
    conn.execute(
        """
        INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, submitted_at)
        VALUES (?, ?, ?, 'graded', 92, ?)
        """,
        (ASSIGNMENT_ID, STUDENT_ID, "Dave", now),
    )


class StudentAchievementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            ensure_streak_schema(conn)
            ensure_achievement_schema(conn)
            _cleanup(conn)
            _seed(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_awards_matching_badges_and_notifies_once(self):
        with get_db_connection() as conn:
            first_run = evaluate_and_award(conn, STUDENT_ID)
            keys = {badge["key"] for badge in first_run}
            # streak7 覆盖 streak3；92 分拿高分章；1 次提交拿首提章。
            self.assertEqual(keys, {"streak_3", "streak_7", "first_submission", "high_score_90"})

            # 幂等：第二次评定不再新增。
            second_run = evaluate_and_award(conn, STUDENT_ID)
            self.assertEqual(second_run, [])

            notifications = conn.execute(
                "SELECT ref_id FROM message_center_notifications WHERE ref_type = 'achievement' AND recipient_user_pk = ?",
                (STUDENT_ID,),
            ).fetchall()
            self.assertEqual(len(notifications), 4)
            conn.commit()

    def test_wall_orders_earned_first_with_progress_hints(self):
        with get_db_connection() as conn:
            wall = build_achievement_wall(conn, STUDENT_ID)
            conn.commit()
        self.assertEqual(wall["earned_count"], 4)
        self.assertEqual(wall["total_count"], 7)
        # 已获得排前面。
        self.assertTrue(all(badge["earned"] for badge in wall["badges"][:4]))
        locked = {badge["key"]: badge for badge in wall["badges"] if not badge["earned"]}
        self.assertIn("streak_30", locked)
        self.assertIn("进度 7 / 30", locked["streak_30"]["progress_hint"])
        self.assertIn("进度 1 / 10", locked["submissions_10"]["progress_hint"])

    def test_fresh_student_earns_nothing(self):
        with get_db_connection() as conn:
            wall = build_achievement_wall(conn, 999999)
        self.assertEqual(wall["earned_count"], 0)
        self.assertEqual(wall["newly_awarded"], [])


if __name__ == "__main__":
    unittest.main()
