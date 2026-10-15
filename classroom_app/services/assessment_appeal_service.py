"""Shared rules for student assessment appeals (作业/考试申诉).

Three approval request types build on the generic approval workflow:

* ``grading_appeal``        批改有误 — re-grade (teacher edit or AI re-grade)
* ``submission_correction`` 错误提交 — interrupt grading, return for editing
* ``late_submission``       超时补交 — open a personal late window

All three address one *answer* = (assignment, student). They share a subject
id ``"{assignment_id}:{student_pk_id}"`` (the student may have no submission
row yet for a late appeal) and a dedupe key, so at most one appeal of any type
is pending per answer. The legacy ``submission_withdraw`` type keeps its own
subject (the submission id) and is checked explicitly.

See docs/homework-exam-improvement-plan-2026-10-13.md §3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .approval_workflow_schema import APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_PENDING
from .approval_workflow_service import ApprovalWorkflowError, count_requests, ensure_approval_workflow_schema

APPEAL_GRADING = "grading_appeal"
APPEAL_CORRECTION = "submission_correction"
APPEAL_LATE = "late_submission"
LEGACY_WITHDRAW = "submission_withdraw"
APPEAL_TYPES = (APPEAL_GRADING, APPEAL_CORRECTION, APPEAL_LATE)
SUBJECT_TYPE = "assignment_student"

# Limits per answer (plan §3.2 / §14 decisions).
CORRECTION_LIMIT = 3
GRADING_APPEAL_LIMIT = 3
LATE_APPROVED_LIMIT = 2
LATE_TOTAL_LIMIT = 4

TASK_KIND_RESUME_GRADING = "appeal_resume_grading"
AI_GRADING_MODES = frozenset({"ai", "auto", "mixed"})


def _query_with_schema_retry(conn, sql: str, params: tuple):
    """fetchone() on the approval tables, creating them once if this database lacks them.

    These lookups sit on hot submit/grading paths. A process-wide "schema
    ready" flag can be stale for a freshly attached database; mirror
    ``auto_cancel_requests`` and ensure the tables again instead of failing.
    """
    ensure_approval_workflow_schema(conn)
    try:
        return conn.execute(sql, params).fetchone()
    except Exception as exc:  # noqa: BLE001
        if "approval_requests" not in str(exc):
            raise
        from . import approval_workflow_schema as schema

        schema._SCHEMA_READY = False
        ensure_approval_workflow_schema(conn)
        return conn.execute(sql, params).fetchone()


def appeal_subject_id(assignment_id: Any, student_pk_id: Any) -> str:
    return f"{assignment_id}:{int(student_pk_id)}"


def parse_appeal_subject_id(subject_id: Any) -> tuple[str, int]:
    text = str(subject_id or "").strip()
    assignment_id, _, student = text.rpartition(":")
    try:
        student_pk_id = int(student)
    except ValueError as exc:
        raise ApprovalWorkflowError(400, "申诉对象无效") from exc
    if not assignment_id:
        raise ApprovalWorkflowError(400, "申诉对象无效")
    return assignment_id, student_pk_id


def appeal_dedupe_key(assignment_id: Any, student_pk_id: Any) -> str:
    return f"assessment_appeal:{appeal_subject_id(assignment_id, student_pk_id)}"


def load_appeal_context(conn, assignment_id: Any, student_pk_id: Any) -> dict[str, Any]:
    """Assignment + offering/course reviewers + the student's submission row (may be absent)."""
    row = conn.execute(
        """
        SELECT a.*, c.created_by_teacher_id, owner_t.name AS owner_teacher_name,
               o.teacher_id AS offering_teacher_id, offering_t.name AS offering_teacher_name
        FROM assignments a
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN teachers owner_t ON owner_t.id = c.created_by_teacher_id
        LEFT JOIN teachers offering_t ON offering_t.id = o.teacher_id
        WHERE a.id = ?
        LIMIT 1
        """,
        (str(assignment_id),),
    ).fetchone()
    if not row:
        raise ApprovalWorkflowError(404, "作业不存在")
    assignment = dict(row)
    submission = conn.execute(
        "SELECT * FROM submissions WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
        (str(assignment_id), int(student_pk_id)),
    ).fetchone()
    student = conn.execute("SELECT id, name FROM students WHERE id = ? LIMIT 1", (int(student_pk_id),)).fetchone()
    return {
        "assignment": assignment,
        "submission": dict(submission) if submission else None,
        "student_name": (dict(student).get("name") if student else "") or "",
    }


