"""重修/插班生服务的单元测试。

关注四件事：

1. AI 识别只按学号前缀给建议，绝不动教师已确认/已撤销的记录。
2. 教师确认才生效：默认分默认 70，历史已截止且未参加的任务补默认分占位，
   真实提交分数绝不改动；回填幂等。
3. 单份作业"截止"时插班生自动用自己的默认分（而不是教师本次选的兜底分）。
4. 名单外学生、非法分值被明确拒绝。
"""

import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

from fastapi import HTTPException

from classroom_app.services import classroom_retake_service as retake
from classroom_app.services.classroom_closeout_service import close_assignment

SCHEMA = """
CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE class_offerings (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    teacher_id INTEGER NOT NULL
);
CREATE TABLE students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    student_id_number TEXT,
    name TEXT,
    enrollment_status TEXT DEFAULT 'active'
);
CREATE TABLE assignments (
    id TEXT PRIMARY KEY,
    course_id INTEGER NOT NULL,
    class_offering_id INTEGER,
    title TEXT,
    status TEXT DEFAULT 'published',
    exam_paper_id TEXT,
    availability_mode TEXT DEFAULT 'permanent',
    starts_at TEXT,
    due_at TEXT,
    auto_close INTEGER DEFAULT 1,
    closed_at TEXT,
    late_submission_enabled INTEGER DEFAULT 0,
    late_submission_until TEXT,
    created_at TEXT DEFAULT '2026-01-01T00:00:00'
);
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT NOT NULL,
    student_pk_id INTEGER NOT NULL,
    student_name TEXT,
    status TEXT DEFAULT 'submitted',
    score REAL,
    feedback_md TEXT,
    answers_json TEXT,
    grading_started_at TEXT,
    grading_attempt_fingerprint TEXT,
    submitted_by_role TEXT DEFAULT 'student',
    submitted_by_teacher_id INTEGER,
    submission_channel TEXT DEFAULT 'online',
    resubmission_allowed INTEGER DEFAULT 0,
    resubmission_due_at TEXT,
    returned_at TEXT,
    returned_by_teacher_id INTEGER,
    returned_reason TEXT,
    is_absence_score INTEGER DEFAULT 0,
    absence_scored_at TEXT,
    absence_scored_by_teacher_id INTEGER,
    submitted_at TEXT
);
CREATE TABLE learning_stage_exam_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id TEXT,
    class_offering_id INTEGER,
    student_id INTEGER,
    status TEXT
);
"""

OFFERING_ID = 30
TEACHER_ID = 1


