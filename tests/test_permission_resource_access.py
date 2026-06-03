import sqlite3
import unittest

from classroom_app.services.learning_progress_service import student_can_access_assignment
from classroom_app.services.resource_access_service import (
    student_can_read_assignment,
    student_can_read_submission,
    teacher_can_manage_assignment,
    teacher_can_manage_class_offering,
    teacher_can_manage_course,
    teacher_can_manage_semester,
    teacher_can_manage_student,
    teacher_can_manage_submission,
    teacher_can_manage_textbook,
    teacher_can_read_student,
    teacher_can_use_course,
    teacher_can_use_semester,
    teacher_can_use_textbook,
)


class PermissionResourceAccessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1,
                is_super_admin INTEGER DEFAULT 0,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE teacher_organization_memberships (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                school_code TEXT NOT NULL,
                school_name TEXT,
                college TEXT,
                department TEXT,
                is_primary INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                updated_at TEXT
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT,
                enrollment_status TEXT DEFAULT 'active'
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                created_by_teacher_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                created_by_teacher_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE academic_semesters (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER,
                school_code TEXT,
                school_name TEXT,
                college TEXT,
                department TEXT
            );
            CREATE TABLE textbooks (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL
            );
            CREATE TABLE assignments (
                id TEXT PRIMARY KEY,
                course_id INTEGER NOT NULL,
                class_offering_id INTEGER,
                status TEXT NOT NULL DEFAULT 'new',
                exam_paper_id TEXT
            );
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT NOT NULL,
                student_pk_id INTEGER NOT NULL
            );
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT,
                student_id INTEGER,
                exam_paper_id TEXT
            );
            """
        )
        self.conn.executemany(
            """
            INSERT INTO teachers (id, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "gxufl", "GXUFL", "info", "network"),
                (2, "gxufl", "GXUFL", "info", "software"),
                (3, "other", "Other", "info", "network"),
                (5, "gxufl", "GXUFL", "info", "network"),
                (6, "gxufl", "GXUFL", "info", "network"),
                (9, "other", "Other", "info", "network"),
            ],
        )
        self.conn.execute("UPDATE teachers SET is_super_admin = 1 WHERE id = 9")
        self.conn.executemany(
            """
            INSERT INTO teacher_organization_memberships (
                id, teacher_id, school_code, school_name, college, department,
                is_primary, is_active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 5, "gxufl", "GXUFL", "info", "network", 1, 1, "2026-01-01"),
                (2, 5, "other", "Other", "info", "network", 0, 1, "2026-01-02"),
                (3, 6, "gxufl", "GXUFL", "info", "network", 1, 1, "2026-01-01"),
                (4, 6, "other", "Other", "info", "network", 0, 0, "2026-01-02"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO courses (id, created_by_teacher_id, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (10, 1, "gxufl", "GXUFL", "info", "network"),
                (20, 3, "other", "Other", "info", "network"),
                (30, 2, "gxufl", "GXUFL", "info", "software"),
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO classes (id, created_by_teacher_id, school_code, school_name, college, department)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (101, 1, "gxufl", "GXUFL", "info", "network"),
                (202, 3, "other", "Other", "info", "network"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO students (id, class_id, school_code, school_name, college, department) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (100, 101, "gxufl", "GXUFL", "info", "network"),
                (200, 202, "other", "Other", "info", "network"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
            [
                (1001, 101, 10, 1),
                (1002, 202, 20, 3),
                (1003, 101, 30, 1),
                (1004, 101, 30, 2),
            ],
        )
        self.conn.executemany(
            "INSERT INTO academic_semesters (id, teacher_id, school_code, school_name, college, department) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (701, 1, "gxufl", "GXUFL", "info", "network"),
                (702, 3, "other", "Other", "info", "network"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO textbooks (id, teacher_id) VALUES (?, ?)",
            [
                (801, 1),
                (802, 3),
            ],
        )
        self.conn.executemany(
            "INSERT INTO assignments (id, course_id, class_offering_id, status) VALUES (?, ?, ?, ?)",
            [
                ("a-class", 10, 1001, "published"),
                ("a-new", 10, 1001, "new"),
                ("a-other", 20, 1002, "published"),
                ("a-reused-course", 30, 1003, "published"),
                ("a-course-wide", 10, None, "published"),
                ("a-personal", 10, 1001, "published"),
            ],
        )
        self.conn.execute(
            "INSERT INTO learning_stage_exam_attempts (assignment_id, student_id) VALUES (?, ?)",
            ("a-personal", 100),
        )
        self.conn.executemany(
            "INSERT INTO submissions (id, assignment_id, student_pk_id) VALUES (?, ?, ?)",
            [
                (1, "a-class", 100),
                (2, "a-other", 200),
                (3, "a-class", 200),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def _course(self, course_id: int):
        return self.conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()

    def _semester(self, semester_id: int):
        return self.conn.execute("SELECT * FROM academic_semesters WHERE id = ?", (semester_id,)).fetchone()

    def _textbook(self, textbook_id: int):
        return self.conn.execute("SELECT * FROM textbooks WHERE id = ?", (textbook_id,)).fetchone()

    def _offering(self, offering_id: int):
        return self.conn.execute("SELECT * FROM class_offerings WHERE id = ?", (offering_id,)).fetchone()

    def _student(self, student_id: int):
        return self.conn.execute(
            """
            SELECT s.*, c.created_by_teacher_id
            FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
            """,
            (student_id,),
        ).fetchone()

    def test_same_school_teacher_can_use_but_not_manage_course(self):
        course = self._course(10)

        self.assertTrue(teacher_can_use_course(self.conn, 2, course))
        self.assertFalse(teacher_can_manage_course(self.conn, 2, course))
        self.assertFalse(teacher_can_use_course(self.conn, 3, course))
        self.assertTrue(teacher_can_manage_course(self.conn, 1, course))

    def test_active_secondary_membership_applies_but_inactive_membership_does_not(self):
        other_school_course = self._course(20)

        self.assertTrue(teacher_can_use_course(self.conn, 5, other_school_course))
        self.assertFalse(teacher_can_manage_course(self.conn, 5, other_school_course))
        self.assertFalse(teacher_can_use_course(self.conn, 6, other_school_course))

    def test_semester_use_follows_school_scope_but_manage_stays_owned(self):
        semester = self._semester(701)

        self.assertTrue(teacher_can_use_semester(self.conn, 2, semester))
        self.assertFalse(teacher_can_manage_semester(self.conn, 2, semester))
        self.assertFalse(teacher_can_use_semester(self.conn, 3, semester))
        self.assertTrue(teacher_can_manage_semester(self.conn, 1, semester))

    def test_textbook_is_private_until_a_scope_column_exists(self):
        textbook = self._textbook(801)

        self.assertTrue(teacher_can_use_textbook(self.conn, 1, textbook))
        self.assertTrue(teacher_can_manage_textbook(self.conn, 1, textbook))
        self.assertFalse(teacher_can_use_textbook(self.conn, 2, textbook))
        self.assertFalse(teacher_can_manage_textbook(self.conn, 2, textbook))
        self.assertTrue(teacher_can_manage_textbook(self.conn, 9, textbook))

    def test_classroom_and_student_management_follow_classroom_boundaries(self):
        offering = self._offering(1004)
        student = self._student(100)

        self.assertTrue(teacher_can_manage_class_offering(self.conn, 2, offering))
        self.assertFalse(teacher_can_manage_class_offering(self.conn, 1, offering))
        self.assertTrue(teacher_can_read_student(self.conn, 2, student))
        self.assertFalse(teacher_can_manage_student(self.conn, 2, student))
        self.assertTrue(teacher_can_manage_student(self.conn, 1, student))

    def test_student_assignment_requires_classroom_or_course_membership_and_publication(self):
        self.assertTrue(student_can_read_assignment(self.conn, "a-class", 100))
        self.assertTrue(student_can_access_assignment(self.conn, "a-class", 100))
        self.assertFalse(student_can_read_assignment(self.conn, "a-class", 200))
        self.assertFalse(student_can_read_assignment(self.conn, "a-other", 100))
        self.assertFalse(student_can_read_assignment(self.conn, "a-new", 100))
        self.assertTrue(student_can_read_assignment(self.conn, "a-course-wide", 100))
        self.assertFalse(student_can_read_assignment(self.conn, "a-course-wide", 200))

    def test_personal_stage_assignment_is_target_student_only(self):
        self.assertTrue(student_can_read_assignment(self.conn, "a-personal", 100))
        self.assertFalse(student_can_read_assignment(self.conn, "a-personal", 200))

    def test_classroom_assignment_management_follows_offering_teacher_not_course_owner(self):
        self.assertTrue(teacher_can_manage_assignment(self.conn, 1, "a-reused-course"))
        self.assertFalse(teacher_can_manage_assignment(self.conn, 2, "a-reused-course"))
        self.assertFalse(teacher_can_manage_assignment(self.conn, 3, "a-reused-course"))

    def test_submission_access_reuses_assignment_boundary(self):
        self.assertTrue(teacher_can_manage_submission(self.conn, 1, 1))
        self.assertFalse(teacher_can_manage_submission(self.conn, 2, 1))
        self.assertTrue(student_can_read_submission(self.conn, 100, 1))
        self.assertFalse(student_can_read_submission(self.conn, 200, 1))
        self.assertFalse(student_can_read_submission(self.conn, 200, 3))


if __name__ == "__main__":
    unittest.main()
