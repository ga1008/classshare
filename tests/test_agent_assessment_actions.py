"""Actual assessment rows and receipts; no model, mailer or production DB."""
import json
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.test_agent_platform_writes import PlatformWriteFixture
from classroom_app.db import schema_ai_jobs, schema_study_group_scheme
from classroom_app.db.schema_grade_publications import ensure_grade_publication_schema
from classroom_app.routers import agent_tasks
from classroom_app.services import agent_platform_write_service as writes, submission_grading_service as grading
from classroom_app.services.agent_action_registry import validate_action_params
from classroom_app.services.agent_user_confirmation_actions import user_confirmation_action_catalog
from classroom_app.services.assignment_creation_service import create_assignment_record
from classroom_app.services.grade_publication_service import student_published_grades


class AssessmentActionTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        with patch.object(schema_ai_jobs, "_SCHEMA_READY_ENGINES", set()), patch.object(schema_study_group_scheme, "_SCHEMA_READY", False):
            schema_ai_jobs.ensure_ai_job_schema(self.conn, engine="sqlite")
            schema_study_group_scheme.ensure_study_group_scheme_schema(self.conn)
        self.assignment_id = create_assignment_record(self.conn, teacher_id=7, course_id=20,
            data={"title": "Synthetic review", "class_offering_id": 40, "requirements_md": "Explain Q", "rubric_md": "Accuracy 100"})["id"]
        self.conn.execute("""INSERT INTO submissions(id,assignment_id,student_pk_id,student_name,status,answers_json,submitted_at)
            VALUES(80,?,7,'Synthetic student','submitted','{"q":"answer"}','2026-09-10T10:00:00')""", (self.assignment_id,))
        self.conn.commit()
        # Hooks stay at the same shared-domain boundary as normal Web tests.
        for name in ("create_student_grading_notification", "handle_stage_exam_grading_complete",
                     "handle_assignment_stage_grading_complete", "refresh_student_learning_state"):
            hook = patch.object(grading, name, Mock())
            hook.start()
            self.addCleanup(hook.stop)
        group = patch("classroom_app.services.group_assignment_service.record_member_work_score", Mock())
        group.start()
        self.addCleanup(group.stop)

    def params(self, **extra):
        review = grading.get_teacher_submission_review(self.conn, submission_id=80, teacher_id=7)
        return {"submission_id": 80, "expected_review_revision": review["expected_review_revision"],
                "expected_assignment_revision": review["expected_assignment_revision"], "score": 84.5, **extra}

    def test_grade_business_revision_ai_fencing_and_receipt_are_atomic_and_replayed(self):
        self.conn.execute("INSERT INTO ai_jobs(id,task_type,source_ref,status,dedupe_key) VALUES(81,'ai_grading','submission:80','running','synthetic-grade-81')")
        self.conn.execute("UPDATE submissions SET grading_job_id=81,status='grading' WHERE id=80")
        self.conn.commit()
        token, params = self.token(), self.params(feedback_md="Clear explanation")
        first = writes.dispatch_write(self.conn, token, "grade-80", "manual_grade_submission", params)
        self.assertEqual(84.5, first["result"]["score"])
        self.assertIsNotNone(first["result"]["grade_revision_id"])
        self.assertEqual("superseded", self.conn.execute("SELECT status FROM ai_jobs WHERE id=81").fetchone()[0])
        self.conn.rollback()
        self.assertEqual("grading", self.conn.execute("SELECT status FROM submissions WHERE id=80").fetchone()[0])
        self.assertEqual(0, self.count("submission_grade_revisions"))
        self.assertEqual(0, self.count("agent_action_executions"))
        writes.dispatch_write(self.conn, token, "grade-80", "manual_grade_submission", params)
        self.conn.commit()
        self.assertTrue(writes.dispatch_write(self.conn, token, "grade-80", "manual_grade_submission", params)["replayed"])
        self.assertEqual(1, self.count("submission_grade_revisions"))
        self.assertEqual(1, self.count("agent_action_executions"))

    def test_new_answer_or_rubric_invalidates_the_review_without_writing(self):
        token, params = self.token(), self.params()
        for sql in ("UPDATE submissions SET answers_json='changed' WHERE id=80",
                    "UPDATE assignments SET rubric_md='New rubric' WHERE id=" + str(self.assignment_id)):
            self.conn.execute("SAVEPOINT test_change")
            self.conn.execute(sql)
            with self.assertRaises(HTTPException) as caught:
                writes.dispatch_write(self.conn, token, "stale-review", "manual_grade_submission", params)
            self.assertEqual(409, caught.exception.status_code)
            self.conn.execute("ROLLBACK TO test_change")
            self.conn.execute("RELEASE test_change")
        self.assertEqual(0, self.count("agent_action_executions"))
        self.assertEqual(0, self.count("submission_grade_revisions"))

    def test_missing_versions_nonfinite_scores_student_and_foreign_teacher_are_rejected(self):
        for extra in ({"score": True}, {"score": float("nan")}, {"score": float("inf")}, {"score": -1}, {"score": 101}):
            clean, errors = validate_action_params("manual_grade_submission", self.params(**extra), reject_unknown=True)
            self.assertTrue(errors)
            self.assertNotIn("score", clean)
        params = self.params()
        params.pop("expected_assignment_revision")
        self.assertTrue(validate_action_params("manual_grade_submission", params)[1])
        with self.assertRaises(HTTPException):
            writes.dispatch_write(self.conn, self.token(self.student), "student-grade", "manual_grade_submission", self.params())
        self.conn.rollback()
        with self.assertRaises(HTTPException) as foreign:
            grading.get_teacher_submission_review(self.conn, submission_id=80, teacher_id=8)
        self.assertEqual(403, foreign.exception.status_code)
        self.assertEqual(0, self.count("submission_grade_revisions"))


class GradePublicationConfirmationHTTPTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        ensure_grade_publication_schema(self.conn, engine="sqlite")
        self.conn.execute("INSERT INTO academic_semesters(id,teacher_id,name,start_date,end_date) VALUES(10,7,'2025-2026第二学期','2026-02-01','2026-07-01')")
        self.conn.execute("UPDATE class_offerings SET semester_id=10,semester='2025-2026-2' WHERE id=40")
        self.conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id,assigned_by_teacher_id) VALUES(500,40,7)")
        self.payload = {"fields": {"class_offering_id": 40, "academic_year": "2025-2026", "semester": "第二学期"},
            "structured": {"students": [{"student_number": "S7", "student_name": "Student 7", "ordinary_score": 84, "final_score": 80},
                                         {"student_number": "S9", "student_name": "Student 9", "ordinary_score": 0, "final_score": 0}]}}
        self.conn.execute("""INSERT INTO material_ai_import_records(id,teacher_id,package_material_id,parse_status,document_group,document_type,export_payload_json,updated_at)
            VALUES(501,7,500,'completed','final_material','final_grade_transcript',?,'2026-09-10')""", (json.dumps(self.payload),))
        self.proposal = {"action": "publish_classroom_grades", "params": {"class_offering_id": 40, "material_id": 501}}
        self.conn.execute("UPDATE agent_tasks SET status='failed',result_detail_json=? WHERE id=10", (json.dumps({"proposed_actions": [self.proposal]}),))
        self.conn.commit()
        self.actor = self.teacher
        self.app = FastAPI()
        self.app.include_router(agent_tasks.router)
        self.app.dependency_overrides[agent_tasks.get_current_user] = lambda: self.actor
        guard = patch.object(agent_tasks, "get_db_connection", self.connection)
        guard.start()
        self.addCleanup(guard.stop)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.url = "/api/agent-tasks/10/actions/0"

    def review(self):
        response = self.client.post(self.url + "/preview", json={})
        self.assertEqual(200, response.status_code, response.text)
        value = response.json()
        self.assertEqual("user_confirmation", value["execution_mode"])
        self.assertFalse(value["secure_fields"])
        return value

    def request(self, review=None):
        value = review or self.review()
        return {"params": value["params"], "confirmation_token": value["confirmation_token"],
                "confirmation_inputs": {"accepted_warning_codes": [item["code"] for item in value["confirmation_review"]["warnings"]],
                                        "confirmation_note": "Synthetic teacher checked the roster and source"}}

    def test_human_catalog_is_not_a_runtime_write_and_model_cannot_supply_confirmed(self):
        definition = user_confirmation_action_catalog(actor_role="teacher")[0]
        self.assertFalse(definition["executable"])
        self.assertNotIn("confirmed", definition["fields"])
        self.assertEqual([], user_confirmation_action_catalog(actor_role="student"))
        self.assertNotIn("publish_classroom_grades", writes.TRANSACTIONAL_ACTIONS)
        with self.assertRaises(HTTPException):
            writes.dispatch_write(self.conn, "model-token", "publish", "publish_classroom_grades", {**self.proposal["params"], "confirmed": True})
        response = self.client.post(self.url + "/preview", json={"params": {"confirmed": True}})
        self.assertEqual(400, response.status_code)
        self.assertEqual(0, self.count("grade_publications"))

    def test_actual_preview_execute_replay_has_one_publication_receipt_and_self_only_grades(self):
        request = self.request()
        self.assertEqual(0, self.count("grade_publications"))
        first = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual("authenticated_user", first.json()["result"]["confirmation_source"])
        second = self.client.post(self.url + "/execute", json=request)
        self.assertEqual(200, second.status_code, second.text)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(1, self.count("grade_publications"))
        self.assertEqual(1, self.count("agent_action_executions"))
        self.assertEqual(81.6, student_published_grades(self.conn, student_id=7)[0]["overall_score"])
        self.assertEqual(0, student_published_grades(self.conn, student_id=9)[0]["overall_score"])
        changed = {**request, "confirmation_inputs": {**request["confirmation_inputs"], "confirmation_note": "Different declaration"}}
        self.assertEqual(409, self.client.post(self.url + "/execute", json=changed).status_code)

    def test_warning_rejection_source_change_and_token_tamper_leave_no_publication(self):
        request = self.request()
        self.assertEqual(400, self.client.post(self.url + "/execute", json={**request, "confirmation_inputs": {"accepted_warning_codes": [], "confirmation_note": ""}}).status_code)
        tampered = {**request, "params": {**request["params"], "material_id": 502}}
        self.assertEqual(409, self.client.post(self.url + "/execute", json=tampered).status_code)
        self.payload["structured"]["students"][0]["final_score"] = 90
        self.conn.execute("UPDATE material_ai_import_records SET export_payload_json=? WHERE id=501", (json.dumps(self.payload),))
        self.conn.commit()
        self.assertEqual(409, self.client.post(self.url + "/execute", json=request).status_code)
        self.assertEqual(0, self.count("grade_publications"))
        self.assertEqual(0, self.count("agent_action_executions"))

    def test_receipt_failure_rolls_back_business_and_revoked_session_cannot_confirm(self):
        request = self.request()
        with patch("classroom_app.services.agent_operation_service.complete_user_agent_operation", side_effect=RuntimeError("Synthetic receipt failure")):
            self.assertEqual(500, self.client.post(self.url + "/execute", json=request).status_code)
        self.assertEqual(0, self.count("grade_publications"))
        self.assertEqual(0, self.count("agent_action_executions"))
        self.conn.execute("DELETE FROM user_sessions WHERE session_user_key='teacher:7'")
        self.conn.commit()
        self.assertEqual(401, self.client.post(self.url + "/execute", json=request).status_code)
        self.assertEqual(0, self.count("grade_publications"))

    def test_roster_change_with_identical_material_hash_requires_a_new_human_review(self):
        old = self.review()
        self.conn.execute("UPDATE students SET name='Corrected student name' WHERE id=9")
        self.conn.commit()
        current = self.review()
        self.assertEqual(old['params']['expected_source_hash'], current['params']['expected_source_hash'])
        self.assertNotEqual(old['params']['expected_review_hash'], current['params']['expected_review_hash'])
        result = self.client.post(self.url + '/execute', json=self.request(old))
        self.assertEqual(409, result.status_code, result.text)
        self.assertEqual(0, self.count('grade_publications'))
        self.assertEqual(0, self.count('agent_action_executions'))
        result = self.client.post(self.url + '/execute', json=self.request(current))
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual(2, result.json()['result']['student_count'])

    def test_authorized_withdrawal_keeps_snapshot_and_receipt_in_the_same_transaction(self):
        published = self.client.post(self.url + '/execute', json=self.request())
        self.assertEqual(200, published.status_code, published.text)
        publication_id = published.json()['result']['publication_id']
        self.conn.execute("UPDATE agent_tasks SET status='running' WHERE id=10")
        self.conn.commit()
        token = self.token()
        params = {'class_offering_id': 40, 'publication_id': publication_id, 'reason': 'Synthetic correction'}
        writes.dispatch_write(self.conn, token, 'withdraw-grade', 'withdraw_grade_publication', params)
        self.assertEqual('withdrawn', self.conn.execute('SELECT status FROM grade_publications').fetchone()[0])
        self.conn.rollback()
        self.assertEqual('active', self.conn.execute('SELECT status FROM grade_publications').fetchone()[0])
        self.assertEqual(1, self.count('agent_action_executions'))
        writes.dispatch_write(self.conn, token, 'withdraw-grade', 'withdraw_grade_publication', params)
        self.conn.commit()
        self.assertTrue(writes.dispatch_write(self.conn, token, 'withdraw-grade', 'withdraw_grade_publication', params)['replayed'])
        self.assertEqual(1, self.count('grade_publications'))
        self.assertEqual(2, self.count('agent_action_executions'))
        self.assertEqual([], student_published_grades(self.conn, student_id=7))

    def test_same_numbered_student_and_other_teacher_cannot_preview_or_confirm(self):
        request = self.request()
        for actor in (self.student, {**self.teacher, "id": 8}):
            self.actor = actor
            self.assertIn(self.client.post(self.url + "/preview", json={}).status_code, (403, 404))
            self.assertIn(self.client.post(self.url + "/execute", json=request).status_code, (403, 404))
        self.assertEqual(0, self.count("grade_publications"))
