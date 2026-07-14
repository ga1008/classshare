"""间隔重复复习队列的单元测试（sqlite）。"""

import os
import unittest
from datetime import timedelta

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.academic_service import china_now
from classroom_app.services.student_review_queue_service import (
    build_review_next_steps,
    build_review_queue,
)

TEACHER_ID = 987
CLASS_ID = 971
COURSE_ID = 971
OFFERING_ID = 971
STUDENT_ID = 9901
MATERIALS = {
    "retry": 9911,
    "window3": 9912,
    "between": 9913,
    "fresh": 9914,
    "incomplete": 9915,
}


def _now():
    return china_now().replace(tzinfo=None)


def _cleanup(conn):
    ids = tuple(MATERIALS.values())
    placeholders = ",".join("?" for _ in ids)
    for sql, params in (
        (f"DELETE FROM learning_material_progress WHERE material_id IN ({placeholders})", ids),
        (f"DELETE FROM course_materials WHERE id IN ({placeholders})", ids),
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
    now = _now()
    now_iso = now.isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TEACHER_ID, "Teacher", "teacher987@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_ID, "复习班", TEACHER_ID))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_ID, "数据库原理", TEACHER_ID))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        (STUDENT_ID, "S9901", "Frank", CLASS_ID),
    )
    names = {
        "retry": "事务隔离级别",
        "window3": "索引与查询优化",
        "between": "范式分解",
        "fresh": "SQL 基础",
        "incomplete": "存储引擎",
    }
    for key, mid in MATERIALS.items():
        conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, material_path, name, node_type, file_ext)
            VALUES (?, ?, ?, ?, 'file', 'md')
            """,
            (mid, TEACHER_ID, f"review-test/{mid}.md", names[key]),
        )

    def add_progress(mid, *, completed, mastered, mastered_days_ago=None, attempts=0):
        mastered_at = (
            (now - timedelta(days=mastered_days_ago)).isoformat(timespec="seconds")
            if mastered_days_ago is not None
            else None
        )
        conn.execute(
            """
            INSERT INTO learning_material_progress (
                class_offering_id, student_id, material_id,
                completed, mastered, mastered_at, mastery_attempts, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (OFFERING_ID, STUDENT_ID, mid, completed, mastered, mastered_at, attempts, now_iso),
        )

    add_progress(MATERIALS["retry"], completed=1, mastered=0, attempts=2)             # 重试卡
    add_progress(MATERIALS["window3"], completed=1, mastered=1, mastered_days_ago=3)  # 3 天窗口
    add_progress(MATERIALS["between"], completed=1, mastered=1, mastered_days_ago=5)  # 窗口之间 → 无卡
    add_progress(MATERIALS["fresh"], completed=1, mastered=1, mastered_days_ago=0)    # 今天 → 无卡
    add_progress(MATERIALS["incomplete"], completed=0, mastered=0, attempts=3)        # 未读完 → 无卡


class ReviewQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            _seed(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_retry_first_then_window_and_gaps_excluded(self):
        with get_db_connection() as conn:
            queue = build_review_queue(conn, STUDENT_ID)
        names = [item["material_name"] for item in queue]
        self.assertEqual(names[0], "事务隔离级别")  # 未通过重试置顶
        self.assertIn("索引与查询优化", names)      # 3 天窗口
        self.assertNotIn("范式分解", names)          # 5 天：窗口间隙
        self.assertNotIn("SQL 基础", names)          # 今天刚掌握
        self.assertNotIn("存储引擎", names)          # 未完成研读

    def test_next_steps_structure(self):
        with get_db_connection() as conn:
            steps = build_review_next_steps(conn, STUDENT_ID, limit=2)
        self.assertEqual(len(steps), 2)
        retry = steps[0]
        self.assertEqual(retry["kind"], "review")
        self.assertEqual(retry["label"], "重试检验")
        self.assertEqual(retry["tone"], "danger")
        self.assertEqual(retry["href"], f"/classroom/{OFFERING_ID}")
        self.assertEqual(retry["due_label"], "数据库原理")
        self.assertEqual(steps[1]["label"], "间隔复习")

    def test_empty_for_student_without_progress(self):
        with get_db_connection() as conn:
            self.assertEqual(build_review_queue(conn, 999999), [])


if __name__ == "__main__":
    unittest.main()
