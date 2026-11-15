"""错误提交 appeal end to end on the real SQLite schema (no network, no AI calls)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

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
    def _teacher_return(self):
        from classroom_app.routers.homework_parts import submissions as route
        from tests.test_mp_grade_safety import JsonRequest
        with patch.object(route, "record_behavior_event", return_value=None):
            return asyncio.run(route.teacher_withdraw_submissions(
                ASSIGNMENT_ID, JsonRequest({"submission_ids": [7701]}), user=TEACHER))

    def _legacy_return_without_draft(self, *, second_precision=False):
        self._submission()
        returned = datetime.now() - timedelta(days=1)
        if second_precision:
            returned = returned.replace(microsecond=0)
        with get_db_connection() as conn:
            conn.execute("UPDATE submissions SET resubmission_allowed=1,returned_at=?,resubmission_due_at=?,status='submitted' WHERE id=7701",
                         (returned.isoformat(), (datetime.now() + timedelta(days=1)).isoformat()))
            conn.commit()
        return returned

    def test_get_draft_recovers_existing_return_once_without_overriding_later_local_answers(self):
        from classroom_app.routers.homework_parts import drafts as route
        returned = self._legacy_return_without_draft()
        original = self._row("SELECT * FROM submissions WHERE id=7701")
        payload = route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT)
        self.assertTrue(payload["exists"])
        self.assertEqual(original["answers_json"], payload["answers_json"])
        self.assertEqual(1, len(payload["files"]))
        stamp = datetime.fromisoformat(payload["server_updated_at"])
        self.assertEqual(returned + timedelta(microseconds=1), stamp)
        self.assertLess(stamp, returned + timedelta(minutes=1), "recovery must not outrank a later local draft")
        from classroom_app.services.submission_write_guard import draft_matches_submission_round
        self.assertTrue(draft_matches_submission_round(payload, original))
        self.assertEqual(payload, route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT))
        self.assertEqual(original, self._row("SELECT * FROM submissions WHERE id=7701"))

    def test_get_draft_keeps_empty_current_draft_even_if_created_while_waiting_for_lock(self):
        from classroom_app.routers.homework_parts import drafts as route
        returned = self._legacy_return_without_draft(second_precision=True)
        actual_lock = route.lock_submission_writer
        def create_blank_draft(conn, assignment_id, student_id):
            actual_lock(conn, assignment_id, student_id)
            stamp = (returned + timedelta(seconds=1)).isoformat()
            conn.execute("INSERT INTO submission_drafts(assignment_id,student_pk_id,answers_json,current_page,server_updated_at,server_version,status) "
                         "VALUES(?,?,'',2,?,3,'active')", (assignment_id, student_id, stamp))
        with patch.object(route, "lock_submission_writer", create_blank_draft):
            payload = route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT)
        self.assertTrue(payload["exists"])
        self.assertEqual("", payload["answers_json"])
        self.assertEqual(2, payload["current_page"])
        self.assertEqual(3, payload["server_version"])
        self.assertEqual([], payload["files"])
        with patch.object(seeding, "seed_resubmission_draft", side_effect=AssertionError("must preserve intentional blank draft")):
            self.assertEqual(payload, route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT))

    def test_get_draft_old_second_precision_return_recovers_at_next_second(self):
        from classroom_app.routers.homework_parts import drafts as route
        returned = self._legacy_return_without_draft(second_precision=True)
        payload = route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT)
        self.assertTrue(payload["exists"])
        self.assertEqual(returned + timedelta(seconds=1), datetime.fromisoformat(payload["server_updated_at"]))

    def test_get_draft_rechecks_return_after_lock_and_does_not_seed_expired_or_submitted_work(self):
        from classroom_app.routers.homework_parts import drafts as route
        self._legacy_return_without_draft()
        actual_lock = route.lock_submission_writer
        transitions = (
            "resubmission_allowed=0",
            "resubmission_due_at='2000-01-01T00:00:00'",
            "returned_at='2026-01-01T01:00:00'",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                def changed_while_waiting(conn, assignment_id, student_id):
                    actual_lock(conn, assignment_id, student_id)
                    conn.execute(f"UPDATE submissions SET {transition} WHERE id=7701")
                with patch.object(route, "lock_submission_writer", changed_while_waiting):
                    with self.assertRaises(HTTPException) as failed:
                        route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT)
                self.assertEqual(409, failed.exception.status_code)
                self.assertIsNone(self._row("SELECT * FROM submission_drafts"))
        with get_db_connection() as conn:
            conn.execute("UPDATE submissions SET resubmission_due_at='2000-01-01T00:00:00'")
            conn.commit()
        with patch.object(route, "lock_submission_writer", side_effect=AssertionError("expired access must fail first")):
            with self.assertRaises(HTTPException) as failed:
                route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT)
        self.assertEqual(409, failed.exception.status_code)
        self.assertIsNone(self._row("SELECT * FROM submission_drafts"))

    def test_get_draft_recovery_commit_failure_restores_database_and_cleans_its_copies(self):
        from classroom_app.routers.homework_parts import drafts as route
        self._legacy_return_without_draft()
        original = self._row("SELECT * FROM submissions WHERE id=7701")
        before = {str(path) for path in self.storage.rglob("*") if path.is_file()}
        class FailingSecondCommit:
            def __init__(self, conn):
                self.conn, self.commits = conn, 0
            def __getattr__(self, name):
                return getattr(self.conn, name)
            def commit(self):
                self.commits += 1
                if self.commits == 2:
                    raise RuntimeError("synthetic lazy-seed commit failure")
                self.conn.commit()
        @contextmanager
        def failing_connection():
            with get_db_connection() as conn:
                yield FailingSecondCommit(conn)
        with patch.object(route, "get_db_connection", failing_connection):
            with self.assertRaisesRegex(RuntimeError, "synthetic lazy-seed commit failure"):
                route.get_assignment_draft(ASSIGNMENT_ID, user=STUDENT)
        self.assertIsNone(self._row("SELECT * FROM submission_drafts"))
        self.assertEqual(original, self._row("SELECT * FROM submissions WHERE id=7701"))
        self.assertEqual(before, {str(path) for path in self.storage.rglob("*") if path.is_file()})

    def test_teacher_return_seeds_and_repeat_preserves_edits_and_original_lapse_grade(self):
        self._submission()
        self.assertEqual(1, self._teacher_return()["updated_count"])
        draft = self._row("SELECT * FROM submission_drafts")
        original = self._row("SELECT * FROM submission_files")
        copied = self._row("SELECT * FROM submission_draft_files")
        self.assertIn("旧答案", draft["answers_json"])
        self.assertEqual("q1", copied["question_id"])
        self.assertEqual("原附件", Path(copied["stored_path"]).read_text(encoding="utf-8"))
        Path(copied["stored_path"]).write_text("学生修改后的附件", encoding="utf-8")
        self.assertEqual("原附件", Path(original["stored_path"]).read_text(encoding="utf-8"))
        with get_db_connection() as conn:
            conn.execute("UPDATE submission_drafts SET answers_json='student edited',current_page=2")
            conn.commit()
        self._teacher_return()
        revised = self._row("SELECT * FROM submission_drafts")
        self.assertEqual("student edited", revised["answers_json"])
        self.assertEqual(2, revised["current_page"])
        self.assertEqual(copied["stored_path"], self._row("SELECT * FROM submission_draft_files")["stored_path"])
        returned = self._row("SELECT * FROM submissions WHERE id=7701")
        from classroom_app.services.submission_write_guard import draft_matches_submission_round
        self.assertTrue(draft_matches_submission_round(revised, returned))
        with get_db_connection() as conn:
            assignment = dict(conn.execute("SELECT * FROM assignments WHERE id=?", (ASSIGNMENT_ID,)).fetchone())
            seeded = seeding.seed_resubmission_draft(conn, assignment=assignment, submission=returned)
            self.assertTrue(seeded["preserved"])
            conn.commit()
        self.assertEqual(revised, self._row("SELECT * FROM submission_drafts"))
        payload = json.loads(self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind=?",
                                      (TASK_KIND_RESUBMISSION_EXPIRY,))["payload_json"])
        self.assertEqual(78, payload["previous"]["score"])
        with get_db_connection() as conn, patch("classroom_app.services.resubmission_expiry_service._notify"):
            outcome = restore_lapsed_resubmission(conn, payload, now=datetime.now() + timedelta(days=4))
            conn.commit()
        self.assertEqual("restored_grade", outcome)
        self.assertEqual(78, self._row("SELECT score FROM submissions WHERE id=7701")["score"])
        self.assertEqual("原附件", Path(original["stored_path"]).read_text(encoding="utf-8"))

    def test_teacher_return_rollback_preserves_previous_draft_and_removes_only_new_copies(self):
        self._submission()
        self._teacher_return()
        old_draft = self._row("SELECT * FROM submission_drafts")
        old_file = self._row("SELECT * FROM submission_draft_files")
        Path(old_file["stored_path"]).write_text("previous draft contents", encoding="utf-8")
        with get_db_connection() as conn:
            conn.execute("UPDATE submissions SET resubmission_allowed=0,status='graded',score=78,returned_at=NULL")
            conn.commit()
        folder = seeding.draft_storage_dir(7501, ASSIGNMENT_ID, STUDENT["id"])
        before_paths = {str(path) for path in folder.rglob("*")}
        with patch("classroom_app.services.resubmission_expiry_service.schedule_resubmission_expiry",
                   side_effect=RuntimeError("synthetic scheduling failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic scheduling failure"):
                self._teacher_return()
        self.assertEqual(old_draft, self._row("SELECT * FROM submission_drafts"))
        self.assertEqual(old_file, self._row("SELECT * FROM submission_draft_files"))
        self.assertEqual("previous draft contents", Path(old_file["stored_path"]).read_text(encoding="utf-8"))
        self.assertEqual(before_paths, {str(path) for path in folder.rglob("*")})
        self.assertEqual(0, self._row("SELECT resubmission_allowed FROM submissions WHERE id=7701")["resubmission_allowed"])

    def test_seed_skips_unsafe_paths_and_keeps_legacy_dictionary_attachment_metadata(self):
        self._submission()
        original = self._row("SELECT * FROM submission_files")
        outside = Path(self.enterContext(tempfile.TemporaryDirectory())) / "private.txt"
        outside.write_text("must not be copied", encoding="utf-8")
        with get_db_connection() as conn:
            conn.execute("UPDATE submissions SET answers_json=?", (json.dumps({"answers": {"legacy-q": {
                "question_id": "stale-inline-id", "answer": "text", "attachments": [{"relative_path": "report.txt"}]}}}),))
            for relative in ("../escape.txt", "/absolute.txt", "C:/drive.txt", "folder/name:stream"):
                conn.execute("INSERT INTO submission_files(submission_id,original_filename,relative_path,stored_path) VALUES(7701,'unsafe.txt',?,?)",
                             (relative, original["stored_path"]))
            conn.execute("INSERT INTO submission_files(submission_id,original_filename,relative_path,stored_path) VALUES(7701,'private.txt','private.txt',?)",
                         (str(outside),))
            conn.commit()
        self._teacher_return()
        copied = self._rows("SELECT * FROM submission_draft_files")
        self.assertEqual(1, len(copied))
        self.assertEqual("legacy-q", copied[0]["question_id"])
        self.assertEqual("must not be copied", outside.read_text(encoding="utf-8"))
        self.assertTrue(Path(copied[0]["stored_path"]).resolve().is_relative_to(self.storage.resolve()))

    def test_correction_approval_commit_failure_removes_new_seed_files(self):
        from classroom_app.routers import approval_workflow as route
        from tests.test_mp_grade_safety import JsonRequest
        self._submission()
        item = self._create()
        before = {str(path) for path in self.storage.rglob("*") if path.is_file()}

        class FailingCommit:
            def __init__(self, conn):
                self.conn = conn
            def __getattr__(self, name):
                return getattr(self.conn, name)
            def commit(self):
                raise RuntimeError("synthetic approval commit failure")

        @contextmanager
        def failing_connection():
            with get_db_connection() as conn:
                yield FailingCommit(conn)

        request = JsonRequest({"note": "同意修改"})
        request.headers = {"content-length": "1"}
        with patch.object(route, "get_db_connection", failing_connection):
            with self.assertRaisesRegex(RuntimeError, "synthetic approval commit failure"):
                asyncio.run(route.approve_request(item["id"], request, user=TEACHER))
        self.assertIsNone(self._row("SELECT * FROM submission_drafts"))
        self.assertEqual("pending", self._row("SELECT status FROM approval_requests WHERE id=?", (item["id"],))["status"])
        self.assertEqual(before, {str(path) for path in self.storage.rglob("*") if path.is_file()})

    def test_lapse_rechecks_after_writer_lock_and_preserves_new_round(self):
        self._submission()
        self._teacher_return()
        payload = json.loads(self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind=?",
                                      (TASK_KIND_RESUBMISSION_EXPIRY,))["payload_json"])
        original_draft = self._row("SELECT * FROM submission_drafts")
        from classroom_app.services import submission_write_guard as guard
        actual_lock = guard.lock_submission_writer
        for replaced_with_new_return in (False, True):
            with self.subTest(new_return=replaced_with_new_return):
                def write_while_waiting(conn, assignment_id, student_id):
                    actual_lock(conn, assignment_id, student_id)
                    conn.execute("UPDATE submissions SET answers_json='new round answer',resubmission_allowed=?,returned_at='new-token'",
                                 (1 if replaced_with_new_return else 0,))
                with get_db_connection() as conn, patch.object(guard, "lock_submission_writer", write_while_waiting):
                    result = restore_lapsed_resubmission(conn, payload, now=datetime.now() + timedelta(days=4))
                    conn.commit()
                self.assertEqual("skipped: returned again in a later round" if replaced_with_new_return
                                 else "skipped: already resubmitted or closed", result)
                self.assertEqual(original_draft, self._row("SELECT * FROM submission_drafts"))
                self.assertEqual("new round answer", self._row("SELECT answers_json FROM submissions WHERE id=7701")["answers_json"])

    def test_lapse_failure_rolls_back_draft_without_losing_attachment(self):
        self._submission()
        self._teacher_return()
        draft = self._row("SELECT * FROM submission_drafts")
        attachment = self._row("SELECT * FROM submission_draft_files")
        payload = json.loads(self._row("SELECT payload_json FROM scheduled_tasks WHERE task_kind=?",
                                      (TASK_KIND_RESUBMISSION_EXPIRY,))["payload_json"])
        with self.assertRaisesRegex(RuntimeError, "synthetic notify failure"):
            with get_db_connection() as conn, patch("classroom_app.services.resubmission_expiry_service._notify",
                                                  side_effect=RuntimeError("synthetic notify failure")):
                restore_lapsed_resubmission(conn, payload, now=datetime.now() + timedelta(days=4))
        self.assertEqual(draft, self._row("SELECT * FROM submission_drafts"))
        self.assertEqual(attachment, self._row("SELECT * FROM submission_draft_files"))
        self.assertEqual("原附件", Path(attachment["stored_path"]).read_text(encoding="utf-8"))

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
