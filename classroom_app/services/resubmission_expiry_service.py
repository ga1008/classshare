"""Close a lapsed resubmission window: the original submission stands.

Before this, a returned submission whose window expired stayed returned
forever — no score, no grading, invisible until term closeout. Now every
return schedules a one-shot task at the window deadline. If the student did
not resubmit in that round, the row goes back to exactly what it was:

* absence placeholder  -> restored placeholder with its recorded score
* graded (non-group)   -> the superseded grade revision is reactivated
* anything else        -> back to "submitted"; AI grading resumes if enabled

Group assignments never restore a stored score (it may be the group-mixed
final, and re-recording it would mix twice); they are re-graded instead.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .late_submission_policy import parse_iso_like_datetime
from .scheduled_task_service import schedule_task

TASK_KIND_RESUBMISSION_EXPIRY = "resubmission_window_expiry"
_SNAPSHOT_FIELDS = (
    "status", "score", "feedback_md", "active_grade_revision_id", "score_before_late_penalty",
    "late_penalty_points", "late_score_cap_applied", "is_absence_score", "absence_scored_at",
    "absence_scored_by_teacher_id", "submission_channel",
)


def snapshot_before_return(submission: Mapping[str, Any]) -> dict[str, Any]:
    return {field: submission.get(field) for field in _SNAPSHOT_FIELDS}


def schedule_resubmission_expiry(conn, *, submission_id: int, due_at: str, previous: Mapping[str, Any],
                                 origin: str) -> None:
    row = conn.execute("SELECT returned_at FROM submissions WHERE id = ? LIMIT 1", (int(submission_id),)).fetchone()
    schedule_task(
        conn,
        task_kind=TASK_KIND_RESUBMISSION_EXPIRY,
        run_at=due_at,
        payload={
            "submission_id": int(submission_id),
            "returned_at": (row["returned_at"] if row else None),
            "previous": dict(previous),
            "origin": origin,
        },
        dedupe_key=f"resubmission-expiry:{int(submission_id)}",
        title="Restore original submission when the resubmission window lapses",
        priority=80,
        max_attempts=3,
        replace=True,
    )


def _notify(conn, submission: Mapping[str, Any], assignment: Mapping[str, Any], *, restored_label: str) -> None:
    from . import message_center_service as mc

    title = f"修改期已结束：{assignment.get('title') or '作业'}"
    student_body = f"重新提交的时间已过，已按原提交为准（{restored_label}）。"
    recipients = [("student", int(submission["student_pk_id"]), student_body, f"/assignment/{assignment['id']}")]
    teacher_id = assignment.get("offering_teacher_id")
    if teacher_id:
        recipients.append(("teacher", int(teacher_id),
                           f"{submission.get('student_name') or '学生'} 未在修改期内重新提交，已按原提交为准（{restored_label}）。",
                           f"/submission/{int(submission['id'])}"))
    for role, user_pk, body, link in recipients:
        payload = mc._build_notification_payload(
            recipient_role=role, recipient_user_pk=user_pk, category=mc.MESSAGE_CATEGORY_APPROVAL,
            title=title, severity="normal", body_preview=body, link_url=link,
            class_offering_id=mc._safe_int(assignment.get("class_offering_id")),
            ref_type="resubmission_expiry",
            ref_id=f"{int(submission['id'])}:{submission.get('returned_at') or ''}",
        )
        mc._insert_notification_if_allowed(conn, payload)


def _delete_round_draft(conn, assignment_id: Any, student_pk_id: int) -> None:
    draft = conn.execute("SELECT id FROM submission_drafts WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
                         (str(assignment_id), int(student_pk_id))).fetchone()
    if not draft:
        return
    for row in conn.execute("SELECT stored_path FROM submission_draft_files WHERE draft_id = ?", (int(draft["id"]),)).fetchall():
        path = Path(str(row["stored_path"] or ""))
        if str(path) and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    conn.execute("DELETE FROM submission_draft_files WHERE draft_id = ?", (int(draft["id"]),))
    conn.execute("DELETE FROM submission_drafts WHERE id = ?", (int(draft["id"]),))


def restore_lapsed_resubmission(conn, payload: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """Apply the lapse rule inside the caller's transaction. Returns an outcome label."""
    from .assessment_appeal_service import schedule_resume_grading
    from .group_assignment_service import get_assignment_group_binding

    submission_id = int(payload.get("submission_id") or 0)
    row = conn.execute(
        """
        SELECT s.*, a.title AS assignment_title, a.class_offering_id, a.grading_mode, o.teacher_id AS offering_teacher_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        WHERE s.id = ? LIMIT 1
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        return "skipped: submission gone"
    submission = dict(row)
    if not int(submission.get("resubmission_allowed") or 0):
        return "skipped: already resubmitted or closed"
    if str(submission.get("returned_at") or "") != str(payload.get("returned_at") or ""):
        return "skipped: returned again in a later round"
    due = parse_iso_like_datetime(submission.get("resubmission_due_at"))
    current = now or datetime.now()
    if due is not None and due.replace(tzinfo=None) > current:
        schedule_resubmission_expiry(conn, submission_id=submission_id, due_at=due.replace(tzinfo=None).isoformat(),
                                     previous=payload.get("previous") or {}, origin=str(payload.get("origin") or ""))
        return "rescheduled: window was extended"

    previous = dict(payload.get("previous") or {})
    assignment = {"id": submission["assignment_id"], "title": submission.get("assignment_title"),
                  "class_offering_id": submission.get("class_offering_id"),
                  "offering_teacher_id": submission.get("offering_teacher_id")}
    is_group = bool(get_assignment_group_binding(conn, submission["assignment_id"]))
    close_window = "resubmission_allowed = 0, resubmission_due_at = NULL"

    if int(previous.get("is_absence_score") or 0):
        conn.execute(
            f"""
            UPDATE submissions
            SET status = 'unsubmitted', score = ?, feedback_md = ?, is_absence_score = 1,
                absence_scored_at = ?, absence_scored_by_teacher_id = ?, {close_window}
            WHERE id = ?
            """,
            (previous.get("score"), previous.get("feedback_md"), previous.get("absence_scored_at"),
             previous.get("absence_scored_by_teacher_id"), submission_id),
        )
        outcome, label = "restored_absence", "按缺交记分"
    elif not is_group and previous.get("score") is not None and str(previous.get("status") or "") == "graded":
        revision_id = int(previous["active_grade_revision_id"]) if previous.get("active_grade_revision_id") else None
        if revision_id:
            # At most one active revision per submission (unique index).
            conn.execute(
                "UPDATE submission_grade_revisions SET status = 'superseded', superseded_at = ? "
                "WHERE submission_id = ? AND status = 'active' AND id <> ?",
                (current.isoformat(), submission_id, revision_id),
            )
            conn.execute(
                "UPDATE submission_grade_revisions SET status = 'active', superseded_at = NULL, activated_at = ? "
                "WHERE id = ? AND submission_id = ?",
                (current.isoformat(), revision_id, submission_id),
            )
        conn.execute(
            f"""
            UPDATE submissions
            SET status = 'graded', score = ?, feedback_md = ?, active_grade_revision_id = ?,
                score_before_late_penalty = ?, late_penalty_points = ?, late_score_cap_applied = ?, {close_window}
            WHERE id = ?
            """,
            (previous.get("score"), previous.get("feedback_md"), revision_id, previous.get("score_before_late_penalty"),
             previous.get("late_penalty_points") or 0, previous.get("late_score_cap_applied") or 0, submission_id),
        )
        outcome, label = "restored_grade", f"恢复原成绩 {previous.get('score')}"
    else:
        conn.execute(f"UPDATE submissions SET status = 'submitted', {close_window} WHERE id = ?", (submission_id,))
        schedule_resume_grading(conn, submission_id, reason="resubmission window lapsed")
        needs_teacher = is_group or str(submission.get("grading_mode") or "").lower() not in {"ai", "auto", "mixed"}
        if needs_teacher and str(previous.get("status") or "") == "graded":
            outcome, label = "restored_needs_regrade", "原提交需要教师重新评分"
        else:
            outcome, label = "restored_pending_grading", "原提交重新进入批改"

    _delete_round_draft(conn, submission["assignment_id"], int(submission["student_pk_id"]))
    _notify(conn, submission, assignment, restored_label=label)
    return outcome


def handle_resubmission_expiry_task(task: Mapping[str, Any]) -> str:
    from ..database import get_db_connection
    from ..db.connection import begin_immediate_transaction

    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    with get_db_connection() as conn:
        begin_immediate_transaction(conn)
        outcome = restore_lapsed_resubmission(conn, payload)
        conn.commit()
    return outcome
