"""Exercise the course-support aggregate with SQLite and an explicit native PG clone."""
from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

from classroom_app.services.student_support_service import _load_student_course_signal_rows


TABLES = {
    "students": "id INTEGER PRIMARY KEY, class_id INTEGER, enrollment_status TEXT",
    "class_offerings": "id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER, teacher_id INTEGER, semester_id INTEGER, semester TEXT, first_class_date TEXT, created_at TEXT",
    "class_offering_class_links": "offering_id INTEGER, class_id INTEGER",
    "courses": "id INTEGER PRIMARY KEY, name TEXT, sect_name TEXT",
    "classes": "id INTEGER PRIMARY KEY, name TEXT",
    "teachers": "id INTEGER PRIMARY KEY, name TEXT",
    "academic_semesters": "id INTEGER PRIMARY KEY, name TEXT, start_date TEXT",
    "learning_stage_status": "class_offering_id INTEGER, student_id INTEGER, progress_score INTEGER, readiness_score INTEGER",
    "learning_certificates": "id INTEGER, class_offering_id INTEGER, student_id INTEGER",
    "learning_material_progress": "class_offering_id INTEGER, student_id INTEGER, completed INTEGER, material_id INTEGER, active_seconds INTEGER",
    "assignments": "id INTEGER PRIMARY KEY, class_offering_id INTEGER, status TEXT",
    "learning_stage_exam_attempts": "assignment_id INTEGER",
    "submissions": "id INTEGER PRIMARY KEY, assignment_id INTEGER, student_pk_id INTEGER, is_absence_score INTEGER, score REAL",
    "classroom_behavior_states": "class_offering_id INTEGER, user_pk INTEGER, user_role TEXT, total_activity_count INTEGER, online_accumulated_seconds INTEGER, focus_total_seconds INTEGER, last_page_key TEXT, PRIMARY KEY (class_offering_id, user_pk, user_role)",
    "classroom_behavior_events": "class_offering_id INTEGER, user_pk INTEGER, user_role TEXT, created_at TEXT, action_type TEXT",
}
ROWS = {
    "students": [(1, 1, "active"), (2, 1, "inactive"), (3, 1, None)],
    "class_offerings": [(1, 1, 1, 1, 1, "old", None, "2026-01-01"),
                        (2, 1, 1, 1, 2, "new", None, "2026-01-01"),
                        (3, 2, 1, 1, None, "fallback", "2026-06-01", "2026-01-01")],
    "class_offering_class_links": [(3, 1), (1, 1)],
    "courses": [(1, "Synthetic course", "Synthetic section")],
    "classes": [(1, "Synthetic class A"), (2, "Synthetic class B")],
    "teachers": [(1, "Synthetic teacher")],
    "academic_semesters": [(1, "Old semester", "2026-02-01"), (2, "New semester", "2026-09-01")],
    "assignments": [(1, 1, "published"), (2, 1, "published"), (3, 1, "new"), (4, 2, "published")],
    "learning_stage_exam_attempts": [(2,)],
    "submissions": [(1, 1, 1, 0, 80), (2, 1, 1, 1, 0), (3, 2, 1, 0, 100), (4, 4, 1, 0, 0)],
}


class PostgreSQLConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace("?", "%s"), params)


def create_fixture(conn):
    # PostgreSQL temporary tables shadow the clone's public tables; no copied
    # production row is inserted, updated or deleted by this regression suite.
    for name, columns in TABLES.items():
        conn.execute(f"CREATE TEMP TABLE {name} ({columns})")
    for table, rows in ROWS.items():
        for row in rows:
            conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", row)


def assert_course_behavior(case, conn):
    rows = _load_student_course_signal_rows(conn, 1)
    case.assertEqual([2, 3, 1], [row["class_offering_id"] for row in rows])
    by_id = {row["class_offering_id"]: row for row in rows}
    case.assertEqual((1, 1, 80), (by_id[1]["assignment_count"], by_id[1]["submitted_count"], by_id[1]["average_score"]))
    case.assertEqual((1, 0), (by_id[2]["submitted_count"], by_id[2]["average_score"]))
    case.assertEqual((0, 0, None), (by_id[3]["assignment_count"], by_id[3]["submitted_count"], by_id[3]["average_score"]))
    current = _load_student_course_signal_rows(conn, 1, current_class_offering_id=1)
    case.assertEqual([1, 2, 3], [row["class_offering_id"] for row in current])
    case.assertEqual([], _load_student_course_signal_rows(conn, 2))
    legacy_active = _load_student_course_signal_rows(conn, 3)
    case.assertEqual([2, 3, 1], [row["class_offering_id"] for row in legacy_active])
    case.assertTrue(all(row["average_score"] is None for row in legacy_active))


