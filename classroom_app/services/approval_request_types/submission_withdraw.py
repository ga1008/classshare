"""Approval request type: a student asks to withdraw a graded homework and redo it.

Business rules
  * Only the submitting student may apply, only for a graded (score visible),
    non-absence submission of a *homework* (midterm/final are never withdrawable).
  * Reviewers are the offering teacher and the course creator; either may decide.
  * Approval reuses the teacher withdraw path (``submission_return_service``) so the
    grade revision history, group scores and the resubmission window are handled
    exactly like a manual "撤回提交". The window ends at the later of the teacher's
    explicit deadline and the assignment's own deadline (or ``now + 24h``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from ..approval_workflow_service import (
    ApprovalRequestType,
    ApprovalWorkflowError,
    PreparedRequest,
    register_request_type,
)
from ..assignment_lifecycle_service import _utc_like_now
from ..submission_return_service import (
    assignment_effective_deadline,
    resolve_resubmission_due_at,
    return_submissions_for_resubmission,
)

REQUEST_TYPE_KEY = "submission_withdraw"
NON_WITHDRAWABLE_KINDS = frozenset({"midterm", "final"})
REQUEST_TTL_DAYS = 7


def _load_submission_context(conn, submission_id: Any) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id, s.assignment_id, s.student_pk_id, s.student_name, s.status, s.score, s.submitted_at,
               s.is_absence_score, s.resubmission_allowed, s.resubmission_due_at,
               a.title AS assignment_title, a.class_offering_id, a.course_id, a.assessment_kind,
               a.due_at, a.late_submission_enabled, a.late_submission_until, a.status AS assignment_status,
               c.created_by_teacher_id, owner_t.name AS owner_teacher_name,
               o.teacher_id AS offering_teacher_id, offering_t.name AS offering_teacher_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN teachers owner_t ON owner_t.id = c.created_by_teacher_id
        LEFT JOIN teachers offering_t ON offering_t.id = o.teacher_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (int(submission_id),),
    ).fetchone()
    return dict(row) if row else None


def _reviewers_for(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    reviewers: list[dict[str, Any]] = []
    for id_key, name_key in (("offering_teacher_id", "offering_teacher_name"), ("created_by_teacher_id", "owner_teacher_name")):
        teacher_id = context.get(id_key)
        if teacher_id and all(int(item["id"]) != int(teacher_id) for item in reviewers):
            reviewers.append({"role": "teacher", "id": int(teacher_id), "name": context.get(name_key) or ""})
    return reviewers


def prepare(conn, applicant: Mapping[str, Any], subject_id: str, reason: str, payload: dict[str, Any]) -> PreparedRequest:
    try:
        submission_id = int(subject_id)
    except (TypeError, ValueError) as exc:
        raise ApprovalWorkflowError(400, "缺少提交记录") from exc
    context = _load_submission_context(conn, submission_id)
    if not context:
        raise ApprovalWorkflowError(404, "提交记录不存在")
    if int(context["student_pk_id"]) != int(applicant.get("id") or 0):
        raise ApprovalWorkflowError(403, "只能为自己的提交发起申请")
    if str(context.get("assessment_kind") or "").lower() in NON_WITHDRAWABLE_KINDS:
        raise ApprovalWorkflowError(400, "期中测验和期末考试不支持撤回重做，如有疑问请直接联系教师")
    if int(context.get("is_absence_score") or 0):
        raise ApprovalWorkflowError(400, "缺交记分的记录不能申请撤回，请直接提交作业")
    if int(context.get("resubmission_allowed") or 0):
        raise ApprovalWorkflowError(409, "该提交已被教师撤回并开放重交，请直接重新提交")
    if str(context.get("status") or "") != "graded":
        raise ApprovalWorkflowError(400, "只有已批改出分的作业才能申请撤回重做")
    reviewers = _reviewers_for(context)
    if not reviewers:
        raise ApprovalWorkflowError(422, "该作业没有可审批的教师，请联系管理员")
    expires_at = (_utc_like_now() + timedelta(days=REQUEST_TTL_DAYS)).isoformat()
    return PreparedRequest(
        title=f"{context.get('student_name') or applicant.get('name') or '学生'} 申请撤回重做：{context.get('assignment_title') or ''}",
        reviewers=reviewers,
        dedupe_key=f"{REQUEST_TYPE_KEY}:{submission_id}",
        subject_id=str(submission_id),
        assignment_id=str(context["assignment_id"]),
        class_offering_id=int(context["class_offering_id"]) if context.get("class_offering_id") else None,
        payload={
            "submission_id": submission_id,
            "assignment_title": context.get("assignment_title") or "",
            "score": context.get("score"),
            "submitted_at": context.get("submitted_at"),
        },
        expires_at=expires_at,
    )


def _assignment_row(conn, assignment_id: Any) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM assignments WHERE id = ? LIMIT 1", (assignment_id,)).fetchone()
    if not row:
        raise ApprovalWorkflowError(404, "作业不存在")
    return dict(row)


def on_approve(conn, request: Mapping[str, Any], reviewer: Mapping[str, Any], decision_payload: dict[str, Any], note: str) -> dict[str, Any]:
    submission = conn.execute("SELECT * FROM submissions WHERE id = ? LIMIT 1", (int(request["subject_id"]),)).fetchone()
    if not submission:
        raise ApprovalWorkflowError(404, "提交记录已不存在，无法撤回")
    submission = dict(submission)
    if int(submission.get("resubmission_allowed") or 0):
        raise ApprovalWorkflowError(409, "该提交已处于重交状态，无需再次撤回")
    if str(submission.get("status") or "") in {"grading", "grading_review"}:
        raise ApprovalWorkflowError(409, "该提交正在批改中，请稍后再处理")
    assignment = _assignment_row(conn, submission["assignment_id"])
    try:
        due_at = resolve_resubmission_due_at(
            assignment,
            explicit_due_at=decision_payload.get("resubmission_due_at"),
            extension_minutes=decision_payload.get("extension_minutes"),
        )
    except ValueError as exc:
        raise ApprovalWorkflowError(400, str(exc)) from exc
    reason = "学生申请撤回重做（教师已批准）" + (f"：{note}" if note else "")
    return_submissions_for_resubmission(
        conn,
        assignment=assignment,
        targets=[submission],
        teacher_id=int(reviewer.get("id") or 0),
        resubmission_due_at=due_at,
        reason=reason,
        begin_transaction=False,  # decide_request already holds the write transaction
    )
    return {"resubmission_due_at": due_at, "notification_suffix": f"请在 {due_at.replace('T', ' ')} 前重新提交。"}


def detail(conn, request: Mapping[str, Any], viewer: Mapping[str, Any]) -> dict[str, Any]:
    context = _load_submission_context(conn, request["subject_id"]) or {}
    assignment = _assignment_row(conn, request["assignment_id"]) if request.get("assignment_id") else {}
    deadline = assignment_effective_deadline(assignment)
    recommended = resolve_resubmission_due_at(assignment) if assignment else None
    return {
        "submission_id": int(request["subject_id"]),
        "student_name": context.get("student_name") or request.get("applicant_name"),
        "assignment_title": context.get("assignment_title") or assignment.get("title") or "",
        "assignment_status": context.get("assignment_status") or assignment.get("status"),
        "current_status": context.get("status"),
        "current_score": context.get("score"),
        "submitted_at": context.get("submitted_at"),
        "assignment_due_at": assignment.get("due_at"),
        "late_submission_until": assignment.get("late_submission_until") if assignment.get("late_submission_enabled") else None,
        "assignment_effective_deadline": deadline.isoformat() if deadline else None,
        "recommended_resubmission_due_at": recommended,
        "review_url": f"/submission/{int(request['subject_id'])}",
        "assignment_url": f"/assignment/{request.get('assignment_id')}",
        "already_returned": bool(int(context.get("resubmission_allowed") or 0)),
    }


def inbox_link(request: Mapping[str, Any], role: str) -> str:
    assignment_id = request.get("assignment_id") or ""
    if role == "teacher":
        return f"/assignment/{assignment_id}?approval_request={int(request['id'])}"
    return f"/assignment/{assignment_id}"


register_request_type(ApprovalRequestType(
    key=REQUEST_TYPE_KEY,
    label="作业撤回重做",
    subject_type="submission",
    applicant_roles=frozenset({"student"}),
    prepare=prepare,
    on_approve=on_approve,
    detail=detail,
    inbox_link=inbox_link,
    approve_label="同意撤回并开放重交",
    reject_label="拒绝",
    description="学生对平时作业分数有异议时，申请撤回本次提交并重新作答；教师批准后可设置重交截止时间。",
))
