import json
import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")
from fastapi import HTTPException
from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
from classroom_app.services.grade_publication_service import (
    preview_grade_publication, publish_grade_snapshot, student_published_grades,
    teacher_grade_publication_status, withdraw_grade_publication,
)
from classroom_app.services.final_grade_transcript_service import _students_by_number


class GradePublicationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_grade_publication_schema(self.conn, engine="sqlite")
        self.conn.executescript("""
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE academic_semesters (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, teacher_id INTEGER, class_id INTEGER,
                course_id INTEGER, semester_id INTEGER, semester TEXT);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, student_id_number TEXT, class_id INTEGER, enrollment_status TEXT);
            CREATE TABLE class_offering_class_links (offering_id INTEGER, class_id INTEGER);
            CREATE TABLE course_material_assignments (material_id INTEGER, class_offering_id INTEGER);
            CREATE TABLE material_ai_import_records (id INTEGER PRIMARY KEY, teacher_id INTEGER, package_material_id INTEGER,
                parsed_material_id INTEGER, parse_status TEXT, document_group TEXT, document_type TEXT,
                export_payload_json TEXT, updated_at TEXT);
            INSERT INTO courses VALUES (1, '课程');
            INSERT INTO academic_semesters VALUES (10, '2025-2026第二学期');
            INSERT INTO class_offerings VALUES (1,1,1,1,10,'2025-2026-2'), (2,2,2,1,10,'2025-2026-2');
            INSERT INTO students VALUES (101,'甲','20260001',1,'active'), (102,'乙','20260002',1,'active'), (103,'丙','20260003',2,'active');
            INSERT INTO course_material_assignments VALUES (500,1);
        """)
        self.payload = {"fields": {"class_offering_id": 1, "academic_year": "2025-2026", "semester": "第二学期"},
            "structured": {"students": [{"student_number": "20260001", "student_name": "甲", "ordinary_score": 84, "final_score": 80},
                                         {"student_number": "20260002", "student_name": "乙", "ordinary_score": 0, "final_score": 0}]}}
        self.conn.execute("INSERT INTO material_ai_import_records VALUES (1,1,500,NULL,'completed','final_material','final_grade_transcript',?,'2026-09-07')", (json.dumps(self.payload),))

    def tearDown(self):
        self.conn.close()

    def preview(self):
        return preview_grade_publication(self.conn, class_offering_id=1, teacher_id=1, material_id=1)

    def publish(self, preview=None):
        review = preview or self.preview()
        return publish_grade_snapshot(self.conn, class_offering_id=1, teacher_id=1, material_id=1,
            expected_source_hash=review["source_hash"], expected_version=review["expected_version"], confirmed=True,
            accepted_warning_codes=[item["code"] for item in review["warnings"]], confirmation_note="已核对来源和名单")

    def update_payload(self):
        self.conn.execute("UPDATE material_ai_import_records SET export_payload_json = ?, updated_at = 'new' WHERE id = 1", (json.dumps(self.payload),))

    def test_read_is_pure_and_explicit_confirmation_required(self):
        before = self.conn.total_changes
        review = self.preview()
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(student_published_grades(self.conn, student_id=101), [])
        with self.assertRaises(HTTPException):
            publish_grade_snapshot(self.conn, class_offering_id=1, teacher_id=1, material_id=1,
                expected_source_hash=review["source_hash"], expected_version=0, confirmed=False)
        with self.assertRaises(HTTPException):
            publish_grade_snapshot(self.conn, class_offering_id=1, teacher_id=1, material_id=1,
                expected_source_hash=review["source_hash"], expected_version=0, confirmed=True)

    def test_v1_816_self_only_and_explicit_zero(self):
        publication = self.publish()
        me = student_published_grades(self.conn, student_id=101)[0]
        self.assertEqual(me["overall_score"], 81.6)
        self.assertEqual(me["final_exam_score"], 80)
        self.assertEqual(me["version"], 1)
        self.assertEqual(student_published_grades(self.conn, student_id=102)[0]["overall_score"], 0)
        self.assertEqual(student_published_grades(self.conn, student_id=103), [])
        serialized = json.dumps(me, ensure_ascii=False)
        self.assertNotIn("20260002", serialized)
        self.assertNotIn("source_lineage", serialized)
        self.assertNotIn("students", serialized)
        self.assertEqual(publication["student_count"], 2)

    def test_source_changes_freeze_old_grade_new_version_and_withdrawal(self):
        first = self.publish()
        stale_review = self.preview()
        self.payload["structured"]["students"][0]["final_score"] = 90
        self.update_payload()
        self.assertEqual(student_published_grades(self.conn, student_id=101)[0]["overall_score"], 81.6)
        self.assertTrue(teacher_grade_publication_status(self.conn, class_offering_id=1, teacher_id=1)["current"]["source_stale"])
        with self.assertRaises(HTTPException):
            self.publish(stale_review)
        second = self.publish()
        self.assertEqual(second["version"], 2)
        self.assertEqual(student_published_grades(self.conn, student_id=101)[0]["overall_score"], 87.6)
        with self.assertRaises(HTTPException):
            withdraw_grade_publication(self.conn, class_offering_id=1, teacher_id=1, publication_id=first["publication_id"], reason="旧版本")
        withdraw_grade_publication(self.conn, class_offering_id=1, teacher_id=1, publication_id=second["publication_id"], reason="重新核对")
        self.assertEqual(student_published_grades(self.conn, student_id=101), [])
        frozen = self.conn.execute("SELECT scores_json FROM grade_publication_students WHERE publication_id = ? AND student_pk_id = 101", (first["publication_id"],)).fetchone()
        self.assertEqual(json.loads(frozen["scores_json"])["overall_score"], 81.6)

    def test_roster_scope_semester_missing_and_duplicate_are_blocking(self):
        with self.assertRaises(HTTPException):
            preview_grade_publication(self.conn, class_offering_id=1, teacher_id=2, material_id=1)
        with self.assertRaises(HTTPException):
            preview_grade_publication(self.conn, class_offering_id=2, teacher_id=2, material_id=1)
        self.payload["fields"]["semester"] = "第一学期"
        self.payload["structured"]["students"][0]["final_score"] = None
        self.payload["structured"]["students"].append(self.payload["structured"]["students"][1])
        self.update_payload()
        codes = {item["code"] for item in self.preview()["blocking_reasons"]}
        self.assertTrue({"semester_mismatch", "missing_published_score", "roster_mismatch"}.issubset(codes))
        with self.assertRaises(HTTPException):
            self.publish()

    def test_academic_final_score_means_overall_and_is_not_recomputed(self):
        self.conn.execute("UPDATE material_ai_import_records SET document_type = 'academic_grade_register'")
        self.payload["structured"]["validation"] = {"passed": True}
        for row in self.payload["structured"]["students"]:
            row.update(final_exam_score=90, final_score=82, ordinary_score=80, midterm_score=70)
        self.update_payload()
        self.publish()
        me = student_published_grades(self.conn, student_id=101)[0]
        self.assertEqual(me["overall_score"], 82)
        self.assertEqual(me["final_exam_score"], 90)
        self.assertEqual(me["formula"]["source_final_score_meaning"], "overall_score")

    def test_transcript_converts_material_40_of_50_and_preserves_teacher_edit(self):
        record = {"export_payload_json": json.dumps({"structured": {
            "score_adjustment_policy": {"version": "task-percentage-to-paper-v1", "target_full_score": 50},
            "students": [{"student_number": "A", "student_name": "甲", "total_score": 40, "task_score_percent": 80}]}})}
        self.assertEqual(_students_by_number(record, "exam_grade_record")["A"]["score"], 80)
        payload = json.loads(record["export_payload_json"])
        payload["structured"]["students"][0]["total_score"] = 45
        record["export_payload_json"] = json.dumps(payload)
        self.assertEqual(_students_by_number(record, "exam_grade_record")["A"]["score"], 90)
        payload["structured"].pop("score_adjustment_policy")
        record["export_payload_json"] = json.dumps(payload)
        self.assertEqual(_students_by_number(record, "exam_grade_record")["A"]["score"], 45)


if __name__ == "__main__":
    unittest.main()