def appeal_reviewers(assignment: Mapping[str, Any]) -> list[dict[str, Any]]:
    reviewers: list[dict[str, Any]] = []
    for id_key, name_key in (("offering_teacher_id", "offering_teacher_name"), ("created_by_teacher_id", "owner_teacher_name")):
        teacher_id = assignment.get(id_key)
        if teacher_id and all(int(item["id"]) != int(teacher_id) for item in reviewers):
            reviewers.append({"role": "teacher", "id": int(teacher_id), "name": assignment.get(name_key) or ""})
    return reviewers


def is_real_submission(submission: Mapping[str, Any] | None) -> bool:
    """A student-made submission, not an absence placeholder."""
    if not submission:
        return False
    return not int(submission.get("is_absence_score") or 0) and str(submission.get("status") or "") != "unsubmitted"


def pending_appeal(conn, assignment_id: Any, student_pk_id: Any, submission_id: Any = None) -> dict[str, Any] | None:
    """Any pending appeal (or legacy withdraw request) on this answer."""
    ensure_approval_workflow_schema(conn)
    row = conn.execute(
        f"SELECT * FROM approval_requests WHERE status = 'pending' AND subject_id = ? "
        f"AND request_type IN ({','.join('?' for _ in APPEAL_TYPES)}) ORDER BY id DESC LIMIT 1",
        (appeal_subject_id(assignment_id, student_pk_id), *APPEAL_TYPES),
    ).fetchone()
    if row:
        return dict(row)
    if submission_id not in (None, ""):
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE status = 'pending' AND request_type = ? AND subject_id = ? LIMIT 1",
            (LEGACY_WITHDRAW, str(submission_id)),
        ).fetchone()
        if row:
            return dict(row)
    return None


def cancel_pending_appeals_for_answer(conn, assignment_id: Any, student_pk_id: Any, *, note: str,
                                      actor: Mapping[str, Any] | None = None, submission_id: Any = None) -> int:
    """Close pending appeals when the answer they target is removed (student withdraw, teacher return).

    Runs inside the caller's transaction, after the submission row is gone, so the
    per-type auto-cancel hooks see no submission and release nothing twice.
    """
    from .approval_workflow_service import auto_cancel_requests

    subject_ids = [appeal_subject_id(assignment_id, student_pk_id)]
    if submission_id not in (None, ""):
        subject_ids.append(str(submission_id))  # legacy withdraw requests key on the submission id
    return auto_cancel_requests(conn, request_type=(*APPEAL_TYPES, LEGACY_WITHDRAW), subject_ids=subject_ids,
                                note=note, actor=actor)


def ensure_no_pending_appeal(conn, assignment_id: Any, student_pk_id: Any, submission_id: Any = None) -> None:
    if pending_appeal(conn, assignment_id, student_pk_id, submission_id):
        raise ApprovalWorkflowError(409, "这份答题已有一条申诉在处理中，请等待教师处理后再申请")


def correction_attempts(conn, assignment_id: Any, student_pk_id: Any, submission_id: Any = None) -> int:
    """错误提交 applications so far; legacy 撤回重做 requests count toward the same limit."""
    total = count_requests(conn, request_types=[APPEAL_CORRECTION],
                           subject_ids=[appeal_subject_id(assignment_id, student_pk_id)])
    if submission_id not in (None, ""):
        total += count_requests(conn, request_types=[LEGACY_WITHDRAW], subject_ids=[str(submission_id)])
    return total


def grading_appeal_attempts(conn, assignment_id: Any, student_pk_id: Any) -> int:
    return count_requests(conn, request_types=[APPEAL_GRADING],
                          subject_ids=[appeal_subject_id(assignment_id, student_pk_id)])


