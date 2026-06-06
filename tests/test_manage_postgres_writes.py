import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks

from classroom_app.routers.manage_parts import (
    classes_courses_classes,
    classes_courses_courses,
    classes_courses_offerings,
    semesters_textbooks,
)


class FakeRow(dict):
    def keys(self):
        return super().keys()


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.execute_calls = []
        self.executemany_calls = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def cursor(self):
        raise AssertionError("manage write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, params))
        return FakeCursor()

    def executemany(self, sql, params_seq):
        self.executemany_calls.append((sql, list(params_seq)))

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def run_async(coro):
    return asyncio.run(coro)


class ManagePostgresWriteTests(unittest.TestCase):
    def test_class_student_create_uses_insert_returning_helper(self):
        conn = FakeConnection()
        inserted = []

        def fake_insert(active_conn, sql, params, **kwargs):
            inserted.append((active_conn, sql, params, kwargs))
            return 501

        with patch.object(classes_courses_classes, "get_db_connection", return_value=conn), patch.object(
            classes_courses_classes,
            "_ensure_teacher_owned_class",
            return_value=FakeRow(
                {
                    "id": 7,
                    "school_code": "gxufl",
                    "school_name": "School",
                    "college": "College",
                    "department": "CS",
                }
            ),
        ), patch.object(
            classes_courses_classes,
            "apply_teacher_scope_to_org",
            return_value={
                "school_code": "gxufl",
                "school_name": "School",
                "college": "College",
                "department": "CS",
            },
        ), patch.object(
            classes_courses_classes,
            "execute_insert_returning_id",
            side_effect=fake_insert,
        ):
            result = run_async(
                classes_courses_classes.api_create_class_student(
                    class_id=7,
                    name="Alice",
                    student_id_number="S001",
                    gender="",
                    email="alice@example.test",
                    phone="",
                    user={"id": 3},
                )
            )

        self.assertEqual("success", result["status"])
        self.assertEqual(501, result["student"]["id"])
        self.assertTrue(conn.committed)
        self.assertEqual(1, len(inserted))
        self.assertIn("INSERT INTO students", inserted[0][1])

    def test_course_save_create_uses_insert_returning_helper(self):
        conn = FakeConnection()
        replace_course_lessons = Mock()

        with patch.object(classes_courses_courses, "get_db_connection", return_value=conn), patch.object(
            classes_courses_courses,
            "apply_teacher_scope_to_org",
            return_value={"school_code": "gxufl", "school_name": "School", "college": "College"},
        ), patch.object(
            classes_courses_courses,
            "get_learning_material_brief_map",
            return_value={},
        ), patch.object(
            classes_courses_courses,
            "replace_course_lessons",
            replace_course_lessons,
        ), patch.object(
            classes_courses_courses,
            "execute_insert_returning_id",
            return_value=601,
        ) as insert_helper:
            result = run_async(
                classes_courses_courses.api_save_course(
                    FakeRequest(
                        {
                            "name": "Networks",
                            "description": "Intro",
                            "department": "CS",
                            "total_hours": 1,
                            "lessons": [
                                {"title": "L1", "content": "Basics", "section_count": 1}
                            ],
                        }
                    ),
                    user={"id": 3},
                )
            )

        self.assertEqual(601, result["course_id"])
        self.assertTrue(conn.committed)
        self.assertEqual(1, insert_helper.call_count)
        replace_course_lessons.assert_called_once()

    def test_class_offering_create_returns_inserted_id(self):
        conn = FakeConnection()
        semester_row = FakeRow({"id": 9, "name": "2026 Fall"})
        textbook_row = FakeRow({"id": 11, "title": "Textbook"})

        with patch.object(classes_courses_offerings, "get_db_connection", return_value=conn), patch.object(
            classes_courses_offerings,
            "_validate_teacher_owned_selection",
            return_value=(FakeRow({}), FakeRow({}), semester_row, textbook_row),
        ), patch.object(
            classes_courses_offerings,
            "execute_insert_returning_id",
            return_value=701,
        ) as insert_helper:
            result = run_async(
                classes_courses_offerings.api_create_class_offering(
                    FakeRequest({}),
                    class_id=5,
                    course_id=6,
                    semester_id=9,
                    textbook_id=11,
                    user={"id": 3},
                )
            )

        self.assertEqual("success", result["status"])
        self.assertEqual(701, result["class_offering_id"])
        self.assertTrue(conn.committed)
        self.assertEqual(1, insert_helper.call_count)

    def test_semester_create_uses_insert_returning_helper(self):
        conn = FakeConnection()
        mark_sync = Mock()

        with patch.object(semesters_textbooks, "get_db_connection", return_value=conn), patch.object(
            semesters_textbooks,
            "load_teacher_org_scope",
            return_value={"school_code": "gxufl", "school_name": "School"},
        ), patch.object(
            semesters_textbooks,
            "mark_semester_calendar_sync_queued",
            mark_sync,
        ), patch.object(
            semesters_textbooks,
            "execute_insert_returning_id",
            return_value=801,
        ) as insert_helper:
            result = run_async(
                semesters_textbooks.api_save_semester(
                    BackgroundTasks(),
                    semester_id="",
                    name="2026 Fall",
                    start_date="2026-09-01",
                    end_date="2027-01-10",
                    user={"id": 3},
                )
            )

        self.assertEqual(801, result["semester_id"])
        self.assertTrue(conn.committed)
        self.assertEqual(1, insert_helper.call_count)
        mark_sync.assert_called_once()

    def test_textbook_create_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(semesters_textbooks, "get_db_connection", return_value=conn), patch.object(
            semesters_textbooks,
            "execute_insert_returning_id",
            return_value=901,
        ) as insert_helper:
            result = run_async(
                semesters_textbooks.api_save_textbook(
                    textbook_id="",
                    title="Database Systems",
                    authors_json="[]",
                    publisher="",
                    publication_date="",
                    introduction="",
                    catalog_text="",
                    tags_json="[]",
                    remove_attachment=False,
                    attachment=None,
                    user={"id": 3},
                )
            )

        self.assertEqual(901, result["textbook_id"])
        self.assertTrue(conn.committed)
        self.assertEqual(1, insert_helper.call_count)


if __name__ == "__main__":
    unittest.main()
