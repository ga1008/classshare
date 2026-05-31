import sqlite3
import unittest

from classroom_app.services.resource_access_service import (
    teacher_can_manage_exam_paper,
    teacher_can_use_exam_paper,
)


class ExamPaperScopeAccessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1,
                is_super_admin INTEGER DEFAULT 0,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            )
            """
        )
        self.conn.executemany(
            """
            INSERT INTO teachers (id, is_super_admin, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 0, "gxufl", "GXUFL", "info", "network"),
                (2, 0, "gxufl", "GXUFL", "info", "network"),
                (3, 0, "gxufl", "GXUFL", "info", "software"),
                (4, 0, "other", "Other", "info", "network"),
                (9, 1, "other", "Other", "info", "network"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def _paper(self, scope_level, **overrides):
        paper = {
            "id": "paper-1",
            "teacher_id": 1,
            "owner_role": "teacher",
            "owner_user_pk": 1,
            "scope_level": scope_level,
            "school_code": "gxufl",
            "school_name": "GXUFL",
            "college": "info",
            "department": "network",
        }
        paper.update(overrides)
        return paper

    def test_private_exam_is_owner_only_except_super_admin(self):
        paper = self._paper("private")

        self.assertTrue(teacher_can_use_exam_paper(self.conn, 1, paper))
        self.assertTrue(teacher_can_manage_exam_paper(self.conn, 1, paper))
        self.assertFalse(teacher_can_use_exam_paper(self.conn, 2, paper))
        self.assertFalse(teacher_can_manage_exam_paper(self.conn, 2, paper))
        self.assertTrue(teacher_can_use_exam_paper(self.conn, 9, paper))
        self.assertTrue(teacher_can_manage_exam_paper(self.conn, 9, paper))

    def test_department_exam_is_visible_to_same_department_only(self):
        paper = self._paper("department")

        self.assertTrue(teacher_can_use_exam_paper(self.conn, 2, paper))
        self.assertFalse(teacher_can_manage_exam_paper(self.conn, 2, paper))
        self.assertFalse(teacher_can_use_exam_paper(self.conn, 3, paper))
        self.assertFalse(teacher_can_use_exam_paper(self.conn, 4, paper))

    def test_school_exam_is_visible_to_same_school(self):
        paper = self._paper("school")

        self.assertTrue(teacher_can_use_exam_paper(self.conn, 2, paper))
        self.assertTrue(teacher_can_use_exam_paper(self.conn, 3, paper))
        self.assertFalse(teacher_can_use_exam_paper(self.conn, 4, paper))


if __name__ == "__main__":
    unittest.main()
