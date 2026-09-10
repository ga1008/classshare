import asyncio
import json
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from classroom_app.db.schema_assignments import ensure_assessment_classification_schema
from classroom_app.routers.homework_parts import assignments, exam_papers
from tests.test_assessment_classification_service import classification_database, grade_hash
from classroom_app.services import exam_paper_management_service as exam_domain


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


@contextmanager
def transactional_connection(conn):
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise


class AssessmentClassificationRouteTests(unittest.TestCase):
    def setUp(self):
        self.conn = classification_database()
        ensure_assessment_classification_schema(self.conn)
        self.conn.executescript("""
            CREATE TABLE courses (id INTEGER PRIMARY KEY, created_by_teacher_id INTEGER);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, course_id INTEGER, teacher_id INTEGER);
            CREATE TABLE material_ai_import_records (id INTEGER PRIMARY KEY, teacher_id INTEGER,
                document_type TEXT, document_group TEXT, export_payload_json TEXT, updated_at TEXT);
            INSERT INTO courses VALUES (10, 9);
            INSERT INTO class_offerings VALUES (100, 10, 7);
        """)
        self.conn.commit()
        self.db_patch = patch.object(assignments, "get_db_connection", lambda: transactional_connection(self.conn))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.conn.close()

    def test_batch_stale_item_rolls_back_all_changes_and_audits(self):
        before = grade_hash(self.conn)
        request = JsonRequest({"items": [
            {"assignment_id": 1, "assessment_kind": "homework", "expected_version": 0},
            {"assignment_id": 2, "assessment_kind": "midterm", "expected_version": 4},
        ]})
        with self.assertRaises(HTTPException) as error:
            asyncio.run(assignments.confirm_assignment_assessment_kinds(request, user={"id": 7}))
        self.assertEqual(409, error.exception.status_code)
        self.assertTrue(all(row[0] is None for row in self.conn.execute("SELECT assessment_kind FROM assignments")))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])
        self.assertEqual(before, grade_hash(self.conn))

    def test_batch_permission_and_personal_scope_are_checked_before_writes(self):
        cases = [([{ "assignment_id": 1, "assessment_kind": "final", "expected_version": 0}], 9, 403),
                 ([{ "assignment_id": 1, "assessment_kind": "homework", "expected_version": 0},
                   { "assignment_id": 3, "assessment_kind": "midterm", "expected_version": 0}], 7, 404)]
        for items, teacher, code in cases:
            with self.subTest(teacher=teacher, code=code), self.assertRaises(HTTPException) as error:
                asyncio.run(assignments.confirm_assignment_assessment_kinds(JsonRequest({"items": items}), user={"id": teacher}))
            self.assertEqual(code, error.exception.status_code)
            self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])

    def test_confirmed_kind_blocks_old_override_and_preserves_historical_value(self):
        result = asyncio.run(assignments.confirm_assignment_assessment_kinds(JsonRequest({"items": [
            {"assignment_id": 1, "assessment_kind": "final", "expected_version": 0},
        ], "reason": "核对教学安排后确认"}), user={"id": 7}))
        self.assertEqual(1, result["changed_count"])
        self.assertEqual("期末测验", result["assignments"][0]["assessment_kind_label"])
        with self.assertRaises(HTTPException) as error:
            asyncio.run(assignments.update_assignment_ordinary_grade_kind("1", JsonRequest({"kind": "exam"}), user={"id": 7}))
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual("assignment", self.conn.execute("SELECT ordinary_grade_kind_override FROM assignments WHERE id=1").fetchone()[0])

    def test_impact_uses_structured_source_ids_and_preserves_material_snapshot(self):
        payload = json.dumps({"structured": {"source_assignments": {"homework_assignment_ids": [1], "assessment_assignment_id": 2}}})
        self.conn.execute("INSERT INTO material_ai_import_records VALUES (81, 7, 'ordinary_grade_record', 'final_material', ?, '2026-01-01')", (payload,))
        transcript = json.dumps({"structured": {"source_lineage": {"ordinary_grade_record": {"record_id": 81}}}})
        self.conn.execute("INSERT INTO material_ai_import_records VALUES (82, 7, 'final_grade_transcript', 'final_material', ?, '2026-02-01')", (transcript,))
        self.conn.commit()
        preview = asyncio.run(assignments.get_assignment_assessment_kind("1", user={"id": 7}))
        self.assertEqual([82, 81], [item["record_id"] for item in preview["impact"]["referenced_materials"]])
        result = asyncio.run(assignments.update_assignment_assessment_kind("1", JsonRequest({
            "assessment_kind": "final", "expected_version": 0,
        }), user={"id": 7}))
        self.assertEqual("final", result["assessment_kind"])
        self.assertEqual(payload, self.conn.execute("SELECT export_payload_json FROM material_ai_import_records WHERE id=81").fetchone()[0])

    def test_inventory_excludes_personal_and_only_suggests_without_writing(self):
        result = asyncio.run(assignments.list_assessment_classifications(100, user={"id": 7}))
        self.assertEqual([1, 2], [item["assignment_id"] for item in result["assignments"]])
        self.assertEqual(["final", "homework"], [item["suggested_assessment_kind"] for item in result["assignments"]])
        self.assertTrue(all(item["assessment_kind"] is None for item in result["assignments"]))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])

    def test_paper_assignment_requires_explicit_three_kind_before_side_effects(self):
        for value in (None, "exam", "quiz", "assignment"):
            with self.subTest(value=value), patch.object(exam_papers, "get_db_connection") as database:
                payload = {"class_offering_id": 100}
                if value is not None:
                    payload["assessment_kind"] = value
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(exam_papers.assign_exam_paper("p", JsonRequest(payload), user={"id": 7}))
                self.assertEqual(400, error.exception.status_code)
                database.assert_not_called()

    def _add_authoring_columns(self):
        for column, definition in {
            "requirements_md": "TEXT", "rubric_md": "TEXT", "grading_mode": "TEXT DEFAULT 'manual'",
            "created_at": "TEXT", "allowed_file_types_json": "TEXT", "availability_mode": "TEXT",
            "starts_at": "TEXT", "due_at": "TEXT", "duration_minutes": "INTEGER", "auto_close": "INTEGER",
            "closed_at": "TEXT", "late_submission_enabled": "INTEGER", "late_submission_until": "TEXT",
            "late_penalty_strategy": "TEXT", "late_penalty_interval_hours": "REAL", "late_penalty_points": "REAL",
            "late_penalty_min_score": "REAL", "late_score_cap": "REAL", "learning_stage_key": "TEXT",
        }.items():
            self.conn.execute(f"ALTER TABLE assignments ADD COLUMN {column} {definition}")
        self.conn.execute("ALTER TABLE class_offerings ADD COLUMN class_id INTEGER DEFAULT 101")
        self.conn.commit()

    def test_ordinary_creation_defaults_only_missing_kind_to_homework_and_audits(self):
        self._add_authoring_columns()
        with patch.object(assignments, "close_overdue_assignments"), patch("classroom_app.services.assignment_management_service.sync_assignment_due_reminders"), patch.object(assignments, "_build_assignment_storage_dir", return_value=MagicMock()):
            for supplied, expected in (({}, "homework"), ({"assessment_kind": "final"}, "final")):
                result = asyncio.run(assignments.create_assignment(10, JsonRequest({"title": "test", "class_offering_id": 100, **supplied}), user={"id": 7}))
                row = self.conn.execute("SELECT * FROM assignments WHERE id = ?", (result["new_assignment_id"],)).fetchone()
                self.assertEqual(expected, row["assessment_kind"])
                self.assertEqual(1, row["assessment_kind_version"])
                self.assertEqual("manual", row["grading_mode"])
                self.assertEqual(expected, result["assessment_kind"])
            with self.assertRaises(HTTPException):
                asyncio.run(assignments.create_assignment(10, JsonRequest({"title": "test", "assessment_kind": None}), user={"id": 7}))
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM assignment_classification_revisions").fetchone()[0])

    def test_paper_assignment_keeps_selected_homework_and_original_ai_mode(self):
        self._add_authoring_columns()
        paper = {"id": "new-paper", "title": "共享试卷", "description": "test", "questions_json": "{}"}
        with patch.object(exam_papers, "get_db_connection", lambda: transactional_connection(self.conn)), \
             patch.object(exam_papers, "close_overdue_assignments"), \
             patch.object(exam_domain, "_get_exam_paper_for_teacher", return_value=paper), \
             patch.object(exam_domain, "normalize_exam_scoring_payload", side_effect=lambda value, **kw: value), \
             patch.object(exam_domain, "build_exam_rubric_md", return_value="existing rubric"), \
             patch.object(exam_domain, "teacher_can_manage_exam_paper", return_value=False), \
             patch.object(exam_domain, "create_assignment_published_notifications"), \
             patch.object(exam_domain, "sync_assignment_due_reminders"), \
             patch.object(exam_domain, "_auto_add_class_name_tag"), \
             patch.object(exam_papers, "_build_assignment_storage_dir", return_value=MagicMock()):
            result = asyncio.run(exam_papers.assign_exam_paper("new-paper", JsonRequest({"class_offering_id": 100, "assessment_kind": "homework"}), user={"id": 7}))
        row = self.conn.execute("SELECT * FROM assignments WHERE id = ?", (result["assignment_id"],)).fetchone()
        self.assertEqual("homework", row["assessment_kind"])
        self.assertEqual("new-paper", row["exam_paper_id"])
        self.assertEqual("ai", row["grading_mode"])
        self.assertEqual("existing rubric", row["rubric_md"])
        self.assertEqual("exam_paper", result["answer_mode"])

    def test_mp_teacher_list_shares_three_kind_but_preserves_answer_format(self):
        from classroom_app.routers.mp import teacher
        self._add_authoring_columns()
        self.conn.executescript("""
            ALTER TABLE courses ADD COLUMN name TEXT DEFAULT '课程';
            CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER, enrollment_status TEXT);
            CREATE TABLE class_offering_class_links (offering_id INTEGER, class_id INTEGER);
            ALTER TABLE submissions ADD COLUMN is_absence_score INTEGER DEFAULT 0;
            ALTER TABLE submissions ADD COLUMN resubmission_allowed INTEGER DEFAULT 0;
            INSERT INTO classes VALUES (101, '班级');
            INSERT INTO students VALUES (20, 101, 'active');
            UPDATE assignments SET assessment_kind = 'homework' WHERE id = 1;
            UPDATE assignments SET assessment_kind = 'final' WHERE id = 2;
        """)
        with patch.object(teacher, "get_db_connection", lambda: transactional_connection(self.conn)):
            result = teacher.mp_teacher_tasks(user={"id": 7})
        tasks = {item["id"]: item for item in result["data"]["tasks"]}
        self.assertEqual({1, 2}, set(tasks))
        self.assertEqual("平时作业", tasks[1]["assessment_kind_label"])
        self.assertTrue(tasks[1]["is_exam"])
        self.assertEqual("期末测验", tasks[2]["assessment_kind_label"])
        self.assertFalse(tasks[2]["is_exam"])
        with self.assertRaises(HTTPException):
            teacher._get_teacher_assignment(self.conn, 3, 7)
        with self.assertRaises(HTTPException):
            teacher._get_teacher_assignment(self.conn, 1, 9)

    def test_confirmation_rejects_duplicates_bool_version_and_empty_batch(self):
        base = {"assignment_id": 1, "assessment_kind": "homework", "expected_version": 0}
        for items in ([], [base, base], [{**base, "expected_version": True}]):
            with self.subTest(items=items), self.assertRaises(HTTPException) as error:
                asyncio.run(assignments.confirm_assignment_assessment_kinds(JsonRequest({"items": items}), user={"id": 7}))
            self.assertEqual(400, error.exception.status_code)

    def _prepare_running_attempt(self):
        self._add_authoring_columns()
        self.conn.executescript("""
            ALTER TABLE submissions ADD COLUMN grading_job_id TEXT;
            ALTER TABLE submissions ADD COLUMN grading_revision_hash TEXT;
            ALTER TABLE submissions ADD COLUMN grading_started_at TEXT;
            UPDATE assignments SET requirements_md='题目原文', rubric_md='原量表',
                allowed_file_types_json='["image","document"]', availability_mode='always_open' WHERE id=1;
            UPDATE submissions SET status='grading', grading_revision_hash='old-content',
                grading_started_at='2026-09-01' WHERE id=11;
        """)
        self.conn.commit()

    def test_title_and_classification_edits_keep_attempt_but_rubric_change_retains_grade_and_invalidates(self):
        self._prepare_running_attempt()
        original_answers = self.conn.execute("SELECT score, feedback_md, answers_json FROM submissions WHERE id=11").fetchone()
        with patch.object(assignments, "close_overdue_assignments"), \
             patch("classroom_app.services.assignment_management_service.refresh_assignment_runtime_status", side_effect=lambda conn, value: value), \
             patch("classroom_app.services.assignment_management_service.sync_assignment_due_reminders"):
            for payload in ({"title": "改标题"}, {"title": "改标题", "assessment_kind": "midterm", "expected_version": 0}):
                asyncio.run(assignments.update_assignment("1", JsonRequest(payload), user={"id": 7}))
                row = self.conn.execute("SELECT * FROM submissions WHERE id=11").fetchone()
                self.assertEqual("grading", row["status"])
                self.assertEqual("old-fingerprint", row["grading_attempt_fingerprint"])
                self.assertEqual("原量表", self.conn.execute("SELECT rubric_md FROM assignments WHERE id=1").fetchone()[0])
            asyncio.run(assignments.update_assignment("1", JsonRequest({"title": "改标题", "rubric_md": "新量表"}), user={"id": 7}))
        row = self.conn.execute("SELECT * FROM submissions WHERE id=11").fetchone()
        self.assertEqual("graded", row["status"])
        self.assertIsNone(row["grading_attempt_fingerprint"])
        self.assertIsNone(row["grading_revision_hash"])
        self.assertEqual(tuple(original_answers), (row["score"], row["feedback_md"], row["answers_json"]))

    def test_changed_attachment_types_invalidate_ungraded_attempt_without_making_a_score(self):
        self._prepare_running_attempt()
        self.conn.execute("UPDATE submissions SET score=NULL WHERE id=11")
        self.conn.commit()
        with patch.object(assignments, "close_overdue_assignments"), \
             patch("classroom_app.services.assignment_management_service.refresh_assignment_runtime_status", side_effect=lambda conn, value: value), \
             patch("classroom_app.services.assignment_management_service.sync_assignment_due_reminders"):
            asyncio.run(assignments.update_assignment("1", JsonRequest({"title": "同标题", "allowed_file_types": ["pdf"]}), user={"id": 7}))
        row = self.conn.execute("SELECT * FROM submissions WHERE id=11").fetchone()
        self.assertEqual("grading_review", row["status"])
        self.assertIsNone(row["score"])
        self.assertEqual("教师原评语", row["feedback_md"])

    def test_shared_paper_sync_invalidates_all_linked_attempts_but_retains_prior_grades(self):
        self._prepare_running_attempt()
        self.conn.execute("UPDATE assignments SET exam_paper_id='paper-a' WHERE id=2")
        self.conn.execute("UPDATE submissions SET status='grading', grading_attempt_fingerprint='other' WHERE id=12")
        self.conn.commit()
        with patch.object(exam_domain, "build_exam_rubric_md", return_value="新量表"):
            synced = exam_papers._sync_exam_assignment_content(self.conn, paper_id="paper-a", title="卷", description="新内容", exam_data={})
        self.assertEqual(2, synced)
        rows = self.conn.execute("SELECT score,status,grading_attempt_fingerprint FROM submissions WHERE id IN (11,12) ORDER BY id").fetchall()
        self.assertEqual([(83, "graded", None), (0, "graded", None)], [tuple(row) for row in rows])
        self.assertEqual("grading", self.conn.execute("SELECT status FROM submissions WHERE id=13").fetchone()[0])

    def test_legacy_paper_update_cannot_bypass_existing_answer_content_lock(self):
        before = grade_hash(self.conn)
        paper = {"id": "paper-a", "title": "原试卷", "description": "原内容", "questions_json": "{}"}
        with patch.object(exam_papers, "get_db_connection", lambda: transactional_connection(self.conn)), \
             patch.object(exam_papers, "_get_exam_paper_for_teacher", return_value=paper), \
             patch.object(exam_papers, "normalize_exam_scoring_payload", side_effect=lambda value, **kw: value):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(exam_papers.update_exam_paper("paper-a", JsonRequest({"title": "卷", "description": "改内容"}), user={"id": 7}))
        self.assertEqual(409, error.exception.status_code)
        self.assertEqual(before, grade_hash(self.conn))


if __name__ == "__main__":
    unittest.main()
