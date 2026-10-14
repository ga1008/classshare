"""Deleting an assignment with student work needs an explicit count confirmation (audit B9)."""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import HTTPException

from tests.sqlite_database_fixture import isolated_sqlite_database
from classroom_app.database import get_db_connection
from classroom_app.routers.homework_parts import assignments as routes
from classroom_app.services import approval_request_types  # noqa: F401  registers types

TEACHER = {"role": "teacher", "id": 9701, "name": "赵老师"}


class AssignmentDeleteGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        from classroom_app.services import approval_workflow_schema

        cls.enterClassContext(patch.object(approval_workflow_schema, "_SCHEMA_READY", False))
        with get_db_connection() as conn:
            approval_workflow_schema.ensure_approval_workflow_schema(conn)
            conn.execute("INSERT INTO teachers (id, name, email, hashed_password, is_active) VALUES (?, ?, ?, ?, 1)",
                         (TEACHER["id"], TEACHER["name"], "t9701@example.test", "x"))
            conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (9702, '网络1班', ?)", (TEACHER["id"],))
            conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (9703, '计算机网络', ?)", (TEACHER["id"],))
            conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (9704, 9702, 9703, ?)",
                         (TEACHER["id"],))
            conn.execute("INSERT INTO students (id, student_id_number, name, class_id) VALUES (9705, 'S9705', '小林', 9702)")
            conn.commit()

    def setUp(self):
        now = datetime.now().isoformat()
        with get_db_connection() as conn:
            for sql in ("DELETE FROM approval_request_events", "DELETE FROM approval_request_reviewers",
                        "DELETE FROM approval_requests", "DELETE FROM submissions", "DELETE FROM assignments"):
                conn.execute(sql)
            conn.execute("INSERT INTO assignments (id, course_id, class_offering_id, title, status, created_at) "
                         "VALUES ('9706', 9703, 9704, '第1次作业', 'published', ?)", (now,))
            conn.commit()
        storage = patch.object(routes, "delete_storage_tree", return_value=None)
        storage.start()
        self.addCleanup(storage.stop)

    def _delete(self, **kwargs):
        return asyncio.run(routes.delete_assignment("9706", user=TEACHER, **kwargs))

    def _exists(self) -> bool:
        with get_db_connection() as conn:
            return conn.execute("SELECT 1 FROM assignments WHERE id = '9706'").fetchone() is not None

    def test_assignment_without_student_work_deletes_directly(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, "
                         "is_absence_score, submitted_at) VALUES ('9706', 9705, '小林', 'unsubmitted', 0, 1, ?)",
                         (datetime.now().isoformat(),))
            conn.commit()
        self._delete()
        self.assertFalse(self._exists())

    def test_student_work_requires_matching_confirmation_and_cancels_appeals(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at) "
                         "VALUES ('9706', 9705, '小林', 'submitted', ?)", (datetime.now().isoformat(),))
            conn.execute(
                "INSERT INTO approval_requests (request_type, status, applicant_role, applicant_user_pk, subject_type, "
                "subject_id, assignment_id, title, reason) VALUES ('submission_correction', 'pending', 'student', 9705, "
                "'assignment_student', '9706:9705', '9706', 't', 'r')"
            )
            conn.commit()
        with self.assertRaises(HTTPException) as ctx:
            self._delete()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["submission_count"], 1)
        with self.assertRaises(HTTPException):
            self._delete(confirm_submissions=2)
        self.assertTrue(self._exists())

        self._delete(confirm_submissions=1)
        self.assertFalse(self._exists())
        with get_db_connection() as conn:
            status = conn.execute("SELECT status FROM approval_requests").fetchone()["status"]
        self.assertEqual(status, "cancelled")


if __name__ == "__main__":
    unittest.main()
