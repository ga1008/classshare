"""AI 学伴课程上下文服务的单元测试（sqlite）。"""

import json
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.student_ai_tutor_context_service import (
    build_tutor_context_block,
    detect_open_task_hit,
    retrieve_material_snippets,
)

TEACHER_ID = 937
CLASS_ID = 931
COURSE_ID = 931
OFFERING_ID = 931
MATERIAL_TCP = 9311
MATERIAL_HTTP = 9312
PAPER_ID = "tutor-paper-1"
ASSIGNMENT_ID = 9031

TCP_MD = (
    "# 三次握手\n\nTCP 建立连接需要三次握手：SYN、SYN-ACK、ACK。"
    "第一次握手由客户端发起，携带初始序列号。三次握手的目的是同步双方的初始序列号并确认双向可达。"
)
HTTP_MD = "# HTTP 概述\n\nHTTP 是应用层协议，默认端口 80，HTTPS 默认端口 443。"

EXAM_QUESTION = "请结合抓包结果，详细分析 TCP 三次握手过程中每个报文的序列号变化规律。"


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM course_material_assignments WHERE class_offering_id = ?", (OFFERING_ID,)),
        ("DELETE FROM course_materials WHERE id IN (?, ?)", (MATERIAL_TCP, MATERIAL_HTTP)),
        ("DELETE FROM assignments WHERE id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM exam_papers WHERE id = ?", (PAPER_ID,)),
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
        (TEACHER_ID, "Teacher", "teacher937@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (CLASS_ID, "网络班", TEACHER_ID))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (COURSE_ID, "计算机网络", TEACHER_ID))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    for mid, name, content in (
        (MATERIAL_TCP, "三次握手精讲", TCP_MD),
        (MATERIAL_HTTP, "HTTP 基础", HTTP_MD),
    ):
        conn.execute(
            """
            INSERT INTO course_materials (id, teacher_id, material_path, name, node_type, file_ext, ai_optimized_markdown)
            VALUES (?, ?, ?, ?, 'file', 'md', ?)
            """,
            (mid, TEACHER_ID, f"tutor-test/{mid}.md", name, content),
        )
        conn.execute(
            """
            INSERT INTO course_material_assignments (material_id, class_offering_id, assigned_by_teacher_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (mid, OFFERING_ID, TEACHER_ID, now),
        )
    questions_json = json.dumps(
        {"pages": [{"name": "分析题", "questions": [{"id": "q1", "type": "textarea", "text": EXAM_QUESTION, "points": 20}]}]},
        ensure_ascii=False,
    )
    conn.execute(
        """
        INSERT INTO exam_papers (id, teacher_id, title, questions_json, status,
                                 owner_role, scope_level, school_code, school_name, college, department)
        VALUES (?, ?, ?, ?, 'published', 'teacher', 'private', 'gxufl', '测试学院', '测试学院', '测试系')
        """,
        (PAPER_ID, TEACHER_ID, "网络实验考试", questions_json),
    )
    due = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO assignments (id, course_id, class_offering_id, exam_paper_id, title, status, due_at, created_at)
        VALUES (?, ?, ?, ?, ?, 'published', ?, ?)
        """,
        (ASSIGNMENT_ID, COURSE_ID, OFFERING_ID, PAPER_ID, "抓包分析实验考试", due, now),
    )


class TutorContextTests(unittest.TestCase):
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

    def test_retrieves_relevant_material_with_snippet(self):
        with get_db_connection() as conn:
            snippets = retrieve_material_snippets(
                conn, class_offering_id=OFFERING_ID, query="@助教 三次握手的序列号是怎么同步的？"
            )
        self.assertTrue(snippets)
        self.assertEqual(snippets[0]["title"], "三次握手精讲")
        self.assertIn("序列号", snippets[0]["snippet"])

    def test_irrelevant_query_returns_empty(self):
        with get_db_connection() as conn:
            snippets = retrieve_material_snippets(
                conn, class_offering_id=OFFERING_ID, query="食堂晚饭吃啥"
            )
        self.assertEqual(snippets, [])

    def test_detects_pasted_exam_question(self):
        with get_db_connection() as conn:
            hit = detect_open_task_hit(
                conn, class_offering_id=OFFERING_ID, query=f"@助教 {EXAM_QUESTION} 答案是什么"
            )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["kind"], "考试")
        self.assertEqual(hit["title"], "抓包分析实验考试")

    def test_no_guard_for_generic_question_or_closed_task(self):
        with get_db_connection() as conn:
            hit = detect_open_task_hit(
                conn, class_offering_id=OFFERING_ID, query="HTTP 默认端口是多少"
            )
            self.assertIsNone(hit)
            # 任务关闭后即使贴题干也不再触发守卫（可以完整复盘了）。
            conn.execute(
                "UPDATE assignments SET closed_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), ASSIGNMENT_ID),
            )
            hit_closed = detect_open_task_hit(
                conn, class_offering_id=OFFERING_ID, query=f"{EXAM_QUESTION} 答案"
            )
            self.assertIsNone(hit_closed)
            conn.commit()

    def test_context_block_guard_only_for_students(self):
        query = f"@助教 {EXAM_QUESTION} 直接告诉我答案"
        with get_db_connection() as conn:
            student_block = build_tutor_context_block(
                conn, class_offering_id=OFFERING_ID, query=query, user_role="student"
            )
            teacher_block = build_tutor_context_block(
                conn, class_offering_id=OFFERING_ID, query=query, user_role="teacher"
            )
        self.assertIn("学业诚信守卫", student_block)
        self.assertIn("抓包分析实验考试", student_block)
        self.assertIn("课程材料参考", student_block)
        self.assertNotIn("学业诚信守卫", teacher_block)

    def test_context_block_never_raises_on_bad_offering(self):
        with get_db_connection() as conn:
            block = build_tutor_context_block(
                conn, class_offering_id=999999, query="三次握手", user_role="student"
            )
        self.assertEqual(block, "")


if __name__ == "__main__":
    unittest.main()
