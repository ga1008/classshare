"""Appeal type 超时补交: the student missed the deadline and asks to submit late.

Lifecycle (plan §6):
  * create  — only when the assignment no longer accepts this student's work by
    time, the student has no real submission (nothing, or an absence
    placeholder) and no open personal window; at most ``LATE_APPROVED_LIMIT``
    approvals and ``LATE_TOTAL_LIMIT`` applications per assignment.
  * approve — opens a personal window (``assignment_student_window_service``)
    with a deadline set as a time or a duration, and a penalty rule: the
    assignment's own rule (lateness keeps counting from the original
    deadline), a new rule (fixed, or every n days/hours/minutes/seconds with
    optional caps), or none. The window bypasses the assignment's closed
    state; reminder and expiry are scheduled.
  * reject / cancel / expire — nothing changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping

from ..approval_workflow_service import (
    ApprovalRequestType,
    ApprovalWorkflowError,
    PreparedRequest,
    register_request_type,
)
from ..assessment_appeal_service import (
    APPEAL_LATE,
    LATE_APPROVED_LIMIT,
    LATE_TOTAL_LIMIT,
    SUBJECT_TYPE,
    appeal_dedupe_key,
    appeal_reviewers,
    appeal_subject_id,
    ensure_no_pending_appeal,
    is_real_submission,
    late_attempts,
    load_appeal_context,
    parse_appeal_subject_id,
)
from ..assignment_lifecycle_service import _utc_like_now, assignment_accepts_submissions
from ..assignment_student_window_service import (
    PENALTY_INHERIT,
    PENALTY_MODES,
    active_window,
    assignment_penalty_policy,
    count_windows,
    describe_penalty_policy,
    open_window,
    resolve_window_policy,
)
from ..late_submission_policy import dt_to_iso, parse_iso_like_datetime

EXPIRE_DAYS = 3
REMINDER_HOURS = 24
DURATION_UNITS = {"minute": 60, "hour": 3600, "day": 86400}
MAX_WINDOW = timedelta(days=30)
EXPECTED_MINUTES_MAX = 60 * 24 * 14


def late_availability(conn, *, assignment: Mapping[str, Any], student_pk_id: int,
                      submission: Mapping[str, Any] | None, pending: Any) -> tuple[bool, str, int, int]:
    """(available, reason, approved, total) for the student entry and ``prepare``."""
    from ..learning_progress_service import student_can_access_assignment

    approved, total = late_attempts(conn, assignment["id"], student_pk_id)
    if not student_can_access_assignment(conn, assignment["id"], int(student_pk_id)):
        return False, "你不在这份作业的布置范围内", approved, total
    if str(assignment.get("status") or "") not in {"published", "closed"}:
        return False, "作业尚未发布", approved, total
    if is_real_submission(submission):
        return False, "已经提交过，如提交有误请选择“错误提交”", approved, total
    if active_window(conn, assignment["id"], student_pk_id):
        return False, "补交通道已开启，请直接作答提交", approved, total
    if assignment_accepts_submissions(assignment):
        return False, "作业仍在接收提交，直接提交即可", approved, total
    if pending:
        return False, "已有一条申诉在处理中", approved, total
    if approved >= LATE_APPROVED_LIMIT:
        return False, f"补交已获批 {LATE_APPROVED_LIMIT} 次，不能再申请", approved, total
    if total >= LATE_TOTAL_LIMIT:
        return False, f"补交申请已达 {LATE_TOTAL_LIMIT} 次上限", approved, total
    return True, "", approved, total


def _effective_deadline(assignment: Mapping[str, Any]) -> str | None:
    if int(assignment.get("late_submission_enabled") or 0) and assignment.get("late_submission_until"):
        return assignment.get("late_submission_until")
    return assignment.get("due_at")


def prepare(conn, applicant: Mapping[str, Any], subject_id: str, reason: str, payload: dict[str, Any]) -> PreparedRequest:
    assignment_id, student_pk_id = parse_appeal_subject_id(subject_id)
    if student_pk_id != int(applicant.get("id") or 0):
        raise ApprovalWorkflowError(403, "只能为自己的作业申请补交")
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    assignment, submission = context["assignment"], context["submission"]
    ensure_no_pending_appeal(conn, assignment_id, student_pk_id, (submission or {}).get("id"))
    available, why, approved, total = late_availability(conn, assignment=assignment, student_pk_id=student_pk_id,
                                                        submission=submission, pending=None)
    if not available:
        if why.startswith("你不在"):
            raise ApprovalWorkflowError(403, why)
        raise ApprovalWorkflowError(409 if approved >= LATE_APPROVED_LIMIT or total >= LATE_TOTAL_LIMIT else 400, why)
    reviewers = appeal_reviewers(assignment)
    if not reviewers:
        raise ApprovalWorkflowError(422, "该作业没有可审批的教师，请联系管理员")
    deadline = _effective_deadline(assignment)
    deadline_dt = parse_iso_like_datetime(deadline)
    missed_by = max(0, int((_utc_like_now() - deadline_dt.replace(tzinfo=None)).total_seconds())) if deadline_dt else None
    try:
        expected = int(payload.get("expected_minutes") or 0) or None
    except (TypeError, ValueError):
        expected = None
    if expected is not None and not 1 <= expected <= EXPECTED_MINUTES_MAX:
        expected = None
    student_name = context["student_name"] or applicant.get("name") or "学生"
    return PreparedRequest(
        title=f"{student_name} 申请超时补交：{assignment.get('title') or ''}",
        reviewers=reviewers,
        dedupe_key=appeal_dedupe_key(assignment_id, student_pk_id),
        subject_id=appeal_subject_id(assignment_id, student_pk_id),
        assignment_id=str(assignment_id),
        class_offering_id=int(assignment["class_offering_id"]) if assignment.get("class_offering_id") else None,
        payload={
            "submission_id": (submission or {}).get("id"),
            "assignment_title": assignment.get("title") or "",
            "assessment_kind": assignment.get("assessment_kind") or "",
            "is_exam": bool(assignment.get("exam_paper_id")),
            "attempt_no": total + 1,
            "attempt_limit": LATE_TOTAL_LIMIT,
            "previous_grants": approved,
            "grant_limit": LATE_APPROVED_LIMIT,
            "effective_deadline": deadline,
            "missed_by_seconds": missed_by,
            "expected_minutes": expected,
            "had_absence_score": bool(submission and int(submission.get("is_absence_score") or 0)),
        },
        expires_at=(_utc_like_now() + timedelta(days=EXPIRE_DAYS)).isoformat(),
    )


def _default_window_seconds(assignment: Mapping[str, Any]) -> int:
    if assignment.get("exam_paper_id"):
        minutes = int(assignment.get("duration_minutes") or 0)
        return (minutes or 120) * 60
    return 24 * 3600


def _window_due(decision: Mapping[str, Any], assignment: Mapping[str, Any]) -> tuple[datetime, int | None]:
    now = datetime.now().replace(microsecond=0)
    if str(decision.get("window_mode") or "duration") == "until":
        due = parse_iso_like_datetime(decision.get("due_at"))
        if due is None:
            raise ApprovalWorkflowError(400, "请填写补交截止时间")
        due = due.replace(tzinfo=None)
        duration = None
    else:
        unit = str(decision.get("duration_unit") or "hour")
        if unit not in DURATION_UNITS:
            raise ApprovalWorkflowError(400, "补交时长单位只能是分钟、小时或天")
        try:
            value = float(decision.get("duration_value")) if decision.get("duration_value") not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ApprovalWorkflowError(400, "补交时长必须是数字") from exc
        duration = int(value * DURATION_UNITS[unit]) if value else _default_window_seconds(assignment)
        if duration < 60:
            raise ApprovalWorkflowError(400, "补交时长至少 1 分钟")
        due = now + timedelta(seconds=duration)
    if due <= now:
        raise ApprovalWorkflowError(400, "补交截止时间必须晚于现在")
    if due - now > MAX_WINDOW:
        raise ApprovalWorkflowError(400, "补交通道最长 30 天")
    return due, duration


def on_approve(conn, request: Mapping[str, Any], reviewer: Mapping[str, Any], decision_payload: dict[str, Any], note: str) -> dict[str, Any]:
    from ..submission_write_guard import lock_submission_writer

    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    lock_submission_writer(conn, assignment_id, int(student_pk_id))
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    assignment, submission = context["assignment"], context["submission"]
    from ..learning_progress_service import student_can_access_assignment

    if is_real_submission(submission):
        raise ApprovalWorkflowError(409, "该生已经提交了作业，无需再开补交通道")
    if not student_can_access_assignment(conn, assignment_id, int(student_pk_id)):
        raise ApprovalWorkflowError(403, "该生不在这份作业的布置范围内")
    if str(assignment.get("status") or "") not in {"published", "closed"}:
        raise ApprovalWorkflowError(409, "作业当前不是已发布状态，不能开补交通道")
    if assignment_accepts_submissions(assignment):
        raise ApprovalWorkflowError(409, "作业现在已重新接收提交，学生可直接提交，无需补交通道")
    # This request is already marked approved here, so count real grants instead.
    if count_windows(conn, assignment_id, student_pk_id) >= LATE_APPROVED_LIMIT:
        raise ApprovalWorkflowError(409, f"该生本作业的补交已获批 {LATE_APPROVED_LIMIT} 次")
    due, duration = _window_due(decision_payload, assignment)
    mode = str(decision_payload.get("penalty_mode") or PENALTY_INHERIT)
    if mode not in PENALTY_MODES:
        raise ApprovalWorkflowError(400, "扣分规则选择无效")
    try:
        policy = resolve_window_policy(assignment, mode, decision_payload.get("penalty_policy") or {})
        window = open_window(conn, assignment=assignment, student_pk_id=int(student_pk_id), due_at=due,
                             penalty_mode=mode, policy=policy, source_request_id=int(request["id"]),
                             teacher_id=int(reviewer.get("id") or 0) or None, duration_seconds=duration)
    except ValueError as exc:
        raise ApprovalWorkflowError(400, str(exc)) from exc
    label = describe_penalty_policy(policy)
    due_text = dt_to_iso(due).replace("T", " ")[:16]
    return {
        "window_id": int(window["id"]),
        "grant_no": int(window["grant_no"]),
        "due_at": dt_to_iso(due),
        "penalty_mode": mode,
        "penalty_policy": policy,
        "penalty_label": label,
        "notification_suffix": f"补交通道已开启，请在 {due_text} 前完成提交（扣分规则：{label}）。",
    }


def _risk_hints(conn, assignment: Mapping[str, Any], submission: Mapping[str, Any] | None) -> list[str]:
    from .grading_appeal import _grade_published

    hints: list[str] = []
    if submission and int(submission.get("is_absence_score") or 0):
        hints.append("该生当前已被记为缺交（结课记分），补交后将按实际提交重新计分。")
    graded = conn.execute("SELECT COUNT(*) AS n FROM submissions WHERE assignment_id = ? AND status = 'graded' "
                          "AND COALESCE(is_absence_score, 0) = 0", (str(assignment["id"]),)).fetchone()
    if int(graded["n"] or 0):
        weight = "（期中/期末请特别注意泄题风险）" if assignment.get("assessment_kind") in {"midterm", "final"} else ""
        hints.append(f"已有 {int(graded['n'])} 位同学的成绩与评语可见，可能存在答案外泄风险{weight}。")
    if _grade_published(conn, assignment.get("class_offering_id")):
        hints.append("课程总评已发布，补交成绩需要重新生成成绩材料。")
    try:
        from ..group_assignment_service import is_group_assignment

        if is_group_assignment(conn, assignment["id"]):
            hints.append("这是小组作业，该生补交后小组成绩按既有规则重新结算。")
    except Exception:  # noqa: BLE001 - a hint must never break the review page
        pass
    return hints


def detail(conn, request: Mapping[str, Any], viewer: Mapping[str, Any]) -> dict[str, Any]:
    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    assignment, submission = context["assignment"], context["submission"]
    inherited = assignment_penalty_policy(assignment)
    try:
        decision = json.loads(request.get("decision_payload_json") or "{}") or {}
    except (TypeError, ValueError):
        decision = {}
    return {
        "student_name": context["student_name"] or request.get("applicant_name"),
        "assignment_title": assignment.get("title") or "",
        "is_exam": bool(assignment.get("exam_paper_id")),
        "assignment_due_at": assignment.get("due_at"),
        "late_submission_until": assignment.get("late_submission_until") if int(assignment.get("late_submission_enabled") or 0) else None,
        "default_window_minutes": _default_window_seconds(assignment) // 60,
        "assignment_policy": inherited,
        "assignment_policy_label": describe_penalty_policy(inherited) if inherited else "",
        "risk_hints": _risk_hints(conn, assignment, submission),
        "submission_id": (submission or {}).get("id") if is_real_submission(submission) else None,
        "window": decision if decision.get("window_id") else None,
        "assignment_url": f"/assignment/{assignment_id}",
    }


def inbox_link(request: Mapping[str, Any], role: str) -> str:
    if role == "teacher":
        return f"/assignment/{request.get('assignment_id')}?approval_request={int(request['id'])}"
    return f"/assignment/{request.get('assignment_id')}"


register_request_type(ApprovalRequestType(
    key=APPEAL_LATE,
    label="超时补交",
    subject_type=SUBJECT_TYPE,
    applicant_roles=frozenset({"student"}),
    prepare=prepare,
    on_approve=on_approve,
    detail=detail,
    inbox_link=inbox_link,
    reminder_hours=REMINDER_HOURS,
    expire_days=EXPIRE_DAYS,
    approve_label="开启补交通道",
    reject_label="驳回",
    description="学生错过提交时间，申请单独补交。教师同意后为该生开启个人补交通道（截止时间或时长），并选择沿用原扣分规则、设置新规则或不扣分。",
))
