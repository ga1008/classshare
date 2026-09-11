"""Generic approval workflow + submission withdraw request type (sqlite, no network)."""
import os
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest import mock

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.services import approval_workflow_schema as schema_approval_workflow  # noqa: E402
from classroom_app.services import approval_workflow_service as workflow  # noqa: E402
from classroom_app.services import approval_request_types  # noqa: E402,F401
from classroom_app.services import submission_return_service as returns  # noqa: E402
from classroom_app.services.approval_workflow_service import ApprovalWorkflowError  # noqa: E402

TEACHER = {"role": "teacher", "id": 7, "name": "张老师"}
OTHER_TEACHER = {"role": "teacher", "id": 8, "name": "李老师"}
ADMIN = {"role": "teacher", "id": 9, "name": "管理员"}
STUDENT = {"role": "student", "id": 20, "name": "小明"}
OTHER_STUDENT = {"role": "student", "id": 21, "name": "小红"}


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


class ApprovalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = mock.patch.dict(os.environ, {"DB_ENGINE": "sqlite"})
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER DEFAULT 1, is_super_admin INTEGER DEFAULT 0);
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE courses (id INTEGER PRIMARY KEY, created_by_teacher_id INTEGER);
            CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, course_id INTEGER, teacher_id INTEGER);
            CREATE TABLE assignments (id TEXT PRIMARY KEY, course_id INTEGER, title TEXT, class_offering_id INTEGER,
                assessment_kind TEXT, due_at TEXT, late_submission_enabled INTEGER DEFAULT 0, late_submission_until TEXT,
                status TEXT DEFAULT 'published', auto_close INTEGER DEFAULT 1, availability_mode TEXT DEFAULT 'deadline');
            CREATE TABLE submissions (id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER, student_name TEXT,
                status TEXT, score INTEGER, feedback_md TEXT, submitted_at TEXT, is_absence_score INTEGER DEFAULT 0,
                resubmission_allowed INTEGER DEFAULT 0, resubmission_due_at TEXT, returned_at TEXT, returned_by_teacher_id INTEGER,
                returned_reason TEXT, grading_started_at TEXT, grading_attempt_fingerprint TEXT, grading_revision_hash TEXT,
                grading_job_id INTEGER, active_grade_revision_id INTEGER, score_before_late_penalty REAL,
                late_penalty_points REAL DEFAULT 0, late_score_cap_applied INTEGER DEFAULT 0);
            CREATE TABLE submission_grade_revisions (id INTEGER PRIMARY KEY, submission_id INTEGER, status TEXT, superseded_at TEXT);
            INSERT INTO teachers VALUES (7, '张老师', 1, 0), (8, '李老师', 1, 0), (9, '管理员', 1, 1);
            INSERT INTO students VALUES (20, '小明'), (21, '小红');
            INSERT INTO courses VALUES (1, 8);
            INSERT INTO class_offerings VALUES (4, 1, 7);
        """)
        future = _iso(datetime.now() + timedelta(days=3))
        self.conn.execute("INSERT INTO assignments (id, course_id, title, class_offering_id, assessment_kind, due_at) VALUES (?, ?, ?, ?, ?, ?)",
                          ("hw1", 1, "第2课课堂练习", 4, "homework", future))
        self.conn.execute("INSERT INTO assignments (id, course_id, title, class_offering_id, assessment_kind, due_at) VALUES (?, ?, ?, ?, ?, ?)",
                          ("final1", 1, "期末考试", 4, "final", future))
        self.conn.execute("INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, status, score, feedback_md, submitted_at) "
                          "VALUES (1, 'hw1', 20, '小明', 'graded', 76, '## 反馈', '2026-09-10T11:01:21')")
        self.conn.execute("INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, status, score, submitted_at) "
                          "VALUES (2, 'final1', 20, '小明', 'graded', 88, '2026-09-10T11:01:21')")
        self.conn.execute("INSERT INTO submission_grade_revisions VALUES (1, 1, 'active', NULL)")
        self.conn.commit()
        schema_approval_workflow._SCHEMA_READY = False
        schema_approval_workflow.ensure_approval_workflow_schema(self.conn)
        self.conn.commit()
        self.notifications = []
        self.notify_patch = mock.patch.object(workflow, "_notify", side_effect=self._capture_notify)
        self.notify_patch.start()
        self.addCleanup(self.notify_patch.stop)
        self.group_patch = mock.patch.object(returns, "invalidate_member_work_score", return_value=[])
        self.group_patch.start()
        self.addCleanup(self.group_patch.stop)

    def _capture_notify(self, conn, **kwargs):
        self.notifications.append(kwargs)
        return len(kwargs.get("recipients") or [])

    def _create(self, applicant=STUDENT, subject_id=1, reason="截图上传不完整，想重新提交"):
        return workflow.create_request(self.conn, applicant, request_type="submission_withdraw", subject_id=subject_id, reason=reason)

    # ------------------------------------------------------------------ creation
    def test_student_creates_request_and_teachers_are_notified(self):
        item = self._create()
        self.conn.commit()
        self.assertEqual(("pending", "submission_withdraw", "1", "hw1", 4), (item["status"], item["request_type"], item["subject_id"], item["assignment_id"], item["class_offering_id"]))
        self.assertEqual([{"role": "teacher", "id": 7, "name": "张老师"}, {"role": "teacher", "id": 8, "name": "李老师"}], item["reviewers"])
        self.assertTrue(item["can_cancel"])
        self.assertFalse(item["can_decide"])
        self.assertEqual(1, len(self.notifications))
        self.assertEqual({7, 8}, {r["id"] for r in self.notifications[0]["recipients"]})
        self.assertIn("待审批", self.notifications[0]["title"])
        events = [row["event_type"] for row in self.conn.execute("SELECT event_type FROM approval_request_events")]
        self.assertEqual(["created"], events)

    def test_duplicate_pending_request_is_rejected(self):
        self._create()
        self.conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as error:
            self._create(reason="再申请一次")
        self.assertEqual(409, error.exception.status_code)

    def test_business_guards(self):
        with self.assertRaises(ApprovalWorkflowError) as error:
            self._create(applicant=OTHER_STUDENT)
        self.assertEqual(403, error.exception.status_code)
        with self.assertRaises(ApprovalWorkflowError) as error:
            self._create(subject_id=2)
        self.assertEqual(400, error.exception.status_code)
        self.assertIn("期中测验和期末考试", error.exception.message)
        with self.assertRaises(ApprovalWorkflowError):
            self._create(reason="   ")
        with self.assertRaises(ApprovalWorkflowError) as error:
            workflow.create_request(self.conn, TEACHER, request_type="submission_withdraw", subject_id=1, reason="x")
        self.assertEqual(403, error.exception.status_code)
        self.conn.execute("UPDATE submissions SET status = 'submitted', score = NULL WHERE id = 1")
        with self.assertRaises(ApprovalWorkflowError) as error:
            self._create()
        self.assertEqual(400, error.exception.status_code)

    # ------------------------------------------------------------------ listing / access
    def test_incoming_and_mine_scopes_and_detail_permissions(self):
        item = self._create()
        self.conn.commit()
        incoming = workflow.list_requests(self.conn, TEACHER, scope="incoming", assignment_id="hw1")
        self.assertEqual([item["id"]], [row["id"] for row in incoming])
        self.assertTrue(incoming[0]["can_decide"])
        self.assertEqual(1, workflow.count_pending_requests(self.conn, TEACHER, assignment_id="hw1"))
        self.assertEqual([], workflow.list_requests(self.conn, OTHER_STUDENT, scope="mine"))
        self.assertEqual(1, len(workflow.list_requests(self.conn, STUDENT, scope="mine")))
        detail = workflow.get_request(self.conn, TEACHER, item["id"])
        self.assertEqual("/submission/1", detail["detail"]["review_url"])
        self.assertEqual(76, detail["detail"]["current_score"])
        self.assertTrue(detail["detail"]["recommended_resubmission_due_at"])
        with self.assertRaises(ApprovalWorkflowError) as error:
            workflow.get_request(self.conn, OTHER_STUDENT, item["id"])
        self.assertEqual(403, error.exception.status_code)
        # A super admin can read and decide anything.
        self.assertTrue(workflow.get_request(self.conn, ADMIN, item["id"])["can_decide"])
        latest = workflow.latest_request_for_subject(self.conn, request_type="submission_withdraw", subject_id=1, applicant=STUDENT)
        self.assertEqual(item["id"], latest["id"])

    # ------------------------------------------------------------------ decisions
    def test_approve_returns_submission_with_assignment_deadline(self):
        item = self._create()
        self.conn.commit()
        self.notifications.clear()
        decided = workflow.decide_request(self.conn, TEACHER, item["id"], decision="approve", note="同意，注意截图完整")
        self.conn.commit()
        self.assertEqual(("approved", "张老师"), (decided["status"], decided["decided_by_name"]))
        due = self.conn.execute("SELECT due_at FROM assignments WHERE id = 'hw1'").fetchone()["due_at"]
        self.assertEqual(due, decided["decision_payload"]["resubmission_due_at"])
        row = self.conn.execute("SELECT * FROM submissions WHERE id = 1").fetchone()
        self.assertEqual(("submitted", None, None, 1, due, 7), (row["status"], row["score"], row["feedback_md"],
            row["resubmission_allowed"], row["resubmission_due_at"], row["returned_by_teacher_id"]))
        self.assertIn("教师已批准", row["returned_reason"])
        self.assertEqual("superseded", self.conn.execute("SELECT status FROM submission_grade_revisions WHERE id = 1").fetchone()["status"])
        self.assertEqual({20}, {r["id"] for r in self.notifications[0]["recipients"]})
        self.assertIn("已通过", self.notifications[0]["title"])
        self.assertIn("前重新提交", self.notifications[0]["body"])
        # The student can apply again after a new grade (pending dedupe released).
        self.conn.execute("UPDATE submissions SET status = 'graded', score = 90, resubmission_allowed = 0 WHERE id = 1")
        self.conn.commit()
        again = self._create(reason="第二次")
        self.assertNotEqual(item["id"], again["id"])

    def test_approve_with_explicit_later_deadline_and_fallback_when_assignment_closed(self):
        item = self._create()
        self.conn.commit()
        explicit = _iso(datetime.now() + timedelta(days=10))
        decided = workflow.decide_request(self.conn, OTHER_TEACHER, item["id"], decision="approve",
                                          decision_payload={"resubmission_due_at": explicit})
        self.assertEqual(explicit, decided["decision_payload"]["resubmission_due_at"])
        self.conn.commit()
        # Another assignment whose deadline already passed -> now + 24h (extension of 60 min is shorter, so ignored).
        past = _iso(datetime.now() - timedelta(days=1))
        self.conn.execute("INSERT INTO assignments (id, course_id, title, class_offering_id, assessment_kind, due_at, status) VALUES ('hw2', 1, '旧作业', 4, 'homework', ?, 'closed')", (past,))
        self.conn.execute("INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, status, score, submitted_at) VALUES (3, 'hw2', 20, '小明', 'graded', 50, '2026-09-01T10:00:00')")
        self.conn.commit()
        old = self._create(subject_id=3)
        self.conn.commit()
        before = datetime.now()
        decided = workflow.decide_request(self.conn, TEACHER, old["id"], decision="approve",
                                          decision_payload={"extension_minutes": 60})
        due = datetime.fromisoformat(decided["decision_payload"]["resubmission_due_at"])
        self.assertGreaterEqual(due, before.replace(microsecond=0) + timedelta(hours=24))
        self.assertLess(due, before + timedelta(hours=25))

    def test_reject_requires_note_and_notifies_student(self):
        item = self._create()
        self.conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as error:
            workflow.decide_request(self.conn, TEACHER, item["id"], decision="reject", note="")
        self.assertEqual(400, error.exception.status_code)
        self.notifications.clear()
        decided = workflow.decide_request(self.conn, TEACHER, item["id"], decision="reject", note="分数合理，不予撤回")
        self.conn.commit()
        self.assertEqual("rejected", decided["status"])
        row = self.conn.execute("SELECT status, score, resubmission_allowed FROM submissions WHERE id = 1").fetchone()
        self.assertEqual(("graded", 76, 0), tuple(row))
        self.assertIn("已拒绝", self.notifications[0]["title"])
        with self.assertRaises(ApprovalWorkflowError) as error:
            workflow.decide_request(self.conn, TEACHER, item["id"], decision="approve")
        self.assertEqual(409, error.exception.status_code)

    def test_only_reviewers_or_admin_can_decide(self):
        item = self._create()
        self.conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as error:
            workflow.decide_request(self.conn, STUDENT, item["id"], decision="approve")
        self.assertEqual(403, error.exception.status_code)
        with self.assertRaises(ApprovalWorkflowError):
            workflow.decide_request(self.conn, {"role": "teacher", "id": 99, "name": "路人"}, item["id"], decision="approve")
        decided = workflow.decide_request(self.conn, ADMIN, item["id"], decision="reject", note="管理员代批")
        self.assertEqual("rejected", decided["status"])

    def test_student_cancel_and_teacher_manual_withdraw_auto_cancel(self):
        item = self._create()
        self.conn.commit()
        cancelled = workflow.cancel_request(self.conn, STUDENT, item["id"])
        self.conn.commit()
        self.assertEqual("cancelled", cancelled["status"])
        with self.assertRaises(ApprovalWorkflowError):
            workflow.cancel_request(self.conn, STUDENT, item["id"])
        # A fresh request is auto-cancelled when the teacher withdraws directly.
        second = self._create(reason="再来一次")
        self.conn.commit()
        assignment = dict(self.conn.execute("SELECT * FROM assignments WHERE id = 'hw1'").fetchone())
        submission = dict(self.conn.execute("SELECT * FROM submissions WHERE id = 1").fetchone())
        due = returns.resolve_resubmission_due_at(assignment)
        returns.return_submissions_for_resubmission(self.conn, assignment=assignment, targets=[submission], teacher_id=7,
                                                    resubmission_due_at=due, reason="线下补交")
        self.conn.commit()
        row = self.conn.execute("SELECT status, decision_note FROM approval_requests WHERE id = ?", (second["id"],)).fetchone()
        self.assertEqual("cancelled", row["status"])
        self.assertIn("教师已直接撤回", row["decision_note"])
        events = [r["event_type"] for r in self.conn.execute("SELECT event_type FROM approval_request_events WHERE request_id = ? ORDER BY id", (second["id"],))]
        self.assertEqual(["created", "auto_cancelled"], events)

    def test_reminder_sweep_reminds_once_then_expires(self):
        item = self._create()
        self.conn.commit()
        self.notifications.clear()
        now = datetime.now()
        self.assertEqual({"reminded": 0, "expired": 0}, workflow.remind_stale_requests(self.conn, now=now))
        result = workflow.remind_stale_requests(self.conn, now=now + timedelta(hours=49))
        self.assertEqual(2, result["reminded"])
        self.assertEqual({"reminded": 0, "expired": 0}, workflow.remind_stale_requests(self.conn, now=now + timedelta(hours=50)))
        result = workflow.remind_stale_requests(self.conn, now=now + timedelta(days=8))
        self.assertEqual(1, result["expired"])
        self.assertEqual("expired", self.conn.execute("SELECT status FROM approval_requests WHERE id = ?", (item["id"],)).fetchone()["status"])
        self.assertIn("已过期", self.notifications[-1]["title"])

    # ------------------------------------------------------------------ deadline rule
    def test_resolve_resubmission_due_at_rule(self):
        now = datetime(2026, 9, 11, 15, 0, 0)
        future = {"due_at": "2026-09-13T08:00:00"}
        self.assertEqual("2026-09-13T08:00:00", returns.resolve_resubmission_due_at(future, now_dt=now))
        self.assertEqual("2026-09-12T15:00:00", returns.resolve_resubmission_due_at({"due_at": "2026-09-10T08:00:00"}, now_dt=now))
        self.assertEqual("2026-09-12T15:00:00", returns.resolve_resubmission_due_at({}, now_dt=now))
        late = {"due_at": "2026-09-10T08:00:00", "late_submission_enabled": 1, "late_submission_until": "2026-09-14T08:00:00"}
        self.assertEqual("2026-09-14T08:00:00", returns.resolve_resubmission_due_at(late, now_dt=now))
        # Explicit later than the assignment deadline wins; explicit earlier is lifted to the deadline.
        self.assertEqual("2026-09-20T08:00:00", returns.resolve_resubmission_due_at(future, explicit_due_at="2026-09-20T08:00", now_dt=now))
        self.assertEqual("2026-09-13T08:00:00", returns.resolve_resubmission_due_at(future, explicit_due_at="2026-09-11T18:00", now_dt=now))
        self.assertEqual("2026-09-13T08:00:00", returns.resolve_resubmission_due_at(future, extension_minutes=120, now_dt=now))
        self.assertEqual("2026-09-12T15:00:00", returns.resolve_resubmission_due_at({}, extension_minutes=120, now_dt=now))
        with self.assertRaises(ValueError):
            returns.resolve_resubmission_due_at(future, explicit_due_at="2026-09-11T14:00", now_dt=now)
        self.assertTrue(returns.payload_has_explicit_deadline({"extension_minutes": 30}))
        self.assertFalse(returns.payload_has_explicit_deadline({"reason": "x", "extension_minutes": ""}))

    def test_registry_lists_types_and_rejects_unknown(self):
        keys = [item["key"] for item in workflow.list_request_types()]
        self.assertIn("submission_withdraw", keys)
        with self.assertRaises(ApprovalWorkflowError) as error:
            workflow.create_request(self.conn, STUDENT, request_type="nope", subject_id=1, reason="x")
        self.assertEqual(404, error.exception.status_code)


if __name__ == "__main__":
    unittest.main()