def assert_independent_history_totals(case, conn):
    additions = {
        "assignments": [(5, 1, "published"), (6, 1, "published"), (7, 1, "published")],
        "submissions": [(5, 5, 1, 0, 40), (6, 6, 1, 0, None), (7, 5, 2, 0, 100)],
        "learning_stage_status": [(1, 1, 70, 65), (1, 1, 80, 60), (1, 2, 999, 999)],
        "learning_certificates": [(10, 1, 1), (11, 1, 1), (12, 1, 2), (13, 2, 1)],
        "learning_material_progress": [(1, 1, 1, 101, 120), (1, 1, 1, 102, 180),
                                       (1, 1, 0, 103, 40), (1, 2, 1, 999, 9999), (2, 1, 1, 201, 50)],
        "classroom_behavior_states": [(1, 1, "student", 13, 77, 33, "calendar"),
                                      (1, 1, "teacher", 999, 999, 999, "teacher-page")],
        "classroom_behavior_events": [(1, 1, "student", "2026-09-07T01:00:00", "ai_question"),
                                      (1, 1, "student", "2026-09-07T01:01:00", "ai_question"),
                                      (1, 1, "student", "2026-09-07T01:02:00", "page_view"),
                                      (1, 1, "teacher", "2026-09-08T00:00:00", "ai_question"),
                                      (1, 2, "student", "2026-09-09T00:00:00", "ai_question"),
                                      (2, 1, "student", "2026-09-07T02:00:00", "ai_question")],
    }
    for table, rows in additions.items():
        for row in rows:
            conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", row)
    rows = _load_student_course_signal_rows(conn, 1)
    case.assertEqual([2, 3, 1], [row["class_offering_id"] for row in rows])
    by_id = {row["class_offering_id"]: row for row in rows}
    expected = {"progress_score": 80, "readiness_score": 65, "certificate_count": 2,
                "material_completed_count": 2, "material_active_seconds": 340,
                "assignment_count": 4, "submitted_count": 3, "average_score": 60,
                "activity_count": 13, "online_seconds": 77, "focus_seconds": 33,
                "last_page_key": "calendar", "last_behavior_at": "2026-09-07T01:02:00", "ai_question_count": 2}
    for field, value in expected.items():
        case.assertEqual(value, by_id[1][field], field)
    case.assertEqual((1, 50, 1, 0), (by_id[2]["certificate_count"], by_id[2]["material_active_seconds"],
                                   by_id[2]["ai_question_count"], by_id[2]["average_score"]))
    case.assertEqual((0, 0, 0, None), (by_id[3]["certificate_count"], by_id[3]["material_active_seconds"],
                                      by_id[3]["ai_question_count"], by_id[3]["average_score"]))


class StudentSupportCourseSignalsSQLiteTests(unittest.TestCase):
    def test_ordering_combined_class_and_existing_submission_filters(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            create_fixture(conn)
            assert_course_behavior(self, conn)
            assert_independent_history_totals(self, conn)


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER") and os.environ.get("ASSESSMENT_REHEARSAL_TEST_PORT"),
                     "Requires an explicit dedicated PostgreSQL rehearsal cluster")
class StudentSupportCourseSignalsPostgreSQLTests(unittest.TestCase):
    def test_native_aggregates_preserve_order_filters_and_independent_history_totals(self):
        from psycopg.rows import dict_row
        from tools.assessment_postgres_rehearsal import connect_offline

        with connect_offline(cluster_dir=Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"]),
                             port=int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"]),
                             database=os.environ.get("ASSESSMENT_REHEARSAL_TEST_DATABASE", "lanshare_assessment_rehearsal")) as raw:
            raw.row_factory = dict_row
            conn = PostgreSQLConnection(raw)
            create_fixture(conn)

            assert_course_behavior(self, conn)
            assert_independent_history_totals(self, conn)
            raw.rollback()


if __name__ == "__main__":
    unittest.main()