def late_attempts(conn, assignment_id: Any, student_pk_id: Any) -> tuple[int, int]:
    """(approved grants, total applications) for 超时补交."""
    subject = [appeal_subject_id(assignment_id, student_pk_id)]
    approved = count_requests(conn, request_types=[APPEAL_LATE], subject_ids=subject,
                              statuses=[APPROVAL_STATUS_APPROVED], include_auto_cancelled=True)
    total = count_requests(conn, request_types=[APPEAL_LATE], subject_ids=subject)
    return approved, total


def submission_grading_hold(conn, submission_id: Any) -> str | None:
    """Reason AI grading must not start for this submission, or None.

    A pending 错误提交 appeal means the student said the content is wrong:
    grading it would waste a model call and publish a score for answers the
    student is about to replace.
    """
    try:
        row = conn.execute("SELECT assignment_id, student_pk_id FROM submissions WHERE id = ? LIMIT 1",
                           (int(submission_id),)).fetchone()
    except (TypeError, ValueError):
        return None
    if not row:
        return None
    pending = _query_with_schema_retry(
        conn,
        "SELECT id FROM approval_requests WHERE status = ? AND request_type = ? AND subject_id = ? LIMIT 1",
        (APPROVAL_STATUS_PENDING, APPEAL_CORRECTION, appeal_subject_id(row["assignment_id"], row["student_pk_id"])),
    )
    if pending:
        return "该生已申请退回修改（错误提交申诉处理中），请先处理申诉"
    return None


APPEAL_TYPE_META = {
    APPEAL_GRADING: {"label": "批改有误", "description": "认为批改不正确或有瑕疵，申请重新评分。"},
    APPEAL_CORRECTION: {"label": "错误提交", "description": "提交的内容或附件有误，申请退回修改；申请后批改会暂停。"},
    APPEAL_LATE: {"label": "超时补交", "description": "错过了提交时间，申请为你单独开放补交通道。"},
}
# Types whose student entry is live; later phases append theirs.
ENABLED_APPEAL_TYPES = [APPEAL_GRADING, APPEAL_CORRECTION, APPEAL_LATE]


def _correction_availability(conn, assignment_id: Any, student_pk_id: int, submission: Mapping[str, Any] | None,
                             pending: Mapping[str, Any] | None) -> tuple[bool, str, int]:
    used = correction_attempts(conn, assignment_id, student_pk_id, (submission or {}).get("id"))
    if not is_real_submission(submission):
        return False, "还没有提交，无需申请；已截止可选择“超时补交”", used
    if int(submission.get("resubmission_allowed") or 0):
        return False, "已退回，可直接修改后重新提交", used
    if pending:
        return False, "已有一条申诉在处理中", used
    if used >= CORRECTION_LIMIT:
        return False, f"已达到 {CORRECTION_LIMIT} 次上限", used
    return True, "", used


def build_student_appeal_state(conn, *, assignment: Mapping[str, Any], student_pk_id: int,
                               submission: Mapping[str, Any] | None) -> dict[str, Any]:
    """Everything the student-side 申诉 entry needs, computed server-side."""
    from .approval_workflow_service import serialize_request

    ensure_approval_workflow_schema(conn)
    assignment_id = str(assignment["id"])
    subject_id = appeal_subject_id(assignment_id, student_pk_id)
    submission_id = (submission or {}).get("id")
    params: list[Any] = [subject_id, *APPEAL_TYPES]
    legacy_clause = ""
    if submission_id:
        legacy_clause = " OR (request_type = ? AND subject_id = ?)"
        params.extend([LEGACY_WITHDRAW, str(submission_id)])
    rows = conn.execute(
        f"SELECT * FROM approval_requests WHERE (subject_id = ? AND request_type IN ({','.join('?' for _ in APPEAL_TYPES)}))"
        f"{legacy_clause} ORDER BY id DESC LIMIT 10",
        tuple(params),
    ).fetchall()
    applicant = {"role": "student", "id": int(student_pk_id)}
    history = [serialize_request(dict(row), viewer=applicant) for row in rows]
    pending = next((item for item in history if item["status"] == APPROVAL_STATUS_PENDING), None)
    types = []
    extra: dict[str, Any] = {}
    for key in ENABLED_APPEAL_TYPES:
        if key == APPEAL_CORRECTION:
            available, reason, used = _correction_availability(conn, assignment_id, student_pk_id, submission, pending)
            limit = CORRECTION_LIMIT
        elif key == APPEAL_GRADING:
            from .approval_request_types.grading_appeal import grading_appeal_availability
            from .grading_question_service import current_grade_questions

            available, reason, used = grading_appeal_availability(
                conn, assignment=assignment, student_pk_id=student_pk_id, submission=submission, pending=pending)
            limit = GRADING_APPEAL_LIMIT
            if available:
                extra["grading_questions"] = current_grade_questions(conn, submission)["questions"]
        elif key == APPEAL_LATE:
            from .approval_request_types.late_submission import late_availability

            raw_submission = submission
            if raw_submission is None:
                raw_submission = load_appeal_context(conn, assignment_id, student_pk_id)["submission"]
            available, reason, approved, total = late_availability(
                conn, assignment=assignment, student_pk_id=student_pk_id, submission=raw_submission, pending=pending)
            used, limit = approved, LATE_APPROVED_LIMIT
        else:  # pragma: no cover - unknown type
            continue
        types.append({"key": key, **APPEAL_TYPE_META[key], "available": available, "reason": reason,
                      "used": used, "limit": limit})
    return {
        "subject_id": subject_id,
        "submission_id": submission_id,
        "pending": pending,
        "latest": history[0] if history else None,
        "history": history,
        "types": types,
        **extra,
    }


