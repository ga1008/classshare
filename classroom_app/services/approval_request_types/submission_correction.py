"""Appeal type 错误提交: the student handed in the wrong content or attachment.

Lifecycle (plan §5):
  * create  — allowed for any real submission (graded or not, any assessment
    kind), at most ``CORRECTION_LIMIT`` times per answer; in the same
    transaction an in-flight AI grading attempt is cancelled and grading of
    this answer is held while the appeal is pending.
  * approve — the submission is returned (status shown to the student as
    "未提交"), its answers and attachments are seeded into the next-round draft
    so the student edits rather than restarts, the late state of the original
    submission is inherited by the resubmission, and the window lapse is
    scheduled (the original stands if the student does not resubmit).
  * reject / cancel / expire / auto-cancel — the hold ends and AI grading
    resumes for an answer that has no score yet.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from ..approval_workflow_service import (
    ApprovalRequestType,
    ApprovalWorkflowError,
    PreparedRequest,
    record_request_event,
    register_request_type,
    update_request_payload,
)
from ..assessment_appeal_service import (
    APPEAL_CORRECTION,
    CORRECTION_LIMIT,
    SUBJECT_TYPE,
    appeal_dedupe_key,
    appeal_reviewers,
    appeal_subject_id,
    correction_attempts,
    ensure_no_pending_appeal,
    is_real_submission,
    load_appeal_context,
    parse_appeal_subject_id,
    schedule_resume_grading,
)
from ..assignment_lifecycle_service import _utc_like_now
from ..resubmission_expiry_service import schedule_resubmission_expiry, snapshot_before_return
from ..submission_draft_seed_service import seed_resubmission_draft
from ..submission_return_service import resolve_resubmission_due_at, return_submissions_for_resubmission

PROBLEM_KINDS = {"content": "答案内容有误", "attachment": "附件有误", "both": "内容和附件都有误"}
EXPIRE_HOURS = 72
REMINDER_HOURS = 12
LATE_MODES = {"inherit", "recompute"}


def _context_for_request(conn, request: Mapping[str, Any]) -> dict[str, Any]:
    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    return load_appeal_context(conn, assignment_id, student_pk_id)


def prepare(conn, applicant: Mapping[str, Any], subject_id: str, reason: str, payload: dict[str, Any]) -> PreparedRequest:
    assignment_id, student_pk_id = parse_appeal_subject_id(subject_id)
    if student_pk_id != int(applicant.get("id") or 0):
        raise ApprovalWorkflowError(403, "只能为自己的答题申诉")
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    assignment, submission = context["assignment"], context["submission"]
    if not is_real_submission(submission):
        raise ApprovalWorkflowError(400, "你还没有提交这份作业；如果已经截止，请选择“超时补交”")
    if int(submission.get("resubmission_allowed") or 0):
        raise ApprovalWorkflowError(409, "这份答题已退回，可以直接修改后重新提交")
    ensure_no_pending_appeal(conn, assignment_id, student_pk_id, submission["id"])
    attempts = correction_attempts(conn, assignment_id, student_pk_id, submission["id"])
    if attempts >= CORRECTION_LIMIT:
        raise ApprovalWorkflowError(409, f"同一份答题的“错误提交”申诉最多 {CORRECTION_LIMIT} 次，已用完")
    reviewers = appeal_reviewers(assignment)
    if not reviewers:
        raise ApprovalWorkflowError(422, "该作业没有可审批的教师，请联系管理员")
    problem_kind = str(payload.get("problem_kind") or "content").strip().lower()
    if problem_kind not in PROBLEM_KINDS:
        problem_kind = "content"
    student_name = context["student_name"] or applicant.get("name") or "学生"
    return PreparedRequest(
        title=f"{student_name} 申请退回修改（错误提交）：{assignment.get('title') or ''}",
        reviewers=reviewers,
        dedupe_key=appeal_dedupe_key(assignment_id, student_pk_id),
        subject_id=appeal_subject_id(assignment_id, student_pk_id),
        assignment_id=str(assignment_id),
        class_offering_id=int(assignment["class_offering_id"]) if assignment.get("class_offering_id") else None,
        payload={
            "submission_id": int(submission["id"]),
            "assignment_title": assignment.get("title") or "",
            "assessment_kind": assignment.get("assessment_kind") or "",
            "is_exam": bool(assignment.get("exam_paper_id")),
            "attempt_no": attempts + 1,
            "attempt_limit": CORRECTION_LIMIT,
            "submission_status_at_apply": submission.get("status"),
            "score_at_apply": submission.get("score"),
            "problem_kind": problem_kind,
            "problem_kind_label": PROBLEM_KINDS[problem_kind],
            "submitted_at": submission.get("submitted_at"),
        },
        expires_at=(_utc_like_now() + timedelta(hours=EXPIRE_HOURS)).isoformat(),
    )


def _interrupt_if_grading(conn, submission: Mapping[str, Any], *, reason: str) -> str | None:
    """Interrupt grading; a concurrent finish is fine, anything else is a 409."""
    from ..ai_grading_service import AIGradingQueueError, interrupt_submission_ai_grading_with_connection

    try:
        return interrupt_submission_ai_grading_with_connection(conn, dict(submission), reason=reason)
    except AIGradingQueueError as exc:
        current = conn.execute("SELECT status FROM submissions WHERE id = ? LIMIT 1", (int(submission["id"]),)).fetchone()
        if current and str(current["status"] or "") != "grading":
            return None  # the grading attempt finished meanwhile; nothing to stop
        raise ApprovalWorkflowError(409, "批改状态刚刚发生变化，请刷新后重试") from exc


def _lock_answer(conn, assignment_id: Any, student_pk_id: int) -> None:
    """Same lock order as grading writers and returns: group, then submission."""
    from ..group_assignment_service import lock_group_grading_for_students
    from ..submission_write_guard import lock_submission_writer

    lock_group_grading_for_students(conn, assignment_id, [int(student_pk_id)])
    lock_submission_writer(conn, assignment_id, int(student_pk_id))


def after_create(conn, request: Mapping[str, Any]) -> None:
    """Interrupt in-flight AI grading in the same transaction as the appeal."""
    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    _lock_answer(conn, assignment_id, student_pk_id)
    submission = load_appeal_context(conn, assignment_id, student_pk_id)["submission"]
    if not submission:
        return
    interrupted_job = submission.get("grading_job_id")
    resumed_status = _interrupt_if_grading(conn, submission, reason="interrupted_by_correction_appeal")
    if resumed_status is None:
        return
    update_request_payload(conn, int(request["id"]), {
        "grading_interrupted": True,
        "interrupted_job_id": interrupted_job,
        "status_after_interrupt": resumed_status,
    })
    record_request_event(conn, int(request["id"]), "grading_interrupted",
                         note="学生申请退回修改，已停止进行中的 AI 批改", payload={"job_id": interrupted_job})


def on_approve(conn, request: Mapping[str, Any], reviewer: Mapping[str, Any], decision_payload: dict[str, Any], note: str) -> dict[str, Any]:
    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    _lock_answer(conn, assignment_id, student_pk_id)
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    assignment, submission = context["assignment"], context["submission"]
    if not is_real_submission(submission):
        raise ApprovalWorkflowError(409, "提交记录已不存在（学生可能已撤回），无法退回；可直接驳回此申请")
    if int(submission.get("resubmission_allowed") or 0):
        raise ApprovalWorkflowError(409, "该提交已处于退回状态，无需再次退回")
    if str(submission.get("status") or "") == "grading":
        # A teacher-forced grading attempt started meanwhile; it is moot now.
        _interrupt_if_grading(conn, submission, reason="superseded_by_correction_return")
        submission = load_appeal_context(conn, assignment["id"], submission["student_pk_id"])["submission"]
    try:
        due_at = resolve_resubmission_due_at(
            assignment,
            explicit_due_at=decision_payload.get("resubmission_due_at"),
            extension_minutes=decision_payload.get("extension_minutes"),
        )
    except ValueError as exc:
        raise ApprovalWorkflowError(400, str(exc)) from exc
    late_mode = str(decision_payload.get("late_mode") or "inherit").strip().lower()
    if late_mode not in LATE_MODES:
        late_mode = "inherit"
    prefill = decision_payload.get("prefill_draft", True) not in (False, "0", 0, "false")
    previous = snapshot_before_return(submission)
    return_submissions_for_resubmission(
        conn,
        assignment=assignment,
        targets=[submission],
        teacher_id=int(reviewer.get("id") or 0),
        resubmission_due_at=due_at,
        reason="学生申请退回修改（错误提交，教师已同意）" + (f"：{note}" if note else ""),
        begin_transaction=False,  # decide_request already holds the write transaction
    )
    returned = load_appeal_context(conn, assignment["id"], submission["student_pk_id"])["submission"]
    seeded = {"answers": False, "files": 0, "missing_files": 0}
    if prefill:
        seeded = seed_resubmission_draft(conn, assignment=assignment, submission=returned)
    schedule_resubmission_expiry(conn, submission_id=int(submission["id"]), due_at=due_at,
                                 previous=previous, origin=APPEAL_CORRECTION)
    return {
        "resubmission_due_at": due_at,
        "returned_at": returned.get("returned_at"),
        "late_mode": late_mode,
        "prefill_draft": prefill,
        "seeded_files": seeded.get("files", 0),
        "missing_files": seeded.get("missing_files", 0),
        "notification_suffix": (
            f"原答案已为你保留，请在 {due_at.replace('T', ' ')[:16]} 前修改并重新提交；逾期将以原提交为准。"
        ),
    }


def _release_hold(conn, request: Mapping[str, Any], reason: str) -> None:
    context = _context_for_request(conn, request)
    submission = context["submission"]
    if not submission or int(submission.get("resubmission_allowed") or 0):
        return
    if submission.get("score") is None and str(submission.get("status") or "") == "submitted":
        schedule_resume_grading(conn, int(submission["id"]), reason=reason)


def on_reject(conn, request: Mapping[str, Any], reviewer: Mapping[str, Any], note: str) -> None:
    _release_hold(conn, request, "correction appeal rejected")


def on_cancel(conn, request: Mapping[str, Any], applicant: Mapping[str, Any], note: str) -> None:
    _release_hold(conn, request, "correction appeal cancelled")


def on_expire(conn, request: Mapping[str, Any]) -> None:
    _release_hold(conn, request, "correction appeal expired")


def on_auto_cancel(conn, request: Mapping[str, Any], note: str) -> None:
    _release_hold(conn, request, "correction appeal auto-cancelled")


def detail(conn, request: Mapping[str, Any], viewer: Mapping[str, Any]) -> dict[str, Any]:
    context = _context_for_request(conn, request)
    assignment, submission = context["assignment"], context["submission"] or {}
    recommended = resolve_resubmission_due_at(assignment)
    submission_id = submission.get("id")
    return {
        "submission_id": submission_id,
        "student_name": context["student_name"] or request.get("applicant_name"),
        "assignment_title": assignment.get("title") or "",
        "current_status": submission.get("status"),
        "current_score": submission.get("score"),
        "submitted_at": submission.get("submitted_at"),
        "recommended_resubmission_due_at": recommended,
        "review_url": f"/submission/{submission_id}" if submission_id else "",
        "appeal_url": f"/submission/{submission_id}?appeal={int(request['id'])}" if submission_id else "",
        "assignment_url": f"/assignment/{assignment.get('id')}",
        "already_returned": bool(int(submission.get("resubmission_allowed") or 0)),
    }


def inbox_link(request: Mapping[str, Any], role: str) -> str:
    if role == "teacher":
        # The assignment page auto-opens the decision dialog with the student's
        # original answer card embedded — one click from the notification.
        return f"/assignment/{request.get('assignment_id')}?approval_request={int(request['id'])}"
    return f"/assignment/{request.get('assignment_id')}"


register_request_type(ApprovalRequestType(
    key=APPEAL_CORRECTION,
    label="错误提交",
    subject_type=SUBJECT_TYPE,
    applicant_roles=frozenset({"student"}),
    prepare=prepare,
    on_approve=on_approve,
    on_reject=on_reject,
    on_cancel=on_cancel,
    detail=detail,
    inbox_link=inbox_link,
    after_create=after_create,
    on_expire=on_expire,
    on_auto_cancel=on_auto_cancel,
    reminder_hours=REMINDER_HOURS,
    expire_hours=EXPIRE_HOURS,
    approve_label="同意退回修改",
    reject_label="驳回",
    description="学生发现提交的内容或附件有误，申请退回修改；申请后进行中的批改会立即暂停，教师同意后学生可在原答案基础上修改并重新提交。",
))