class ClassroomRetakeServiceTests(unittest.TestCase):
    def setUp(self):
        import classroom_app.db.schema_retake as schema_retake

        # 每个用例都是全新内存库：重置模块级 _SCHEMA_READY，确保建表执行。
        schema_retake._SCHEMA_READY = False

        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO courses VALUES (10, '课程')")
        self.conn.execute("INSERT INTO classes VALUES (20, '软工2405班')")
        self.conn.execute("INSERT INTO class_offerings VALUES (?, 10, 20, ?)", (OFFERING_ID, TEACHER_ID))
        students = [
            (101, 20, "24053010101", "学生一", "active"),
            (102, 20, "24053010102", "学生二", "active"),
            (103, 20, "24053010103", "学生三", "active"),
            (104, 20, "24053010104", "学生四", "active"),
            (105, 20, "24053010105", "学生五", "active"),
            (106, 20, "23053010333", "杨勇武", "active"),
        ]
        self.conn.executemany("INSERT INTO students VALUES (?, ?, ?, ?, ?)", students)
        schema_retake.ensure_retake_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_detection_flags_minority_prefix_and_preserves_teacher_decisions(self):
        detection = retake.detect_retake_candidates(self.conn, class_offering_id=OFFERING_ID)
        self.assertTrue(detection["detectable"])
        self.assertEqual(detection["majority_prefix"], "24")
        self.assertEqual([s["student_number"] for s in detection["suggestions"]], ["23053010333"])
        self.assertIn("23", detection["suggestions"][0]["reason"])

        # 教师确认后再次识别：确认记录保持不变。
        retake.confirm_retake_student(
            self.conn,
            class_offering_id=OFFERING_ID,
            student_id=106,
            teacher_id=TEACHER_ID,
            default_score=None,
        )
        retake.detect_retake_candidates(self.conn, class_offering_id=OFFERING_ID)
        rows = retake.list_retake_students(self.conn, class_offering_id=OFFERING_ID)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "confirmed")
        self.assertEqual(float(rows[0]["default_ordinary_score"]), 70.0)

    def test_detection_requires_clear_majority(self):
        # 前缀五五开时不可判定。
        self.conn.execute(
            "UPDATE students SET student_id_number = '2305301033' || id WHERE id IN (104, 105)"
        )
        detection = retake.detect_retake_candidates(self.conn, class_offering_id=OFFERING_ID)
        self.assertFalse(detection["detectable"])
        self.assertEqual(detection["suggestions"], [])

    def test_confirm_backfills_only_missing_closed_assignments(self):
        self.conn.executemany(
            "INSERT INTO assignments (id, course_id, class_offering_id, title, status) VALUES (?, 10, ?, ?, ?)",
            [
                ("a-closed-missing", OFFERING_ID, "已截止未参加", "closed"),
                ("a-closed-real", OFFERING_ID, "已截止已参加", "closed"),
                ("a-open", OFFERING_ID, "进行中", "published"),
            ],
        )
        self.conn.execute(
            "INSERT INTO submissions (assignment_id, student_pk_id, status, score) VALUES ('a-closed-real', 106, 'graded', 88)"
        )

        result = retake.confirm_retake_student(
            self.conn,
            class_offering_id=OFFERING_ID,
            student_id=106,
            teacher_id=TEACHER_ID,
            default_score=65,
        )
        self.assertEqual(result["default_ordinary_score"], 65.0)
        self.assertEqual(result["backfill"]["created_count"], 1)

        filled = self.conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = 'a-closed-missing' AND student_pk_id = 106"
        ).fetchone()
        self.assertIsNotNone(filled)
        self.assertEqual(float(filled["score"]), 65.0)
        self.assertEqual(int(filled["is_absence_score"]), 1)
        self.assertIn("重修", filled["feedback_md"])

        real = self.conn.execute(
            "SELECT score FROM submissions WHERE assignment_id = 'a-closed-real' AND student_pk_id = 106"
        ).fetchone()
        self.assertEqual(float(real["score"]), 88.0)
        open_rows = self.conn.execute(
            "SELECT COUNT(*) AS c FROM submissions WHERE assignment_id = 'a-open'"
        ).fetchone()
        self.assertEqual(int(open_rows["c"]), 0)

        # 幂等：重复回填不重复计数、不重复建行。
        again = retake.backfill_retake_absences_for_offering(
            self.conn,
            class_offering_id=OFFERING_ID,
            teacher_id=TEACHER_ID,
        )
        self.assertEqual(again["created_count"], 0)
        rows = self.conn.execute(
            "SELECT COUNT(*) AS c FROM submissions WHERE assignment_id = 'a-closed-missing' AND student_pk_id = 106"
        ).fetchone()
        self.assertEqual(int(rows["c"]), 1)

    def test_close_assignment_uses_retake_default_over_fallback_score(self):
        retake.confirm_retake_student(
            self.conn,
            class_offering_id=OFFERING_ID,
            student_id=106,
            teacher_id=TEACHER_ID,
            default_score=72,
        )
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, class_offering_id, title, status) VALUES ('a-final', 10, ?, '期末前作业', 'published')",
            (OFFERING_ID,),
        )
        assignment = dict(
            self.conn.execute(
                """
                SELECT a.*, o.class_id AS offering_class_id
                FROM assignments a JOIN class_offerings o ON o.id = a.class_offering_id
                WHERE a.id = 'a-final'
                """
            ).fetchone()
        )
        outcome = close_assignment(self.conn, assignment, teacher_id=TEACHER_ID, score=0)
        self.assertTrue(outcome["closed"])

        retake_row = self.conn.execute(
            "SELECT score, feedback_md FROM submissions WHERE assignment_id = 'a-final' AND student_pk_id = 106"
        ).fetchone()
        self.assertEqual(float(retake_row["score"]), 72.0)
        self.assertIn("重修", retake_row["feedback_md"])
        normal_row = self.conn.execute(
            "SELECT score FROM submissions WHERE assignment_id = 'a-final' AND student_pk_id = 101"
        ).fetchone()
        self.assertEqual(float(normal_row["score"]), 0.0)

    def test_confirm_rejects_students_outside_roster_and_invalid_scores(self):
        with self.assertRaises(HTTPException) as outsider:
            retake.confirm_retake_student(
                self.conn,
                class_offering_id=OFFERING_ID,
                student_id=999,
                teacher_id=TEACHER_ID,
            )
        self.assertEqual(outsider.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            retake.normalize_retake_default_score(101)
        self.assertEqual(retake.normalize_retake_default_score(None), 70.0)
        self.assertEqual(retake.normalize_retake_default_score(""), 70.0)

    def test_revoke_returns_student_to_normal_handling(self):
        retake.confirm_retake_student(
            self.conn,
            class_offering_id=OFFERING_ID,
            student_id=106,
            teacher_id=TEACHER_ID,
        )
        self.assertTrue(
            retake.is_confirmed_retake_student(
                self.conn, class_offering_id=OFFERING_ID, student_id=106
            )
        )
        retake.revoke_retake_student(
            self.conn,
            class_offering_id=OFFERING_ID,
            student_id=106,
            teacher_id=TEACHER_ID,
        )
        self.assertFalse(
            retake.is_confirmed_retake_student(
                self.conn, class_offering_id=OFFERING_ID, student_id=106
            )
        )
        self.assertEqual(
            retake.get_confirmed_retake_students(self.conn, class_offering_id=OFFERING_ID),
            [],
        )


if __name__ == "__main__":
    unittest.main()