def load_submission_appeal_marks(conn, assignment_id: Any) -> dict[str, dict[int, Any]]:
    """Pending appeals and open late windows per student for the teacher list (two indexed reads).

    One student may have only one pending appeal (cross-type dedupe key), so
    ``pending`` is keyed by student. Windows past their due time are excluded
    even before the expiry task flips them.
    """
    from .assignment_student_window_service import ensure_student_window_schema
    from .late_submission_policy import dt_to_iso

    ensure_approval_workflow_schema(conn)
    ensure_student_window_schema(conn)
    pending: dict[int, Any] = {}
    rows = conn.execute(
        f"SELECT id, request_type, subject_id FROM approval_requests WHERE assignment_id = ? AND status = ? "
        f"AND request_type IN ({','.join('?' for _ in APPEAL_TYPES)})",
        (str(assignment_id), APPROVAL_STATUS_PENDING, *APPEAL_TYPES),
    ).fetchall()
    for row in rows:
        try:
            _aid, student_pk_id = parse_appeal_subject_id(row["subject_id"])
        except ApprovalWorkflowError:
            continue
        pending[int(student_pk_id)] = {"request_id": int(row["id"]), "request_type": row["request_type"],
                                       "label": APPEAL_TYPE_META.get(row["request_type"], {}).get("label", "申诉")}
    now_iso = dt_to_iso(datetime.now().replace(microsecond=0))
    windows = {
        int(row["student_pk_id"]): row["due_at"]
        for row in conn.execute(
            "SELECT student_pk_id, due_at FROM assignment_student_windows "
            "WHERE assignment_id = ? AND status = 'active' AND due_at > ?",
            (str(assignment_id), now_iso),
        ).fetchall()
    }
    return {"pending": pending, "windows": windows}


def apply_submission_appeal_marks(entries: list[dict[str, Any]], marks: Mapping[str, Mapping[int, Any]]) -> int:
    """Add ``pending_appeal`` / ``late_window_due_at`` to list rows; returns the pending count."""
    pending, windows = marks.get("pending") or {}, marks.get("windows") or {}
    for entry in entries:
        student_pk_id = int(entry.get("student_pk_id") or 0)
        entry["pending_appeal"] = pending.get(student_pk_id)
        entry["late_window_due_at"] = windows.get(student_pk_id)
    return sum(1 for entry in entries if entry.get("pending_appeal"))


