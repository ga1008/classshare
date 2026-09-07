import hashlib
import json
import sqlite3
import unittest

from fastapi import HTTPException

from classroom_app.db.schema_assignments import ensure_assessment_classification_schema
from classroom_app.services.assessment_classification_service import (
    assessment_kind_info,
    enrich_assessment_classifications,
    normalize_assessment_kind,
    set_assignment_assessment_kind,
)


def classification_database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE assignments (id INTEGER PRIMARY KEY, course_id INTEGER,
            class_offering_id INTEGER, title TEXT, status TEXT, exam_paper_id TEXT,
            ordinary_grade_kind_override TEXT, ordinary_grade_kind_updated_at TEXT,
            ordinary_grade_kind_updated_by_teacher_id INTEGER);
        CREATE TABLE submissions (id INTEGER PRIMARY KEY, assignment_id TEXT,
            student_pk_id INTEGER, score INTEGER, feedback_md TEXT, answers_json TEXT,
            status TEXT, grading_attempt_fingerprint TEXT);
        CREATE TABLE learning_stage_exam_attempts (id INTEGER PRIMARY KEY, assignment_id TEXT);
        INSERT INTO assignments VALUES (1, 10, 100, '期末考试（历史未确认）', 'published', 'paper-a', 'assignment', 'old-time', 7);
        INSERT INTO assignments VALUES (2, 10, 100, '平时作业', 'published', NULL, NULL, NULL, NULL);
        INSERT INTO assignments VALUES (3, 10, 100, '个人期中试炼', 'published', 'paper-b', 'exam', NULL, NULL);
        INSERT INTO submissions VALUES (11, '1', 20, 83, '教师原评语', '{"q1":"原答案"}', 'graded', 'old-fingerprint');
        INSERT INTO submissions VALUES (12, '2', 21, 0, '缺交记0', '', 'graded', NULL);
        INSERT INTO submissions VALUES (13, '3', 20, NULL, '', '{"q1":"草稿"}', 'grading', 'running-fingerprint');
        INSERT INTO learning_stage_exam_attempts VALUES (1, '3');
    """)
    conn.commit()
    return conn


def grade_hash(conn):
    rows = [tuple(row) for row in conn.execute("SELECT * FROM submissions ORDER BY id")]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


class AssessmentClassificationServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = classification_database()

    def tearDown(self):
        self.conn.close()

    def test_migration_twice_preserves_all_old_answers_grades_and_overrides(self):
        before_hash = grade_hash(self.conn)
        old_rows = [tuple(row) for row in self.conn.execute("SELECT * FROM assignments ORDER BY id")]
        for _ in range(2):
            ensure_assessment_classification_schema(self.conn)
            self.conn.commit()
            self.assertEqual(before_hash, grade_hash(self.conn))
            rows = self.conn.execute("SELECT * FROM assignments ORDER BY id").fetchall()
            self.assertEqual(old_rows, [tuple(row)[:len(old_rows[0])] for row in rows])
            self.assertTrue(all(row["assessment_kind"] is None for row in rows))
            self.assertTrue(all(row["assessment_kind_version"] == 0 for row in rows))
            self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])

    def test_database_rejects_fourth_category_and_leaves_null_legacy_valid(self):
        ensure_assessment_classification_schema(self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE assignments SET assessment_kind = 'exam' WHERE id = 1")
        self.assertIsNone(self.conn.execute("SELECT assessment_kind FROM assignments WHERE id = 1").fetchone()[0])

    def test_normalization_never_accepts_old_or_ambiguous_values(self):
        for value in ("assignment", "quiz", "exam", "期末测验", "FINAL", "", None, 1, {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_assessment_kind(value)
        self.assertIsNone(normalize_assessment_kind(None, allow_none=True))
        for value in ("homework", "midterm", "final"):
            self.assertEqual(value, normalize_assessment_kind(value))

    def test_row_dictionary_and_personal_scope_remain_distinct_from_paper_format(self):
        ensure_assessment_classification_schema(self.conn)
        self.conn.execute("UPDATE assignments SET assessment_kind = 'homework' WHERE id = 1")
        rows = self.conn.execute("SELECT * FROM assignments ORDER BY id").fetchall()
        self.assertEqual(assessment_kind_info(rows[0]), assessment_kind_info(dict(rows[0])))
        result = enrich_assessment_classifications(self.conn, rows)
        self.assertEqual(("homework", "平时作业", "exam_paper"),
                         (result[0]["assessment_kind"], result[0]["assessment_kind_label"], result[0]["answer_mode"]))
        self.assertEqual("legacy_unknown", result[1]["classification_status"])
        self.assertEqual("历史任务", result[1]["assessment_kind_label"])
        self.assertEqual("personal_stage", result[2]["source_feature"])
        self.assertEqual("not_applicable", result[2]["classification_status"])
        self.assertIsNone(result[2]["assessment_kind"])
        # Submission rows may also contain their own id; lookup must use assignment_id.
        nested = enrich_assessment_classifications(self.conn, [{"id": 99, "assignment_id": 3}])
        self.assertEqual("personal_stage", nested[0]["source_feature"])

    def test_compare_and_swap_audit_does_not_change_grades_or_running_attempt(self):
        ensure_assessment_classification_schema(self.conn)
        before_hash = grade_hash(self.conn)
        first = set_assignment_assessment_kind(self.conn, {"id": 1}, assessment_kind="homework",
                                               expected_version=0, teacher_id=7, reason="教师确认")
        self.assertEqual(1, first["assessment_kind_version"])
        with self.assertRaises(HTTPException) as conflict:
            set_assignment_assessment_kind(self.conn, {"id": 1}, assessment_kind="final", expected_version=0, teacher_id=7)
        self.assertEqual(409, conflict.exception.status_code)
        second = set_assignment_assessment_kind(self.conn, {"id": 1}, assessment_kind="final", expected_version=1, teacher_id=7)
        self.assertEqual(2, second["assessment_kind_version"])
        unchanged = set_assignment_assessment_kind(self.conn, {"id": 1}, assessment_kind="final", expected_version=2, teacher_id=7)
        self.assertFalse(unchanged["changed"])
        audit = [dict(row) for row in self.conn.execute("SELECT * FROM assignment_classification_revisions ORDER BY version")]
        self.assertEqual([None, "homework"], [row["previous_kind"] for row in audit])
        self.assertEqual([1, 2], [row["version"] for row in audit])
        self.assertEqual("教师确认", audit[0]["reason"])
        self.assertEqual(before_hash, grade_hash(self.conn))
        self.assertEqual("assignment", self.conn.execute("SELECT ordinary_grade_kind_override FROM assignments WHERE id = 1").fetchone()[0])

    def test_personal_stage_and_missing_version_cannot_be_classified(self):
        ensure_assessment_classification_schema(self.conn)
        for assignment_id, version in ((3, 0), (1, None), (1, True), (1, "0")):
            with self.subTest(assignment_id=assignment_id, version=version), self.assertRaises(HTTPException):
                set_assignment_assessment_kind(self.conn, {"id": assignment_id}, assessment_kind="final",
                                               expected_version=version, teacher_id=7)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])

    def test_postgres_schema_covers_same_nullable_categories_and_audit(self):
        from classroom_app.db.postgres_schema import POSTGRES_RUNTIME_COLUMN_DEFINITIONS, POSTGRES_RUNTIME_TABLE_DEFINITIONS, REQUIRED_POSTGRES_COLUMNS
        definitions = POSTGRES_RUNTIME_COLUMN_DEFINITIONS["assignments"]
        self.assertIn("IS NULL", definitions["assessment_kind"])
        for value in ("homework", "midterm", "final"):
            self.assertIn(value, definitions["assessment_kind"])
        self.assertIn("assessment_kind_version", REQUIRED_POSTGRES_COLUMNS["assignments"])
        self.assertIn("assignment_classification_revisions", POSTGRES_RUNTIME_TABLE_DEFINITIONS)


if __name__ == "__main__":
    unittest.main()
