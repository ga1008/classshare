"""错误提交 appeal end to end on the real SQLite schema (no network, no AI calls)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.sqlite_database_fixture import isolated_sqlite_database
from classroom_app.database import get_db_connection
from classroom_app.services import approval_request_types  # noqa: F401  registers types
from classroom_app.services import approval_workflow_service as workflow
from classroom_app.services import ai_durable_job_service as jobs
from classroom_app.services import submission_draft_seed_service as seeding
from classroom_app.services.approval_workflow_service import ApprovalWorkflowError
from classroom_app.services.assessment_appeal_service import (
    APPEAL_CORRECTION,
    TASK_KIND_RESUME_GRADING,
    appeal_subject_id,
    resubmission_late_mode,
    submission_grading_hold,
)
from classroom_app.services.late_submission_policy import inherited_late_state
from classroom_app.services.resubmission_expiry_service import (
    TASK_KIND_RESUBMISSION_EXPIRY,
    restore_lapsed_resubmission,
)

TEACHER = {"role": "teacher", "id": 7101, "name": "张老师"}
STUDENT = {"role": "student", "id": 7201, "name": "小明"}
OTHER = {"role": "student", "id": 7202, "name": "小红"}
ASSIGNMENT_ID = "7301"
SUBJECT = appeal_subject_id(ASSIGNMENT_ID, STUDENT["id"])


class CorrectionAppealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        cls.storage = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.enterClassContext(patch.object(seeding, "HOMEWORK_SUBMISSIONS_DIR", cls.storage))
        from classroom_app.services import approval_workflow_schema

        cls.enterClassContext(patch.object(approval_workflow_schema, "_SCHEMA_READY", False))
        with get_db_connection() as conn:
            approval_workflow_schema.ensure_approval_workflow_schema(conn)
            conn.execute("INSERT INTO teachers (id, name, email, hashed_password, is_active) VALUES (?, ?, ?, ?, 1)",
                         (TEACHER["id"], TEACHER["name"], "t7101@example.test", "x"))
            conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (7401, '软件1班', ?)", (TEACHER["id"],))
            conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (7501, '程序设计', ?)", (TEACHER["id"],))
            conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (7601, 7401, 7501, ?)",
                         (TEACHER["id"],))
            for student in (STUDENT, OTHER):
                conn.execute("INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, 7401)",
                             (student["id"], f"S{student['id']}", student["name"]))
            conn.commit()

    def setUp(self):
        with get_db_connection() as conn:
            for sql in ("DELETE FROM approval_request_events", "DELETE FROM approval_request_reviewers",
                        "DELETE FROM approval_requests", "DELETE FROM scheduled_tasks",
                        "DELETE FROM submission_draft_files", "DELETE FROM submission_drafts",
                        "DELETE FROM submission_grade_revisions", "DELETE FROM submission_files",
                        "DELETE FROM submissions", "DELETE FROM ai_jobs", "DELETE FROM assignments"):
                conn.execute(sql)
            due = (datetime.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
            conn.execute(
                """
                INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, due_at,
                                         created_at, grading_mode, assessment_kind)
                VALUES (?, 7501, 7601, '第3次作业', '完成练习', 'published', ?, ?, 'ai', 'homework')
                """,
                (ASSIGNMENT_ID, due, datetime.now().isoformat()),
            )
            conn.commit()
        self.notify = patch.object(workflow, "_notify", return_value=0)
        self.notify.start()
        self.addCleanup(self.notify.stop)

    # ------------------------------------------------------------------ helpers
    def _submission(self, *, status="graded", score=78.0, absence=False, with_file=True):
        answers = {"answers": [{"question": "第1题", "answer": "旧答案", "attachments": [
            {"relative_path": "report.txt", "question_id": "q1", "kind": "file"}]}]}
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, status, score, feedback_md,
                                         answers_json, submitted_at, is_absence_score, submission_channel)
                VALUES (7701, ?, ?, ?, ?, ?, '## 反馈', ?, '2026-10-10T10:00:00', ?, ?)
                """,
                (ASSIGNMENT_ID, STUDENT["id"], STUDENT["name"], "unsubmitted" if absence else status,
                 0 if absence else score, json.dumps(answers, ensure_ascii=False), 1 if absence else 0,
                 "absence_zero" if absence else "online"),
            )
            if with_file:
                folder = self.storage / "7501" / ASSIGNMENT_ID / str(STUDENT["id"])
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "report.txt").write_text("原附件", encoding="utf-8")
                conn.execute(
                    "INSERT INTO submission_files (submission_id, original_filename, relative_path, stored_path, mime_type) "
                    "VALUES (7701, 'report.txt', 'report.txt', ?, 'text/plain')",
                    (str(folder / "report.txt"),),
                )
            if status == "graded" and not absence:
                conn.execute(
                    "INSERT INTO submission_grade_revisions (id, submission_id, revision_hash, status, score, feedback_md) "
                    "VALUES (7801, 7701, 'h1', 'active', ?, '## 反馈')",
                    (score,),
                )
                conn.execute("UPDATE submissions SET active_grade_revision_id = 7801 WHERE id = 7701")
            conn.commit()

    def _start_grading(self) -> int:
        with get_db_connection() as conn:
            row, _ = jobs.create_ai_job(conn, task_type="ai_grading", dedupe_key="ai-grading:7701:t",
                                        payload={"submission_id": 7701}, source_ref="submission:7701")
            conn.execute("UPDATE submissions SET status = 'grading', grading_job_id = ?, "
                         "grading_attempt_fingerprint = 'fp:t' WHERE id = 7701", (row["id"],))
            conn.commit()
            return int(row["id"])

    def _create(self, applicant=STUDENT, reason="附件传错了，想换成正确版本"):
        with get_db_connection() as conn:
            item = workflow.create_request(conn, applicant, request_type=APPEAL_CORRECTION, subject_id=SUBJECT,
                                           reason=reason, payload={"problem_kind": "attachment"})
            conn.commit()
            return item

    def _decide(self, request_id, decision, note="", payload=None):
        with get_db_connection() as conn:
            item = workflow.decide_request(conn, TEACHER, request_id, decision=decision, note=note,
                                           decision_payload=payload or {})
            conn.commit()
            return item

    def _row(self, sql, params=()):
        with get_db_connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _rows(self, sql, params=()):
        with get_db_connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ tests
    def test_appeal_on_graded_answer_keeps_score_and_notifies_reviewer(self):
        self._submission()
        item = self._create()
        self.assertEqual("pending", item["status"])
        self.assertEqual(1, item["payload"]["attempt_no"])
        self.assertEqual("graded", self._row("SELECT status FROM submissions WHERE id = 7701")["status"])
        self.assertEqual([TEACHER["id"]], [r["id"] for r in item["reviewers"]])

    def test_appeal_interrupts_grading_and_holds_it(self):
        self._submission(status="submitted", score=None)
        job_id = self._start_grading()
        item = self._create()
        self.assertTrue(item["payload"]["grading_interrupted"])
        row = self._row("SELECT status, grading_job_id, grading_attempt_fingerprint FROM submissions WHERE id = 7701")
        self.assertEqual("submitted", row["status"])
        self.assertIsNone(row["grading_job_id"])
        self.assertEqual("cancelled", self._row("SELECT status FROM ai_jobs WHERE id = ?", (job_id,))["status"])
        with get_db_connection() as conn:
            self.assertIsNotNone(submission_grading_hold(conn, 7701))
        events = [r["event_type"] for r in self._rows("SELECT event_type FROM approval_request_events ORDER BY id")]
        self.assertIn("grading_interrupted", events)

        from classroom_app.services import ai_grading_service as grading

        with self.assertRaises(grading.AIGradingQueueError) as ctx:
            asyncio.run(grading.submit_submission_for_ai_grading(7701, allow_graded=False))
        self.assertEqual(409, ctx.exception.status_code)

    def test_reject_releases_hold_and_resumes_grading(self):
        self._submission(status="submitted", score=None)
        item = self._create()
        with self.assertRaises(ApprovalWorkflowError):
            self._decide(item["id"], "reject")  # a rejection needs a reason
        self._decide(item["id"], "reject", note="附件没有问题，按原提交批改")
        with get_db_connection() as conn:
            self.assertIsNone(submission_grading_hold(conn, 7701))
        task = self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind = ?", (TASK_KIND_RESUME_GRADING,))
        self.assertEqual(7701, json.loads(task["payload_json"])["submission_id"])

    def test_one_pending_appeal_per_answer_and_three_attempt_limit(self):
        self._submission()
        first = self._create()
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._create(reason="再申请一次")
        self.assertEqual(409, ctx.exception.status_code)
        self._decide(first["id"], "reject", note="不同意")
        self._decide(self._create(reason="第二次")["id"], "reject", note="不同意")
        self._decide(self._create(reason="第三次")["id"], "reject", note="不同意")
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._create(reason="第四次")
        self.assertIn("最多 3 次", ctx.exception.message)

    def test_only_owner_and_only_real_submissions(self):
        self._submission(absence=True, with_file=False)
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._create()
        self.assertIn("超时补交", ctx.exception.message)
        with get_db_connection() as conn:
            with self.assertRaises(ApprovalWorkflowError) as ctx:
                workflow.create_request(conn, OTHER, request_type=APPEAL_CORRECTION, subject_id=SUBJECT, reason="冒名")
        self.assertEqual(403, ctx.exception.status_code)

    def test_approve_returns_answer_seeds_draft_and_schedules_lapse(self):
        self._submission()
        item = self._create()
        decided = self._decide(item["id"], "approve", note="同意，请改正附件")
        self.assertEqual("approved", decided["status"])
        row = self._row("SELECT * FROM submissions WHERE id = 7701")
        self.assertEqual(1, row["resubmission_allowed"])
        self.assertIsNone(row["score"])
        draft = self._row("SELECT * FROM submission_drafts WHERE assignment_id = ? AND student_pk_id = ?",
                          (ASSIGNMENT_ID, STUDENT["id"]))
        self.assertIn("旧答案", draft["answers_json"])
        self.assertGreater(draft["server_updated_at"], row["returned_at"])
        draft_file = self._row("SELECT * FROM submission_draft_files WHERE draft_id = ?", (draft["id"],))
        self.assertEqual("q1", draft_file["question_id"])
        self.assertEqual("原附件", Path(draft_file["stored_path"]).read_text(encoding="utf-8"))
        lapse = self._row("SELECT run_at, payload_json FROM scheduled_tasks WHERE task_kind = ?",
                          (TASK_KIND_RESUBMISSION_EXPIRY,))
        self.assertEqual(row["resubmission_due_at"][:16], str(lapse["run_at"])[:16])
        self.assertEqual(78.0, json.loads(lapse["payload_json"])["previous"]["score"])

    def test_resubmission_inherits_original_late_state(self):
        self._submission()
        item = self._create()
        self._decide(item["id"], "approve", note="同意")
        returned = self._row("SELECT * FROM submissions WHERE id = 7701")
        with get_db_connection() as conn:
            self.assertEqual("inherit", resubmission_late_mode(conn, returned))
        self.assertEqual((0, 0, None), inherited_late_state(returned))

    def test_teacher_can_choose_to_recompute_lateness(self):
        self._submission()
        item = self._create()
        self._decide(item["id"], "approve", note="同意", payload={"late_mode": "recompute"})
        returned = self._row("SELECT * FROM submissions WHERE id = 7701")
        with get_db_connection() as conn:
            self.assertEqual("recompute", resubmission_late_mode(conn, returned))

    def test_lapsed_window_restores_original_grade(self):
        self._submission()
        item = self._create()
        self._decide(item["id"], "approve", note="同意")
        payload = json.loads(self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind = ?",
                                       (TASK_KIND_RESUBMISSION_EXPIRY,))["payload_json"])
        with get_db_connection() as conn:
            outcome = restore_lapsed_resubmission(conn, payload, now=datetime.now() + timedelta(days=30))
            conn.commit()
        self.assertEqual("restored_grade", outcome)
        row = self._row("SELECT status, score, resubmission_allowed, active_grade_revision_id FROM submissions WHERE id = 7701")
        self.assertEqual(("graded", 78.0, 0, 7801), (row["status"], row["score"], row["resubmission_allowed"],
                                                     row["active_grade_revision_id"]))
        self.assertEqual("active", self._row("SELECT status FROM submission_grade_revisions WHERE id = 7801")["status"])
        self.assertIsNone(self._row("SELECT id FROM submission_drafts WHERE assignment_id = ?", (ASSIGNMENT_ID,)))

    def test_lapse_is_skipped_after_resubmission_or_extension(self):
        self._submission()
        item = self._create()
        self._decide(item["id"], "approve", note="同意")
        payload = json.loads(self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind = ?",
                                       (TASK_KIND_RESUBMISSION_EXPIRY,))["payload_json"])
        with get_db_connection() as conn:
            self.assertTrue(restore_lapsed_resubmission(conn, payload).startswith("rescheduled"))
            conn.execute("UPDATE submissions SET resubmission_allowed = 0 WHERE id = 7701")  # student resubmitted
            self.assertTrue(restore_lapsed_resubmission(conn, payload, now=datetime.now() + timedelta(days=30))
                            .startswith("skipped"))

    def test_lapse_restores_a_legacy_grade_that_has_no_revision_row(self):
        self._submission()
        with get_db_connection() as conn:
            conn.execute("UPDATE submissions SET active_grade_revision_id = NULL WHERE id = 7701")
            conn.execute("DELETE FROM submission_grade_revisions")
            conn.commit()
        self._decide(self._create()["id"], "approve", note="同意")
        payload = json.loads(self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind = ?",
                                       (TASK_KIND_RESUBMISSION_EXPIRY,))["payload_json"])
        with get_db_connection() as conn:
            self.assertEqual("restored_grade", restore_lapsed_resubmission(conn, payload, now=datetime.now() + timedelta(days=30)))
            conn.commit()
        row = self._row("SELECT status, score FROM submissions WHERE id = 7701")
        self.assertEqual(("graded", 78.0), (row["status"], row["score"]))

    def test_resume_grading_retries_transient_failures_and_reports_terminal_ones(self):
        from classroom_app.services import ai_grading_service as grading
        from classroom_app.services.assessment_appeal_service import handle_resume_grading_task

        self._submission(status="submitted", score=None)
        task = {"payload": {"submission_id": 7701}}

        async def unavailable(*_args, **_kwargs):
            raise grading.AIGradingQueueError(503, "AI 服务暂不可用")

        async def unsupported(*_args, **_kwargs):
            raise grading.AIGradingQueueError(400, "附件类型不支持自动批改")

        with patch.object(grading, "submit_submission_for_ai_grading", unavailable):
            with self.assertRaises(grading.AIGradingQueueError):
                asyncio.run(handle_resume_grading_task(task))
        with patch.object(grading, "submit_submission_for_ai_grading", unsupported):
            self.assertTrue(asyncio.run(handle_resume_grading_task(task)).startswith("failed"))
        notice = self._row("SELECT recipient_user_pk, link_url FROM message_center_notifications "
                           "WHERE ref_type = 'appeal_resume_grading'")
        self.assertEqual((TEACHER["id"], "/submission/7701"), (notice["recipient_user_pk"], notice["link_url"]))

    def test_teacher_return_voids_pending_appeal_without_counting_it(self):
        from classroom_app.services import submission_return_service as returns
        from classroom_app.services.assessment_appeal_service import correction_attempts

        self._submission()
        item = self._create()
        with get_db_connection() as conn, patch.object(returns, "invalidate_member_work_score", return_value=[]):
            assignment = dict(conn.execute("SELECT * FROM assignments WHERE id = ?", (ASSIGNMENT_ID,)).fetchone())
            submission = dict(conn.execute("SELECT * FROM submissions WHERE id = 7701").fetchone())
            returns.return_submissions_for_resubmission(conn, assignment=assignment, targets=[submission],
                                                        teacher_id=TEACHER["id"],
                                                        resubmission_due_at=returns.resolve_resubmission_due_at(assignment),
                                                        reason="教师撤回")
            conn.commit()
        self.assertEqual("cancelled", self._row("SELECT status FROM approval_requests WHERE id = ?", (item["id"],))["status"])
        with get_db_connection() as conn:
            self.assertEqual(0, correction_attempts(conn, ASSIGNMENT_ID, STUDENT["id"], 7701))


if __name__ == "__main__":
    unittest.main()