def resubmission_late_mode(conn, submission: Mapping[str, Any] | None) -> str:
    """'inherit' (default) or 'recompute' for the current resubmission round.

    Only an approved 错误提交 appeal decided in this round can ask to recompute
    lateness; every other return inherits the original submission's state.
    """
    if not submission or not int(submission.get("resubmission_allowed") or 0):
        return "inherit"
    row = _query_with_schema_retry(
        conn,
        "SELECT decision_payload_json, decided_at FROM approval_requests WHERE request_type = ? AND subject_id = ? "
        "AND status = ? ORDER BY id DESC LIMIT 1",
        (APPEAL_CORRECTION, appeal_subject_id(submission["assignment_id"], submission["student_pk_id"]),
         APPROVAL_STATUS_APPROVED),
    )
    if not row:
        return "inherit"
    import json

    try:
        decision = json.loads(row["decision_payload_json"] or "{}") or {}
    except (TypeError, ValueError):
        decision = {}
    # Same round only: the approval opened exactly this return (an extended
    # window keeps returned_at, so the teacher's choice survives extensions).
    if str(decision.get("returned_at") or "") != str(submission.get("returned_at") or ""):
        return "inherit"
    return "recompute" if decision.get("late_mode") == "recompute" else "inherit"


def schedule_resume_grading(conn, submission_id: int, *, reason: str) -> None:
    """Re-queue AI grading through the scheduler after a correction appeal ends.

    Enqueueing is async and opens its own connection, so it cannot run inside
    the approval transaction; the scheduler task fires right after commit and
    re-checks every precondition.
    """
    from .scheduled_task_service import schedule_task

    schedule_task(
        conn,
        task_kind=TASK_KIND_RESUME_GRADING,
        run_at=datetime.now(),
        payload={"submission_id": int(submission_id), "reason": str(reason or "")},
        dedupe_key=f"appeal-resume-grading:{int(submission_id)}",
        title="Resume AI grading after appeal",
        priority=60,
        max_attempts=3,
        replace=True,
    )


async def handle_resume_grading_task(task: Mapping[str, Any]) -> str:
    from ..database import get_db_connection
    from .ai_grading_service import AIGradingQueueError, submit_submission_for_ai_grading

    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    submission_id = int(payload.get("submission_id") or 0)
    if submission_id <= 0:
        return "skipped: no submission"
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT s.status, s.score, s.resubmission_allowed, s.is_absence_score, a.grading_mode
            FROM submissions s JOIN assignments a ON a.id = s.assignment_id
            WHERE s.id = ? LIMIT 1
            """,
            (submission_id,),
        ).fetchone()
        hold = submission_grading_hold(conn, submission_id) if row else None
    if not row:
        return "skipped: submission gone"
    if hold:
        return "skipped: still on hold"
    if (str(row["status"] or "") != "submitted" or row["score"] is not None
            or int(row["resubmission_allowed"] or 0) or int(row["is_absence_score"] or 0)):
        return f"skipped: status={row['status']}"
    if str(row["grading_mode"] or "").strip().lower() not in AI_GRADING_MODES:
        return "skipped: manual grading assignment"
    try:
        result = await submit_submission_for_ai_grading(submission_id, allow_graded=False)
    except AIGradingQueueError as exc:
        if int(exc.status_code) >= 500 or int(exc.status_code) == 429:
            raise  # transient (queue/gateway): let the scheduler retry
        # A terminal refusal (unsupported attachment, missing rubric…): the
        # answer is no longer held, so the teacher must grade it by hand.
        with get_db_connection() as conn:
            _notify_teacher_resume_failed(conn, submission_id, str(exc.detail))
            conn.commit()
        return f"failed: {exc.detail}"
    return f"resumed: {result.get('status')}"


def _notify_teacher_resume_failed(conn, submission_id: int, detail: str) -> None:
    from . import message_center_service as mc

    row = conn.execute(
        """
        SELECT s.id, s.student_name, a.title, a.class_offering_id, o.teacher_id
        FROM submissions s JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE s.id = ? LIMIT 1
        """,
        (int(submission_id),),
    ).fetchone()
    if not row or not row["teacher_id"]:
        return
    payload = mc._build_notification_payload(
        recipient_role="teacher", recipient_user_pk=int(row["teacher_id"]), category=mc.MESSAGE_CATEGORY_APPROVAL,
        title=f"需要人工批改：{row['title'] or '作业'}",
        body_preview=f"{row['student_name'] or '学生'} 的申诉已结束，但自动批改未能恢复：{detail}",
        link_url=f"/submission/{int(row['id'])}", class_offering_id=mc._safe_int(row["class_offering_id"]),
        ref_type="appeal_resume_grading", ref_id=f"{int(row['id'])}:resume-failed",
    )
    mc._insert_notification_if_allowed(conn, payload)
