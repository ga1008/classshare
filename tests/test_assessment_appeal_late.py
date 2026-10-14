"""超时补交 appeal + personal windows on the real SQLite schema (no network)."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException

from tests.sqlite_database_fixture import isolated_sqlite_database
from classroom_app.database import get_db_connection
from classroom_app.services import approval_request_types  # noqa: F401  registers types
from classroom_app.services import approval_workflow_service as workflow
from classroom_app.services import assignment_student_window_service as windows
from classroom_app.services.approval_workflow_service import ApprovalWorkflowError
from classroom_app.services.assessment_appeal_service import (
    APPEAL_LATE,
    appeal_subject_id,
    build_student_appeal_state,
)
from classroom_app.services.late_submission_policy import apply_late_policy_to_score

TEACHER = {"role": "teacher", "id": 9101, "name": "李老师"}
STUDENT = {"role": "student", "id": 9201, "name": "小周"}
ASSIGNMENT_ID = "9301"
SUBJECT = appeal_subject_id(ASSIGNMENT_ID, STUDENT["id"])


def _iso(delta: timedelta) -> str:
    return (datetime.now() + delta).replace(microsecond=0).isoformat()


class LateSubmissionAppealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        from classroom_app.services import approval_workflow_schema

        cls.enterClassContext(patch.object(approval_workflow_schema, "_SCHEMA_READY", False))
        cls.enterClassContext(patch.object(windows, "_SCHEMA_READY", False))
        with get_db_connection() as conn:
            approval_workflow_schema.ensure_approval_workflow_schema(conn)
            conn.execute("INSERT INTO teachers (id, name, email, hashed_password, is_active) VALUES (?, ?, ?, ?, 1)",
                         (TEACHER["id"], TEACHER["name"], "t9101@example.test", "x"))
            conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (9401, '软件3班', ?)", (TEACHER["id"],))
            conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (9501, '操作系统', ?)", (TEACHER["id"],))
            conn.execute("INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (9601, 9401, 9501, ?)",
                         (TEACHER["id"],))
            conn.execute("INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, 'S9201', ?, 9401)",
                         (STUDENT["id"], STUDENT["name"]))
            conn.commit()

    def setUp(self):
        with get_db_connection() as conn:
            windows.ensure_student_window_schema(conn)
            for sql in ("DELETE FROM approval_request_events", "DELETE FROM approval_request_reviewers",
                        "DELETE FROM approval_requests", "DELETE FROM scheduled_tasks", "DELETE FROM submissions",
                        "DELETE FROM assignment_student_windows", "DELETE FROM assignments"):
                conn.execute(sql)
            self._insert_assignment(conn, due=_iso(-timedelta(days=1)), status="closed")
            conn.commit()
        for target in (patch.object(workflow, "_notify", return_value=0),
                       patch.object(windows, "_notify_window", return_value=None)):
            target.start()
            self.addCleanup(target.stop)

    def _insert_assignment(self, conn, *, due: str, status: str, late_enabled: int = 0):
        conn.execute(
            """
            INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, due_at, created_at,
                                     grading_mode, assessment_kind, late_submission_enabled, late_penalty_strategy,
                                     late_penalty_interval_hours, late_penalty_points, late_penalty_min_score)
            VALUES (?, 9501, 9601, '第7次作业', '完成实验', ?, ?, ?, 'ai', 'homework', ?, 'gradient', 24, 10, 0)
            """,
            (ASSIGNMENT_ID, status, due, datetime.now().isoformat(), late_enabled),
        )

    def _create(self, expected=90):
        with get_db_connection() as conn:
            item = workflow.create_request(conn, STUDENT, request_type=APPEAL_LATE, subject_id=SUBJECT,
                                           reason="生病住院错过了截止时间，附医院证明", payload={"expected_minutes": expected})
            conn.commit()
            return item

    def _approve(self, request_id, payload):
        with get_db_connection() as conn:
            item = workflow.decide_request(conn, TEACHER, request_id, decision="approve", note="同意", decision_payload=payload)
            conn.commit()
            return item

    def _one(self, sql, params=()):
        with get_db_connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql, params=()):
        with get_db_connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def _assignment(self):
        return self._one("SELECT * FROM assignments WHERE id = ?", (ASSIGNMENT_ID,))

    # ------------------------------------------------------------------ eligibility
    def test_closed_assignment_without_submission_can_apply(self):
        payload = self._create()["payload"]
        self.assertEqual(payload["expected_minutes"], 90)
        self.assertGreater(payload["missed_by_seconds"], 80000)
        self.assertEqual(payload["previous_grants"], 0)

    def test_open_assignment_cannot_apply(self):
        with get_db_connection() as conn:
            conn.execute("UPDATE assignments SET status = 'published', due_at = ? WHERE id = ?",
                         (_iso(timedelta(days=1)), ASSIGNMENT_ID))
            conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._create()
        self.assertIn("直接提交", ctx.exception.message)

    def test_real_submission_cannot_apply(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at) "
                         "VALUES (?, ?, ?, 'submitted', ?)",
                         (ASSIGNMENT_ID, STUDENT["id"], STUDENT["name"], _iso(-timedelta(days=2))))
            conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._create()
        self.assertIn("错误提交", ctx.exception.message)

    def test_absence_placeholder_can_apply_and_student_state_lists_late_type(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, score, is_absence_score, "
                         "submission_channel, submitted_at) VALUES (?, ?, ?, 'unsubmitted', 0, 1, 'absence_zero', ?)",
                         (ASSIGNMENT_ID, STUDENT["id"], STUDENT["name"], _iso(timedelta())))
            conn.commit()
            state = build_student_appeal_state(conn, assignment=self._assignment(), student_pk_id=STUDENT["id"], submission=None)
        late = next(t for t in state["types"] if t["key"] == APPEAL_LATE)
        self.assertTrue(late["available"])
        self.assertTrue(self._create()["payload"]["had_absence_score"])

    # ------------------------------------------------------------------ approval
    def test_approve_opens_window_with_custom_seconds_policy(self):
        item = self._create()
        decided = self._approve(item["id"], {
            "window_mode": "duration", "duration_value": "2", "duration_unit": "hour", "penalty_mode": "custom",
            "penalty_policy": {"strategy": "gradient", "points": 2, "interval_value": 30, "interval_unit": "second",
                               "lateness_base": "window_open", "max_penalty_points": 15},
        })
        self.assertEqual(decided["status"], "approved")
        window = self._one("SELECT * FROM assignment_student_windows WHERE assignment_id = ?", (ASSIGNMENT_ID,))
        self.assertEqual((window["status"], window["grant_no"], window["penalty_mode"]), ("active", 1, "custom"))
        self.assertEqual(json.loads(window["penalty_policy_json"])["interval_seconds"], 30)
        self.assertIn("每 30 秒扣 2 分", decided["decision_payload"]["penalty_label"])
        tasks = {row["task_kind"] for row in self._all("SELECT task_kind FROM scheduled_tasks")}
        self.assertTrue({windows.TASK_KIND_WINDOW_EXPIRY, windows.TASK_KIND_WINDOW_REMINDER} <= tasks)

    def test_inherit_requires_assignment_rule(self):
        item = self._create()
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._approve(item["id"], {"window_mode": "duration", "duration_value": "1", "duration_unit": "day",
                                       "penalty_mode": "inherit"})
        self.assertIn("没有设置补交扣分规则", ctx.exception.message)

    def test_window_lets_student_past_closed_assignment(self):
        from classroom_app.routers.homework_parts.common import _ensure_accepting_submission, _personal_window_for_save

        assignment = self._assignment()
        with get_db_connection() as conn:
            with self.assertRaises(HTTPException):
                _ensure_accepting_submission(assignment, conn=conn, student_pk_id=STUDENT["id"])
        item = self._create()
        self._approve(item["id"], {"window_mode": "duration", "duration_value": "30", "duration_unit": "minute",
                                   "penalty_mode": "none"})
        with get_db_connection() as conn:
            _ensure_accepting_submission(assignment, conn=conn, student_pk_id=STUDENT["id"])
            self.assertIsNotNone(_personal_window_for_save(conn, assignment, STUDENT["id"], None, "student"))
            self.assertIsNone(_personal_window_for_save(conn, assignment, STUDENT["id"], None, "teacher"))
            state = build_student_appeal_state(conn, assignment=assignment, student_pk_id=STUDENT["id"], submission=None)
        late = next(t for t in state["types"] if t["key"] == APPEAL_LATE)
        self.assertFalse(late["available"])
        self.assertIn("补交通道已开启", late["reason"])

    def test_two_grants_maximum(self):
        for _ in range(2):
            item = self._create()
            self._approve(item["id"], {"window_mode": "duration", "duration_value": "3", "duration_unit": "hour",
                                       "penalty_mode": "none"})
            with get_db_connection() as conn:
                window = conn.execute("SELECT id FROM assignment_student_windows WHERE status = 'active'").fetchone()
                windows.expire_window(conn, window["id"], now=datetime.now() + timedelta(hours=4))
                conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._create()
        self.assertEqual(ctx.exception.status_code, 409)
        grants = [row["grant_no"] for row in self._all("SELECT grant_no FROM assignment_student_windows ORDER BY id")]
        self.assertEqual(grants, [1, 2])

    def test_expiry_and_closeout_skip(self):
        from classroom_app.services.classroom_closeout_service import apply_absence_scores

        item = self._create()
        self._approve(item["id"], {"window_mode": "duration", "duration_value": "3", "duration_unit": "hour",
                                   "penalty_mode": "none"})
        assignment = {**self._assignment(), "class_id": 9401}
        with get_db_connection() as conn:
            result = apply_absence_scores(conn, assignment, teacher_id=TEACHER["id"])
            conn.commit()
        self.assertEqual(result["window_open_student_ids"], [STUDENT["id"]])
        self.assertIsNone(self._one("SELECT id FROM submissions WHERE student_pk_id = ?", (STUDENT["id"],)))
        window = self._one("SELECT id FROM assignment_student_windows")
        with get_db_connection() as conn:
            self.assertEqual(windows.expire_window(conn, window["id"]), "not_due")
            self.assertEqual(windows.expire_window(conn, window["id"], now=datetime.now() + timedelta(hours=4)), "expired")
            conn.commit()
        self.assertEqual(self._one("SELECT status FROM assignment_student_windows")["status"], "expired")


    # ------------------------------------------------------------------ review fixes
    def test_student_outside_class_cannot_apply(self):
        outsider = {"role": "student", "id": 9299, "name": "外班"}
        with get_db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO classes (id, name, created_by_teacher_id) VALUES (9499, '外班', ?)", (TEACHER["id"],))
            conn.execute("INSERT OR IGNORE INTO students (id, student_id_number, name, class_id) VALUES (9299, 'S9299', '外班', 9499)")
            conn.commit()
            with self.assertRaises(ApprovalWorkflowError) as ctx:
                workflow.create_request(conn, outsider, request_type=APPEAL_LATE,
                                        subject_id=appeal_subject_id(ASSIGNMENT_ID, 9299), reason="想补交这份作业可以吗")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_submit_started_after_window_closed_is_refused(self):
        from classroom_app.routers.homework_parts.common import _personal_window_for_save

        item = self._create()
        self._approve(item["id"], {"window_mode": "duration", "duration_value": "30", "duration_unit": "minute",
                                   "penalty_mode": "none"})
        assignment = self._assignment()
        with get_db_connection() as conn:
            inside = (datetime.now() + timedelta(minutes=29)).isoformat()
            self.assertIsNotNone(_personal_window_for_save(conn, assignment, STUDENT["id"], None, "student", submitted_at=inside))
            after = (datetime.now() + timedelta(minutes=31)).isoformat()
            with self.assertRaises(HTTPException) as ctx:
                _personal_window_for_save(conn, assignment, STUDENT["id"], None, "student", submitted_at=after)
        self.assertIn("补交通道已关闭", ctx.exception.detail)

    def test_approve_refused_when_assignment_reopened(self):
        item = self._create()
        with get_db_connection() as conn:
            conn.execute("UPDATE assignments SET status = 'published', due_at = ? WHERE id = ?", (_iso(timedelta(days=1)), ASSIGNMENT_ID))
            conn.commit()
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self._approve(item["id"], {"window_mode": "duration", "duration_value": "1", "duration_unit": "hour",
                                       "penalty_mode": "none"})
        self.assertIn("重新接收", ctx.exception.message)

    def test_expiry_after_real_submission_marks_used(self):
        item = self._create()
        self._approve(item["id"], {"window_mode": "duration", "duration_value": "3", "duration_unit": "hour",
                                   "penalty_mode": "none"})
        with get_db_connection() as conn:
            conn.execute("INSERT INTO submissions (assignment_id, student_pk_id, student_name, status, submitted_at) "
                         "VALUES (?, ?, ?, 'submitted', ?)", (ASSIGNMENT_ID, STUDENT["id"], STUDENT["name"], _iso(timedelta())))
            window = conn.execute("SELECT id FROM assignment_student_windows").fetchone()
            self.assertEqual(windows.expire_window(conn, window["id"], now=datetime.now() + timedelta(hours=4)), "used")
            conn.commit()
        self.assertEqual(self._one("SELECT status FROM assignment_student_windows")["status"], "used")


    def test_teacher_list_marks_only_open_windows(self):
        from classroom_app.services.assessment_appeal_service import load_submission_appeal_marks

        item = self._create()
        self._approve(item["id"], {"window_mode": "duration", "duration_value": "3", "duration_unit": "hour",
                                   "penalty_mode": "none"})
        with get_db_connection() as conn:
            self.assertIn(STUDENT["id"], load_submission_appeal_marks(conn, ASSIGNMENT_ID)["windows"])
            # Past due but the expiry task has not run yet: not "open" any more.
            conn.execute("UPDATE assignment_student_windows SET due_at = ?", (_iso(-timedelta(minutes=1)),))
            self.assertNotIn(STUDENT["id"], load_submission_appeal_marks(conn, ASSIGNMENT_ID)["windows"])


class WindowPenaltyMathTests(unittest.TestCase):
    ASSIGNMENT = {"due_at": "2026-10-10T08:00:00"}

    def _score(self, policy, submitted_at, raw=90):
        window = {"id": 1, "grant_no": 1, "opens_at": "2026-10-12T08:00:00", "due_at": "2026-10-13T08:00:00",
                  "penalty_policy_json": json.dumps(windows.normalize_penalty_policy(policy)), "source_request_id": 7}
        snapshot = windows.window_late_snapshot(self.ASSIGNMENT, window, submitted_at)
        return apply_late_policy_to_score(raw, submission={"late_policy_snapshot_json": json.dumps(snapshot)})

    def test_gradient_seconds_from_window_open_with_cap(self):
        policy = {"strategy": "gradient", "points": 1, "interval_value": 10, "interval_unit": "minute",
                  "lateness_base": "window_open", "max_penalty_points": 5}
        self.assertEqual(self._score(policy, "2026-10-12T08:25:00")["penalty_points"], 3)
        self.assertEqual(self._score(policy, "2026-10-12T10:00:00")["penalty_points"], 5)

    def test_gradient_continues_from_original_deadline(self):
        result = self._score({"strategy": "gradient", "points": 5, "interval_value": 1, "interval_unit": "day"},
                             "2026-10-12T09:00:00")
        self.assertEqual(result["penalty_points"], 15)  # 2 days 1 hour late → 3 steps

    def test_none_keeps_score(self):
        result = self._score({"strategy": "none"}, "2026-10-12T09:00:00")
        self.assertFalse(result["applied"])
        self.assertEqual(result["final_score"], 90)

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            windows.normalize_penalty_policy({"strategy": "gradient", "points": 120})
        with self.assertRaises(ValueError):
            windows.normalize_penalty_policy({"strategy": "weird"})
        label = windows.describe_penalty_policy(windows.normalize_penalty_policy(
            {"strategy": "fixed", "points": 10, "score_cap": 80}))
        self.assertEqual(label, "一次性扣 10 分，补交最高 80 分")


if __name__ == "__main__":
    unittest.main()
