"""Return a graded/submitted answer to the student and open a resubmission window.

This is the single implementation behind the teacher's manual "撤回提交" action
and the approved student "撤回重做" request, so both paths reset the same
columns, retire the same grade revisions and honour the same deadline rule.

Deadline rule (2026-09-11): the new window ends at the *later* of
  * the teacher's explicit deadline (or ``now + extension_minutes``), and
  * the assignment's own effective deadline (late-submission cutoff if enabled,
    otherwise ``due_at``); when that is missing or already past, ``now + 24h``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..db.connection import begin_immediate_transaction
from .assignment_lifecycle_service import (
    _dt_to_iso,
    _parse_iso_like_datetime,
    _parse_resubmission_minutes,
    _utc_like_now,
)
from .grading_revision_service import retire_submission_grade_for_replacement
from .group_assignment_service import invalidate_member_work_score, lock_group_grading_for_students
from .submission_write_guard import lock_submission_writer, verify_submission_write

RESUBMISSION_FALLBACK_HOURS = 24


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def assignment_effective_deadline(assignment: dict[str, Any] | None) -> datetime | None:
    """The last moment the assignment itself still accepts work (late cutoff wins when enabled)."""
    assignment = dict(assignment or {})
    if _is_truthy(assignment.get("late_submission_enabled")):
        late_until = _parse_iso_like_datetime(assignment.get("late_submission_until"))
        if late_until is not None:
            return late_until
    return _parse_iso_like_datetime(assignment.get("due_at"))


def resolve_resubmission_due_at(
    assignment: dict[str, Any] | None,
    *,
    explicit_due_at: Any = None,
    extension_minutes: Any = None,
    now_dt: datetime | None = None,
) -> str:
    now_dt = (now_dt or _utc_like_now()).replace(microsecond=0)
    base = assignment_effective_deadline(assignment)
    if base is None or base <= now_dt:
        base = now_dt + timedelta(hours=RESUBMISSION_FALLBACK_HOURS)
    explicit = _parse_iso_like_datetime(explicit_due_at)
    if explicit is None and extension_minutes not in (None, ""):
        explicit = now_dt + timedelta(minutes=_parse_resubmission_minutes(extension_minutes, 0))
    if explicit is not None and explicit <= now_dt:
        raise ValueError("重交截止时间必须晚于当前时间")
    return _dt_to_iso(max(base, explicit) if explicit is not None else base)


def payload_has_explicit_deadline(payload: dict[str, Any] | None) -> bool:
    payload = payload or {}
    return any(str(payload.get(key) or "").strip() for key in ("resubmission_due_at", "reopen_until", "due_at", "extension_minutes"))


def return_submissions_for_resubmission(
    conn,
    *,
    assignment: dict[str, Any],
    targets: list[dict[str, Any]],
    teacher_id: int,
    resubmission_due_at: str,
    reason: str | None,
    request_note: str = "",
    begin_transaction: bool = True,
) -> list[int]:
    """Reset the targets to 'submitted' with an open resubmission window.

    The caller owns the connection; this function opens the write transaction
    (unless ``begin_transaction`` is False because the caller already did),
    leaves it uncommitted (callers commit) and returns the updated submission ids.
    Pending withdraw requests on the same submissions are auto-cancelled so a
    student never sees a stale "审批中" once the teacher has acted directly.
    """
    if not targets:
        return []
    assignment_id = assignment["id"]
    if begin_transaction:
        begin_immediate_transaction(conn)
    lock_group_grading_for_students(conn, assignment_id, [int(row["student_pk_id"]) for row in targets])
    for target in sorted(targets, key=lambda row: int(row["student_pk_id"])):
        lock_submission_writer(conn, assignment_id, int(target["student_pk_id"]))
        current = conn.execute("SELECT * FROM submissions WHERE id = ?", (int(target["id"]),)).fetchone()
        verify_submission_write(current, target, actor_role="teacher")
        retire_submission_grade_for_replacement(conn, dict(current))
        invalidate_member_work_score(conn, assignment_id=assignment_id, student_pk_id=int(target["student_pk_id"]))

    now_iso = datetime.now().isoformat()
    target_ids = [int(row["id"]) for row in targets]
    placeholders = ",".join("?" for _ in target_ids)
    conn.execute(
        f"""
        UPDATE submissions
        SET status = 'submitted',
            score = NULL,
            feedback_md = NULL,
            grading_started_at = NULL,
            grading_attempt_fingerprint = NULL,
            grading_revision_hash = NULL,
            grading_job_id = NULL,
            active_grade_revision_id = NULL,
            score_before_late_penalty = NULL,
            late_penalty_points = 0,
            late_score_cap_applied = 0,
            resubmission_allowed = 1,
            resubmission_due_at = ?,
            returned_at = ?,
            returned_by_teacher_id = ?,
            returned_reason = ?
        WHERE assignment_id = ?
          AND id IN ({placeholders})
        """,
        (resubmission_due_at, now_iso, int(teacher_id), (str(reason or "").strip() or None), assignment_id, *target_ids),
    )
    from .approval_workflow_service import auto_cancel_requests

    auto_cancel_requests(
        conn,
        request_type="submission_withdraw",
        subject_ids=[str(item) for item in target_ids],
        note=request_note or "教师已直接撤回该提交并开放重交，申请自动结束。",
        actor={"role": "teacher", "id": int(teacher_id)},
    )
    return target_ids
