import asyncio
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from classroom_app.routers import homework
from classroom_app.routers.homework_parts import assignments as homework_assignments
from classroom_app.routers.homework_parts import grading as homework_grading


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


@contextmanager
def _same_connection(conn):
    yield conn


class HomeworkRoutePermissionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                created_by_teacher_id INTEGER
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
                title TEXT,
                status TEXT DEFAULT 'published',
                grading_mode TEXT DEFAULT 'manual'
            );
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT NOT NULL,
                student_pk_id INTEGER NOT NULL,
                status TEXT DEFAULT 'submitted'
            );
            CREATE TABLE learning_stage_exam_attempts (
                id INTEGER PRIMARY KEY,
                assignment_id TEXT,
                student_id INTEGER,
                exam_paper_id TEXT
            );
            """
        )
        self.conn.execute("INSERT INTO courses (id, created_by_teacher_id) VALUES (?, ?)", (10, 9))
        self.conn.execute(
            "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
            (1001, 101, 10, 1),
        )
        self.conn.execute(
            "INSERT INTO assignments (id, course_id, class_offering_id, title, status) VALUES (?, ?, ?, ?, ?)",
            ("a-class", 10, 1001, "original", "published"),
        )
        self.conn.execute(
            "INSERT INTO submissions (id, assignment_id, student_pk_id, status) VALUES (?, ?, ?, ?)",
            (501, "a-class", 100, "submitted"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _title(self):
        row = self.conn.execute("SELECT title FROM assignments WHERE id = ?", ("a-class",)).fetchone()
        return str(row["title"] or "") if row else ""

    def _assignment_exists(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM assignments WHERE id = ?", ("a-class",)).fetchone()
        return row is not None

    def _run_with_patched_db(self, coroutine):
        with (
            patch.object(homework, "get_db_connection", lambda: _same_connection(self.conn)),
            patch.object(homework_assignments, "get_db_connection", lambda: _same_connection(self.conn)),
            patch.object(homework_grading, "get_db_connection", lambda: _same_connection(self.conn)),
            patch.object(homework, "close_overdue_assignments", lambda conn: 0),
            patch.object(homework_assignments, "close_overdue_assignments", lambda conn: 0),
            patch.object(homework_grading, "close_overdue_assignments", lambda conn: 0),
        ):
            return asyncio.run(coroutine)

    def test_update_assignment_rejects_non_offering_teacher_before_update(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run_with_patched_db(
                homework.update_assignment(
                    "a-class",
                    JsonRequest({"title": "changed"}),
                    user={"role": "teacher", "id": 2},
                )
            )

        self.assertEqual(403, ctx.exception.status_code)
        self.assertEqual("original", self._title())

    def test_delete_assignment_rejects_non_offering_teacher_before_delete(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run_with_patched_db(
                homework.delete_assignment(
                    "a-class",
                    user={"role": "teacher", "id": 2},
                )
            )

        self.assertEqual(403, ctx.exception.status_code)
        self.assertTrue(self._assignment_exists())

    def test_batch_grade_rejects_non_offering_teacher_before_ai_enqueue(self):
        with patch.object(homework_grading, "submit_submission_for_ai_grading", new_callable=AsyncMock) as enqueue:
            with self.assertRaises(HTTPException) as ctx:
                self._run_with_patched_db(
                    homework.batch_grade_submissions(
                        "a-class",
                        JsonRequest({"submission_ids": [501]}),
                        user={"role": "teacher", "id": 2},
                    )
                )

        self.assertEqual(403, ctx.exception.status_code)
        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
