"""Isolated regression tests: no configured database, model, or network access."""
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")
from fastapi import HTTPException
from classroom_app.db import schema_ai_jobs, schema_study_group_scheme
from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.services.grading_revision_service import activate_submission_grade_revision
from classroom_app.services.score_projection_service import load_submission_score_facts
from classroom_app.services.student_report_card_service import build_student_report_card
from classroom_app.services import submission_export_docx_service as export_service


class ScoreProjectionTests(unittest.TestCase):
    def setUp(self):
        self._previous_ai_ready = set(schema_ai_jobs._SCHEMA_READY_ENGINES)
        self._previous_group_ready = schema_study_group_scheme._SCHEMA_READY
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
        ensure_grade_publication_schema(self.conn, engine="sqlite")
        ensure_assignment_schema(self.conn)
        schema_ai_jobs._SCHEMA_READY_ENGINES.clear()
        schema_ai_jobs.ensure_ai_job_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        schema_study_group_scheme._SCHEMA_READY = False
        schema_study_group_scheme.ensure_study_group_scheme_schema(self.conn)
        self.conn.executescript("""
            CREATE TABLE learning_stage_exam_attempts (id INTEGER PRIMARY KEY, assignment_id TEXT);
            CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, semester_id INTEGER);
            CREATE TABLE academic_semesters (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO courses VALUES (1, '课程');
            INSERT INTO academic_semesters VALUES (1, '秋季'), (2, '春季');
            INSERT INTO class_offerings VALUES (1, 1), (2, 2);
        """)

    def tearDown(self):
        self.conn.close()
        schema_ai_jobs._SCHEMA_READY_ENGINES = self._previous_ai_ready
        schema_study_group_scheme._SCHEMA_READY = self._previous_group_ready

    def add(self, aid, score, *, student=1, kind="homework", status="graded", offering=1, absence=0):
        self.conn.execute("INSERT OR IGNORE INTO assignments (id, course_id, class_offering_id, title, assessment_kind) VALUES (?, 1, ?, '任务', ?)", (aid, offering, kind))
        return self.conn.execute("INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, feedback_md, is_absence_score, submitted_at) VALUES (?, ?, '学生', ?, ?, '旧评语', ?, '2026-09-07')", (aid, student, status, score, absence)).lastrowid

    def fact(self, sid, student_view=True):
        return load_submission_score_facts(self.conn, submission_ids=[sid], student_view=student_view)[0]

    def bind(self, aid, sid, *, revealed=0, final=88):
        self.conn.execute("INSERT INTO assignment_group_bindings (assignment_id, class_offering_id, scheme_id, created_by_teacher_id) VALUES (?, 1, 1, 1)", (aid,))
        self.conn.execute("INSERT INTO study_groups (id, class_offering_id, name, created_by_role, created_by_user_pk, scheme_id) VALUES (1, 1, '组', 'teacher', 1, 1)")
        self.conn.execute("INSERT INTO study_group_members (group_id, student_id, member_role, status) VALUES (1, 1, 'member', 'active')")
        self.conn.execute("INSERT INTO group_assignment_member_results (assignment_id, class_offering_id, group_id, student_pk_id, submission_id, work_score, final_score, revealed) VALUES (?, 1, 1, 1, ?, 90, ?, ?)", (aid, sid, final, revealed))

    def test_old_effective_score_survives_regrading_and_null_is_not_zero(self):
        sid = self.add(1, 70, status="grading")
        self.assertEqual(self.fact(sid)["effective_score"], 70)
        self.assertTrue(self.fact(sid)["is_regrading"])
        activate_submission_grade_revision(self.conn, submission={"id": sid}, data={"source": "manual"}, score=82, feedback_md="有效评语")
        self.conn.execute("UPDATE submissions SET score = NULL, feedback_md = '' WHERE id = ?", (sid,))
        self.assertEqual(self.fact(sid)["score"], 82)
        self.assertEqual(self.fact(sid)["feedback_md"], "有效评语")
        pending = self.add(2, None, status="submitted")
        self.assertFalse(self.fact(pending)["has_effective_score"])
        self.assertIsNone(self.fact(pending)["score"])

    def test_statistics_projection_omits_answer_and_feedback_content(self):
        sid = self.add(1, 70, status="grading")
        self.conn.execute("UPDATE submissions SET answers_json = ? WHERE id = ?", ('x' * 100_000, sid))
        heavy = self.fact(sid)
        queries = []
        self.conn.set_trace_callback(queries.append)
        light = load_submission_score_facts(self.conn, submission_ids=[sid], student_view=True, include_content=False)[0]
        self.conn.set_trace_callback(None)
        self.assertEqual(light['effective_score'], heavy['effective_score'])
        self.assertEqual(light['is_regrading'], heavy['is_regrading'])
        self.assertNotIn('answers_json', light)
        self.assertEqual(light['feedback_md'], '')
        self.assertFalse(any('s.*' in query or 's.answers_json' in query for query in queries))

    def test_teacher_absence_zero_is_visible_but_not_an_exportable_answer(self):
        sid = self.add(1, 0, absence=1)
        fact = self.fact(sid)
        self.assertEqual(fact["effective_score"], 0)
        self.assertTrue(fact["score_visible"])
        self.assertFalse(fact["can_export_answer"])
        self.assertEqual(build_student_report_card(self.conn, student_id=1)["summary"]["overall_avg"], 0)
        with patch.object(export_service, "_load_export_context", return_value={"submission": {"submission_id": sid, "is_absence_score": 1}}):
            with self.assertRaises(HTTPException) as error:
                export_service.build_student_submission_export_docx(self.conn, assignment_id="1", student_pk_id=1)
            self.assertIn("没有可导出", error.exception.detail)

    def test_hidden_group_never_leaks_into_card_peer_average_or_export(self):
        sid = self.add(1, 90)
        self.add(1, 40, student=2)
        self.bind(1, sid)
        fact = self.fact(sid)
        self.assertIsNone(fact["score"])
        self.assertEqual(fact["feedback_md"], "")
        self.assertNotIn("revision_score", fact)
        card = build_student_report_card(self.conn, student_id=1)
        self.assertIsNone(card["summary"]["overall_avg"])
        self.assertIsNone(card["courses"][0]["records"][0]["class_avg"])
        self.assertNotIn("90.0", json.dumps(card))
        with patch.object(export_service, "_load_export_context", return_value={"submission": {"submission_id": sid, "is_absence_score": 0}}):
            with self.assertRaises(HTTPException) as error:
                export_service.build_student_submission_export_docx(self.conn, assignment_id="1", student_pk_id=1)
            self.assertIn("尚未公布", error.exception.detail)

    def test_legacy_group_final_wins_over_raw_revision(self):
        sid = self.add(1, 88, status="grading")
        activate_submission_grade_revision(self.conn, submission={"id": sid}, data={"source": "ai"}, score=90, feedback_md="原始分")
        self.bind(1, sid, revealed=1)
        self.assertEqual(self.fact(sid)["effective_score"], 88)
        self.conn.execute("UPDATE study_group_members SET status = 'removed'")
        self.assertIsNone(self.fact(sid)["score"])

    def test_offering_categories_and_personal_stage_are_separate(self):
        self.add(1, 80)
        self.add(2, 90, kind="midterm")
        self.add(3, 70, kind="final", offering=2)
        self.add(4, 100, kind=None)
        self.conn.execute("INSERT INTO learning_stage_exam_attempts VALUES (1, '4')")
        card = build_student_report_card(self.conn, student_id=1)
        self.assertEqual(len(card["courses"]), 2)
        self.assertEqual(card["summary"]["overall_avg"], 80)
        self.assertEqual(len(card["personal_records"]), 1)
        self.assertEqual(card["personal_records"][0]["kind_label"], "个人阶段试炼")
        filtered = build_student_report_card(self.conn, student_id=1, assessment_kind="midterm")
        self.assertEqual(filtered["summary"]["overall_avg"], 90)
        self.assertEqual(filtered["personal_records"], [])

    def test_returned_grade_is_not_effective_and_scopes_are_required(self):
        sid = self.add(1, 90)
        self.conn.execute("UPDATE submissions SET resubmission_allowed = 1 WHERE id = ?", (sid,))
        self.assertIsNone(self.fact(sid)["effective_score"])
        with self.assertRaises(ValueError):
            load_submission_score_facts(self.conn)
        self.assertEqual(load_submission_score_facts(self.conn, submission_ids=[]), [])

    def test_execution_audit_is_saved_for_teacher_and_not_sent_to_student(self):
        sid = self.add(1, 80)
        activate_submission_grade_revision(self.conn, submission={"id": sid}, score=80, feedback_md="评分",
            data={"source": "ai", "execution_plan": {"profile_id": "critical"},
                  "execution_state": {"version": 1, "attempts": [{"status": "unknown", "cost_estimate_cny": None}]},
                  "business_context": {"assessment_kind": "homework"}, "ai_policy_version": "v2"})
        teacher_fact = self.fact(sid, student_view=False)
        self.assertEqual(json.loads(teacher_fact["grade_quality_audit_json"])["ai_policy_version"], "v2")
        execution_state = json.loads(teacher_fact["grade_quality_audit_json"])["execution_state"]
        self.assertIsNone(execution_state["attempts"][0]["cost_estimate_cny"])
        self.assertNotIn("grade_quality_audit_json", self.fact(sid))

    def test_mp_submission_json_masks_hidden_grade_and_distinguishes_absence_from_answer(self):
        from classroom_app.routers.mp.tasks import _serialize_my_submission
        sid = self.add(1, 90)
        self.bind(1, sid)
        payload = _serialize_my_submission(self.conn, self.fact(sid))
        self.assertIsNone(payload["score"])
        self.assertEqual(payload["feedback_md"], "")
        self.assertEqual(payload["grade_display_state"], "group_pending")
        sid2 = self.add(2, 0, absence=1)
        absent = _serialize_my_submission(self.conn, self.fact(sid2))
        self.assertEqual(absent["score"], 0)
        self.assertFalse(absent["has_answer_submission"])
        self.assertFalse(absent["can_export_answer"])
        self.assertEqual(absent["answers"], [])


if __name__ == "__main__":
    unittest.main()
