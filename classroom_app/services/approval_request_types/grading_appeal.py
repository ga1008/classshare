"""Appeal type 批改有误: the student believes the grade is wrong.

Lifecycle (plan §4):
  * create  — the student's own visible grade (not returned, not mid-grading,
    group grade revealed), within GRADING_APPEAL_WINDOW_DAYS of the grade and
    before the course's final grades are published; at most
    GRADING_APPEAL_LIMIT times per answer, and never again for a grade that
    was already upheld. The appealed questions are snapshotted.
  * the teacher either re-grades per question, or asks for an AI re-grade
    (strongest tier, with the previous grading and the appeal as context;
    the result is a *candidate* revision the teacher adopts or discards).
  * approve — writes the new grade through ``grade_submission_record`` in the
    decision transaction (late penalty, group settlement, notification and
    revision history come with it); ``outcome`` records whether it changed.
  * reject  — 维持原分, with the teacher's reply.
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
    APPEAL_GRADING,
    GRADING_APPEAL_LIMIT,
    SUBJECT_TYPE,
    appeal_dedupe_key,
    appeal_reviewers,
    appeal_subject_id,
    ensure_no_pending_appeal,
    grading_appeal_attempts,
    is_real_submission,
    load_appeal_context,
    parse_appeal_subject_id,
)
from ..assignment_lifecycle_service import _utc_like_now
from ..grading_question_service import current_grade_questions
from ..late_submission_policy import parse_iso_like_datetime

GRADING_APPEAL_WINDOW_DAYS = 7
EXPIRE_DAYS = 7
REMINDER_HOURS = 24
NOTE_MAX = 200


def _active_revision(conn, submission: Mapping[str, Any]) -> dict[str, Any] | None:
    revision_id = submission.get("active_grade_revision_id")
    if not revision_id:
        return None
    row = conn.execute("SELECT * FROM submission_grade_revisions WHERE id = ? LIMIT 1", (int(revision_id),)).fetchone()
    return dict(row) if row else None


def _grade_published(conn, class_offering_id: Any) -> bool:
    if not class_offering_id:
        return False
    try:
        row = conn.execute("SELECT 1 FROM grade_publications WHERE class_offering_id = ? AND status = 'active' LIMIT 1",
                           (int(class_offering_id),)).fetchone()
    except Exception:  # noqa: BLE001 - table absent on installs without grade publication
        return False
    return bool(row)


def _group_grade_hidden(conn, assignment_id: Any, student_pk_id: int) -> bool:
    try:
        from ..group_assignment_service import get_student_display_state

        state = get_student_display_state(conn, assignment_id, int(student_pk_id))
    except Exception:  # noqa: BLE001
        return False
    return bool(state and state.get("is_group") and not state.get("revealed"))


def _upheld_revision_ids(conn, assignment_id: Any, student_pk_id: int) -> set[int]:
    """Grade revisions a previous grading appeal already confirmed."""
    rows = conn.execute(
        "SELECT status, payload_json, decision_payload_json FROM approval_requests WHERE request_type = ? AND subject_id = ?",
        (APPEAL_GRADING, appeal_subject_id(assignment_id, student_pk_id)),
    ).fetchall()
    upheld: set[int] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}") or {}
            decision = json.loads(row["decision_payload_json"] or "{}") or {}
        except (TypeError, ValueError):
            continue
        confirmed = row["status"] == "rejected" or (row["status"] == "approved" and decision.get("outcome") == "upheld")
        if confirmed and payload.get("grade_revision_id"):
            upheld.add(int(payload["grade_revision_id"]))
    return upheld


def grading_appeal_availability(conn, *, assignment: Mapping[str, Any], student_pk_id: int,
                                submission: Mapping[str, Any] | None, pending: Any) -> tuple[bool, str, int]:
    """(available, reason, used) for the student entry and for ``prepare``."""
    assignment_id = assignment["id"]
    used = grading_appeal_attempts(conn, assignment_id, student_pk_id)
    if not is_real_submission(submission) or submission.get("score") is None or str(submission.get("status") or "") != "graded":
        return False, "出分后才能申请复核", used
    if int(submission.get("resubmission_allowed") or 0):
        return False, "答题已退回修改，重新提交后再看新成绩", used
    if _group_grade_hidden(conn, assignment_id, student_pk_id):
        return False, "小组成绩公布后才能申请复核", used
    if pending:
        return False, "已有一条申诉在处理中", used
    if used >= GRADING_APPEAL_LIMIT:
        return False, f"已达到 {GRADING_APPEAL_LIMIT} 次上限", used
    revision = _active_revision(conn, submission)
    if revision and int(revision["id"]) in _upheld_revision_ids(conn, assignment_id, student_pk_id):
        return False, "本次成绩已复核维持，如仍有疑问请直接联系教师", used
    graded_at = parse_iso_like_datetime((revision or {}).get("activated_at") or (revision or {}).get("created_at")
                                        or submission.get("submitted_at"))
    if graded_at is not None and datetime.now() - graded_at.replace(tzinfo=None) > timedelta(days=GRADING_APPEAL_WINDOW_DAYS):
        return False, f"已超过出分后 {GRADING_APPEAL_WINDOW_DAYS} 天的申诉期", used
    if _grade_published(conn, assignment.get("class_offering_id")):
        return False, "课程总评已发布，请直接联系教师", used
    return True, "", used


def prepare(conn, applicant: Mapping[str, Any], subject_id: str, reason: str, payload: dict[str, Any]) -> PreparedRequest:
    assignment_id, student_pk_id = parse_appeal_subject_id(subject_id)
    if student_pk_id != int(applicant.get("id") or 0):
        raise ApprovalWorkflowError(403, "只能为自己的答题申诉")
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    assignment, submission = context["assignment"], context["submission"]
    ensure_no_pending_appeal(conn, assignment_id, student_pk_id, (submission or {}).get("id"))
    available, why, used = grading_appeal_availability(conn, assignment=assignment, student_pk_id=student_pk_id,
                                                      submission=submission, pending=None)
    if not available:
        raise ApprovalWorkflowError(409 if used >= GRADING_APPEAL_LIMIT else 400, why)
    reviewers = appeal_reviewers(assignment)
    if not reviewers:
        raise ApprovalWorkflowError(422, "该作业没有可审批的教师，请联系管理员")
    grade = current_grade_questions(conn, submission)
    by_no = {int(item["question_no"]): item for item in grade["questions"]}
    selected: list[dict[str, Any]] = []
    for raw in payload.get("questions") or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            number = int(raw.get("question_no"))
        except (TypeError, ValueError):
            continue
        if by_no and number not in by_no:
            continue
        current = by_no.get(number, {})
        selected.append({
            "question_no": number,
            "question_id": current.get("question_id") or f"q{number}",
            "current_score": current.get("score"),
            "max_score": current.get("max_score"),
            "note": " ".join(str(raw.get("note") or "").split())[:NOTE_MAX],
        })
    if by_no and not selected:
        raise ApprovalWorkflowError(400, "请至少选择一道需要复核的题目")
    revision = _active_revision(conn, submission)
    student_name = context["student_name"] or applicant.get("name") or "学生"
    return PreparedRequest(
        title=f"{student_name} 申请复核成绩（批改有误）：{assignment.get('title') or ''}",
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
            "attempt_no": used + 1,
            "attempt_limit": GRADING_APPEAL_LIMIT,
            "grade_revision_id": int(revision["id"]) if revision else None,
            "score": submission.get("score"),
            "questions": selected,
            "question_source": grade["source"],
            "accept_score_may_decrease": True,
        },
        expires_at=(_utc_like_now() + timedelta(days=EXPIRE_DAYS)).isoformat(),
    )


def _grade_payload_from_candidate(conn, request: Mapping[str, Any], submission: Mapping[str, Any],
                                  candidate_revision_id: Any) -> dict[str, Any]:
    from ..grading_question_service import normalize_question_items, overview_from_feedback

    try:
        candidate_id = int(candidate_revision_id)
    except (TypeError, ValueError) as exc:
        raise ApprovalWorkflowError(400, "缺少要采纳的 AI 重改结果") from exc
    row = conn.execute(
        "SELECT * FROM submission_grade_revisions WHERE id = ? AND submission_id = ? AND status = 'candidate' LIMIT 1",
        (candidate_id, int(submission["id"])),
    ).fetchone()
    if not row:
        raise ApprovalWorkflowError(409, "AI 重改结果已失效，请刷新后重试")
    try:
        audit = json.loads(row["quality_audit_json"] or "{}") or {}
    except (TypeError, ValueError):
        audit = {}
    if int(audit.get("appeal_request_id") or 0) != int(request["id"]):
        raise ApprovalWorkflowError(409, "AI 重改结果不属于这条申诉")
    base = audit.get("base_revision_id")
    if base and submission.get("active_grade_revision_id") and int(base) != int(submission["active_grade_revision_id"]):
        raise ApprovalWorkflowError(409, "AI 重改之后成绩已被修改，该结果已过期；请重新 AI 重改或直接逐题改分")
    questions = normalize_question_items(audit.get("questions"))
    grade: dict[str, Any] = {"grade_source": "appeal_ai", "summary": overview_from_feedback(row["feedback_md"])}
    if questions and all(item["score"] is not None for item in questions):
        grade["questions"] = questions
        if row["score"] is not None and abs(sum(item["score"] for item in questions) - float(row["score"])) > 1e-6:
            grade["score_override"] = float(row["score"])
            grade["score_override_reason"] = "采用 AI 复核给出的总分"
    else:
        grade["score"] = row["score"]
        grade["feedback_md"] = row["feedback_md"]
    conn.execute("UPDATE submission_grade_revisions SET status = 'superseded', superseded_at = ? WHERE id = ?",
                 (datetime.now().isoformat(timespec="seconds"), int(row["id"])))
    return grade


def _discard_candidates(conn, request: Mapping[str, Any], *_args: Any) -> None:
    """A closed appeal leaves no AI suggestion behind as a pending candidate."""
    try:
        state = (json.loads(request.get("payload_json") or "{}") or {}).get("ai_regrade") or {}
    except (TypeError, ValueError):
        return
    token = str(state.get("token") or "")
    if not token:
        return
    conn.execute(
        "UPDATE submission_grade_revisions SET status = 'superseded', superseded_at = ? "
        "WHERE status = 'candidate' AND revision_hash LIKE ?",
        (datetime.now().isoformat(timespec="seconds"), f"appeal:appeal:{int(request['id'])}:%"),
    )


def on_approve(conn, request: Mapping[str, Any], reviewer: Mapping[str, Any], decision_payload: dict[str, Any], note: str) -> dict[str, Any]:
    from ..submission_grading_service import grade_submission_record

    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    submission = load_appeal_context(conn, assignment_id, student_pk_id)["submission"]
    if not is_real_submission(submission):
        raise ApprovalWorkflowError(404, "提交记录已不存在")
    mode = str(decision_payload.get("mode") or "")
    if mode == "manual":
        grade = dict(decision_payload.get("grade") or {})
        grade["grade_source"] = "appeal_manual"
    elif mode == "adopt_ai":
        grade = _grade_payload_from_candidate(conn, request, submission, decision_payload.get("candidate_revision_id"))
        for key in ("expected_review_revision", "expected_assignment_revision"):
            if decision_payload.get(key):
                grade[key] = decision_payload[key]
    else:
        raise ApprovalWorkflowError(400, "请在答题卡中改分或采纳 AI 重改结果后结案；维持原分请选择驳回并填写回复")
    if not note:
        raise ApprovalWorkflowError(400, "请填写给学生的复核回复")
    old_score = submission.get("score")
    try:
        result = grade_submission_record(
            conn, submission_id=int(submission["id"]), teacher_id=int(reviewer.get("id") or 0),
            data={**grade, "appeal_request_id": int(request["id"])}, actor_display_name=str(reviewer.get("name") or ""),
        )
    except ApprovalWorkflowError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface grading validation as a decision error
        if not hasattr(exc, "status_code"):
            raise
        raise ApprovalWorkflowError(int(exc.status_code), str(getattr(exc, "detail", exc))) from exc
    _discard_candidates(conn, request)
    new_score = result.get("score")
    changed = old_score is None or new_score is None or abs(float(new_score) - float(old_score)) > 1e-9
    return {
        "mode": mode,
        "outcome": "score_changed" if changed else "upheld",
        "old_score": old_score,
        "new_score": new_score,
        "grade_revision_id": result.get("grade_revision_id"),
        "notification_suffix": (f"成绩由 {old_score} 调整为 {new_score}。" if changed else f"复核后维持原成绩 {old_score}。"),
    }


def detail(conn, request: Mapping[str, Any], viewer: Mapping[str, Any]) -> dict[str, Any]:
    assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
    context = load_appeal_context(conn, assignment_id, student_pk_id)
    submission = context["submission"] or {}
    grade = current_grade_questions(conn, submission) if submission else {"questions": [], "source": "none"}
    try:
        payload = json.loads(request.get("payload_json") or "{}") or {}
    except (TypeError, ValueError):
        payload = {}
    # The AI suggestion is advice to the teacher; the student sees only the decision.
    is_student = str(viewer.get("role") or "") == "student"
    from ..grading_appeal_ai_service import effective_regrade_state

    regrade = {} if is_student else effective_regrade_state(payload.get("ai_regrade"))
    regrade.pop("token", None)
    candidate = None
    if regrade.get("candidate_revision_id"):
        row = conn.execute("SELECT id, status, score, feedback_md, quality_audit_json FROM submission_grade_revisions WHERE id = ?",
                           (int(regrade["candidate_revision_id"]),)).fetchone()
        if row:
            try:
                audit = json.loads(row["quality_audit_json"] or "{}") or {}
            except (TypeError, ValueError):
                audit = {}
            candidate = {"revision_id": int(row["id"]), "status": row["status"], "score": row["score"],
                         "questions": audit.get("questions") or [], "prior_issues": audit.get("prior_issues") or [],
                         "appeal_response": audit.get("appeal_response") or "", "changed": bool(audit.get("changed"))}
    submission_id = submission.get("id")
    return {
        "submission_id": submission_id,
        "student_name": context["student_name"] or request.get("applicant_name"),
        "assignment_title": context["assignment"].get("title") or "",
        "current_score": submission.get("score"),
        "current_questions": grade["questions"],
        "question_source": grade["source"],
        "appealed_questions": payload.get("questions") or [],
        "ai_regrade": regrade,
        "ai_candidate": candidate,
        "review_url": f"/submission/{submission_id}?appeal={int(request['id'])}" if submission_id else "",
        "assignment_url": f"/assignment/{assignment_id}",
    }


def inbox_link(request: Mapping[str, Any], role: str) -> str:
    if role == "teacher":
        try:
            submission_id = (json.loads(request.get("payload_json") or "{}") or {}).get("submission_id")
        except (TypeError, ValueError):
            submission_id = None
        if submission_id:
            return f"/submission/{int(submission_id)}?appeal={int(request['id'])}"
        return f"/assignment/{request.get('assignment_id')}?approval_request={int(request['id'])}"
    return f"/assignment/{request.get('assignment_id')}"


register_request_type(ApprovalRequestType(
    key=APPEAL_GRADING,
    label="批改有误",
    subject_type=SUBJECT_TYPE,
    applicant_roles=frozenset({"student"}),
    prepare=prepare,
    on_approve=on_approve,
    on_reject=_discard_candidates,
    on_cancel=_discard_candidates,
    on_expire=_discard_candidates,
    on_auto_cancel=_discard_candidates,
    detail=detail,
    inbox_link=inbox_link,
    reminder_hours=REMINDER_HOURS,
    expire_days=EXPIRE_DAYS,
    approve_label="改分并结案",
    reject_label="维持原分",
    description="学生认为批改不正确或有瑕疵，申请重新评分。教师可在答题卡中逐题改分，或请 AI 以更高档模型结合前次批改重新评分。",
))
