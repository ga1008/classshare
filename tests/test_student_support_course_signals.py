"""Exercise the course-support aggregate with SQLite and an explicit native PG clone."""
from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.services import student_support_service as support
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
    "submissions": "id INTEGER PRIMARY KEY, assignment_id INTEGER, student_pk_id INTEGER, is_absence_score INTEGER, score REAL, status TEXT DEFAULT 'graded', submitted_at TEXT DEFAULT '2026-09-07', resubmission_allowed INTEGER DEFAULT 0, is_late_submission INTEGER DEFAULT 0, active_grade_revision_id INTEGER",
    "submission_grade_revisions": "id INTEGER PRIMARY KEY, submission_id INTEGER, score REAL, status TEXT",
    "assignment_group_bindings": "id INTEGER PRIMARY KEY, assignment_id TEXT, scheme_id INTEGER, status TEXT",
    "group_assignment_member_results": "id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER, group_id INTEGER, final_score REAL, revealed INTEGER",
    "study_groups": "id INTEGER PRIMARY KEY, scheme_id INTEGER",
    "study_group_members": "id INTEGER PRIMARY KEY, group_id INTEGER, student_id INTEGER, status TEXT",
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


def insert_rows(conn, table, rows):
    columns = " (id,assignment_id,student_pk_id,is_absence_score,score)" if table == "submissions" else ""
    for row in rows:
        conn.execute(f"INSERT INTO {table}{columns} VALUES ({','.join('?' for _ in row)})", row)


def create_fixture(conn):
    # PostgreSQL temporary tables shadow the clone's public tables; no copied
    # production row is inserted, updated or deleted by this regression suite.
    for name, columns in TABLES.items():
        conn.execute(f"CREATE TEMP TABLE {name} ({columns})")
    for table, rows in ROWS.items():
        insert_rows(conn, table, rows)


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
        insert_rows(conn, table, rows)
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


