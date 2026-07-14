"""学生错题本服务的单元测试（sqlite）。"""

import json
import os
import unittest
from datetime import datetime

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.student_wrong_book_service import build_student_wrong_book

TEACHER_ID = 917
CLASS_ID = 911
COURSE_ID = 911
OFFERING_ID = 911
STUDENT_ID = 9111
PAPER_ID = "wrongbook-paper-1"
ASSIGNMENT_ID = 9011

QUESTIONS_JSON = json.dumps(
    {
        "pages": [
            {
                "name": "第一部分",
                "questions": [
                    {
                        "id": "q1",
                        "type": "radio",
                        "text": "TCP 建立连接需要几次握手？",
                        "options": ["A. 两次", "B. 三次", "C. 四次"],
                        "answer": "B",
                        "points": 5,
                        "knowledge_points": "三次握手",
                    },
                    {
                        "id": "q2",
                        "type": "textarea",
                        "text": "简述拥塞控制的基本思想。",
                        "points": 10,
                        "knowledge_points": "拥塞控制",
                    },
                    {
                        "id": "q3",
                        "type": "radio",
                        "text": "HTTP 默认端口是？",
                        "options": ["A. 21", "B. 80", "C. 443"],
                        "answer": "B",
                        "points": 5,
                        "knowledge_points": "应用层协议",
                    },
                ],
            }
        ]
    },
    ensure_ascii=False,
)

# q1 选错(C≠B)、q2 文本题 6/10 失分、q3 选对 → 错题应为 q1、q2。
ANSWERS_JSON = json.dumps({"q1": "C", "q2": "带宽探测与退避", "q3": "B"}, ensure_ascii=False)
FEEDBACK_MD = "### 第 2 题\n得分 6/10，思路只覆盖了慢启动。"


def _cleanup(conn):
    for sql, params in (
        ("DELETE FROM submissions WHERE assignment_id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM assignments WHERE id = ?", (ASSIGNMENT_ID,)),
        ("DELETE FROM exam_papers WHERE id = ?", (PAPER_ID,)),
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
        (TEACHER_ID, "Teacher", "teacher917@example.test", "hashed", "gxufl", "测试学院", 1),
    )
    conn.execute(
        "INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)",
        (CLASS_ID, "网络班", TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)",
        (COURSE_ID, "计算机网络", TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (OFFERING_ID, CLASS_ID, COURSE_ID, TEACHER_ID),
    )
    conn.execute(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        (STUDENT_ID, "S9111", "Carol", CLASS_ID),
    )
    conn.execute(
        """
        INSERT INTO exam_papers (id, teacher_id, title, questions_json, status,
                                 owner_role, scope_level, school_code, school_name, college, department)
        VALUES (?, ?, ?, ?, 'published', 'teacher', 'private', 'gxufl', '测试学院', '测试学院', '测试系')
        """,
        (PAPER_ID, TEACHER_ID, "网络期中卷", QUESTIONS_JSON),
    )
    conn.execute(
        """
        INSERT INTO assignments (id, course_id, class_offering_id, exam_paper_id, title, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'published', ?)
        """,
        (ASSIGNMENT_ID, COURSE_ID, OFFERING_ID, PAPER_ID, "网络期中考试", now),
    )
    conn.execute(
        """
        INSERT INTO submissions (assignment_id, student_pk_id, student_name, status,
                                 answers_json, feedback_md, score, submitted_at)
        VALUES (?, ?, ?, 'graded', ?, ?, 16, ?)
        """,
        (ASSIGNMENT_ID, STUDENT_ID, "Carol", ANSWERS_JSON, FEEDBACK_MD, now),
    )


class StudentWrongBookTests(unittest.TestCase):
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

    def test_collects_wrong_choice_and_partial_text_questions(self):
        with get_db_connection() as conn:
            book = build_student_wrong_book(conn, student_id=STUDENT_ID)

        ordinals = sorted(item["question_ordinal"] for item in book["items"])
        self.assertEqual(ordinals, [1, 2])

        choice_item = next(i for i in book["items"] if i["question_ordinal"] == 1)
        self.assertIn("三次握手", choice_item["knowledge_points"])
        self.assertEqual(choice_item["max_score"], 5.0)
        self.assertEqual(choice_item["score"], 0.0)
        self.assertEqual(choice_item["link_url"], f"/assignment/{ASSIGNMENT_ID}")

        text_item = next(i for i in book["items"] if i["question_ordinal"] == 2)
        self.assertEqual(text_item["score"], 6.0)
        self.assertEqual(text_item["max_score"], 10.0)

        self.assertEqual(book["summary"]["wrong_total"], 2)
        self.assertEqual(book["summary"]["evaluated_total"], 3)
        self.assertEqual(book["summary"]["exam_count"], 1)
        self.assertEqual(len(book["courses"]), 1)
        self.assertEqual(book["courses"][0]["wrong_count"], 2)

    def test_knowledge_mastery_flags_weak_points_first(self):
        with get_db_connection() as conn:
            book = build_student_wrong_book(conn, student_id=STUDENT_ID)

        mastery = {item["name"]: item for item in book["knowledge_mastery"]}
        self.assertEqual(mastery["三次握手"]["mastery_percent"], 0)
        self.assertEqual(mastery["三次握手"]["tier_tone"], "danger")
        self.assertEqual(mastery["应用层协议"]["mastery_percent"], 100)
        # 薄弱点排在最前面。
        self.assertEqual(book["knowledge_mastery"][0]["mastery_percent"], 0)
        self.assertIn("三次握手", book["summary"]["weakest_points"])

    def test_empty_for_student_without_graded_exams(self):
        with get_db_connection() as conn:
            book = build_student_wrong_book(conn, student_id=999999)
        self.assertEqual(book["items"], [])
        self.assertEqual(book["summary"]["wrong_total"], 0)
        self.assertIsNone(book["summary"]["correct_percent"])


if __name__ == "__main__":
    unittest.main()