def assert_student_visible_effective_scores(case, conn):
    insert_rows(conn, "classes", [(4, "Visibility fixture")])
    insert_rows(conn, "students", [(4, 4, "active"), (5, 4, "active")])
    insert_rows(conn, "class_offerings", [(4, 4, 1, 1, 1, "old", None, "2026-01-01")])
    insert_rows(conn, "assignments", [(101, 4, "published")])
    insert_rows(conn, "submissions", [(101, 101, 4, 0, 90), (102, 101, 5, 0, 20)])
    insert_rows(conn, "submission_grade_revisions", [(101, 101, 90, "active")])
    conn.execute("UPDATE submissions SET active_grade_revision_id=101 WHERE id=101")
    insert_rows(conn, "assignment_group_bindings", [(1, "101", 1, "active")])
    insert_rows(conn, "study_groups", [(1, 1), (2, 2)])
    insert_rows(conn, "study_group_members", [(1, 1, 4, "active"), (2, 1, 5, "active")])
    insert_rows(conn, "group_assignment_member_results", [(1, "101", 4, 1, 88, 0), (2, "101", 5, 1, 25, 1)])

    def own():
        with patch.object(support, "load_submission_score_facts", wraps=support.load_submission_score_facts) as loader:
            rows = _load_student_course_signal_rows(conn, 4)
        case.assertEqual(1, len(rows))
        loader.assert_called_once()
        case.assertTrue(loader.call_args.kwargs["student_view"])
        case.assertFalse(loader.call_args.kwargs["include_content"])
        case.assertEqual(4, loader.call_args.kwargs["student_id"])
        case.assertEqual({"101"} if len(rows) and rows[0]["assignment_count"] == 1 else {"101", "103", "104", "105", "106"},
                         set(loader.call_args.kwargs["assignment_ids"]))
        return rows[0]

    row = own()
    case.assertEqual((1, 1, None), (row["assignment_count"], row["submitted_count"], row["average_score"]))
    case.assertEqual(25, _load_student_course_signal_rows(conn, 5)[0]["average_score"])
    conn.execute("UPDATE group_assignment_member_results SET revealed=1 WHERE id=1")
    case.assertEqual(88, own()["average_score"])
    conn.execute("UPDATE group_assignment_member_results SET final_score=0 WHERE id=1")
    case.assertEqual(0, own()["average_score"])
    conn.execute("UPDATE group_assignment_member_results SET final_score=NULL WHERE id=1")
    case.assertIsNone(own()["average_score"])
    conn.execute("UPDATE group_assignment_member_results SET final_score=88 WHERE id=1")
    conn.execute("UPDATE study_group_members SET status='removed' WHERE id=1")
    case.assertIsNone(own()["average_score"])
    conn.execute("UPDATE study_group_members SET status='active', group_id=2 WHERE id=1")
    case.assertIsNone(own()["average_score"])
    conn.execute("UPDATE study_group_members SET group_id=1 WHERE id=1")
    conn.execute("UPDATE assignment_group_bindings SET scheme_id=2 WHERE id=1")
    case.assertIsNone(own()["average_score"])
    conn.execute("UPDATE assignment_group_bindings SET status='inactive' WHERE id=1")
    case.assertEqual(90, own()["average_score"])
    conn.execute("UPDATE assignment_group_bindings SET status='active', scheme_id=1 WHERE id=1")
    conn.execute("UPDATE group_assignment_member_results SET revealed=0 WHERE id=1")

    # Retained active revision survives regrading; true zero counts, absence and
    # returned grades do not. Other students' visible scores stay out of AVG.
    insert_rows(conn, "assignments", [(103, 4, "published"), (104, 4, "published"),
                                      (105, 4, "published"), (106, 4, "published"), (107, 4, "new"), (108, 4, "published")])
    insert_rows(conn, "submissions", [(103, 103, 4, 0, None), (104, 104, 4, 0, 0),
                                      (105, 105, 4, 1, 0), (106, 106, 4, 0, 99),
                                      (107, 107, 4, 0, 100), (108, 108, 4, 0, 100)])
    insert_rows(conn, "learning_stage_exam_attempts", [(108,)])
    insert_rows(conn, "submission_grade_revisions", [(103, 103, 82, "active")])
    conn.execute("UPDATE submissions SET active_grade_revision_id=103, status='grading' WHERE id=103")
    conn.execute("UPDATE submissions SET resubmission_allowed=1 WHERE id=106")
    row = own()
    case.assertEqual((5, 4, 41), (row["assignment_count"], row["submitted_count"], row["average_score"]))
    conn.execute("UPDATE submission_grade_revisions SET status='superseded' WHERE id=103")
    case.assertEqual(0, own()["average_score"])
    conn.execute("UPDATE submissions SET score=70, status='submitted' WHERE id=103")
    case.assertEqual(0, own()["average_score"])
    conn.execute("UPDATE submissions SET score=70, status='grading_review' WHERE id=103")
    case.assertEqual(35, own()["average_score"])
    with patch.object(support, "load_submission_score_facts") as loader:
        case.assertEqual([], _load_student_course_signal_rows(conn, 999))
        loader.assert_not_called()
    insert_rows(conn, "classes", [(6, "Empty-course fixture")])
    insert_rows(conn, "students", [(6, 6, "active")])
    insert_rows(conn, "class_offerings", [(6, 6, 1, 1, 1, "old", None, "2026-01-01")])
    with patch.object(support, "load_submission_score_facts") as loader:
        case.assertIsNone(_load_student_course_signal_rows(conn, 6)[0]["average_score"])
        loader.assert_not_called()


class StudentSupportCourseSignalsSQLiteTests(unittest.TestCase):
    def test_ordering_combined_class_and_existing_submission_filters(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            create_fixture(conn)
            assert_course_behavior(self, conn)
            assert_independent_history_totals(self, conn)
            assert_student_visible_effective_scores(self, conn)


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
            assert_student_visible_effective_scores(self, conn)
            raw.rollback()


if __name__ == "__main__":
    unittest.main()
