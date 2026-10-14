"""AI re-grade for a 批改有误 appeal (plan §4.3).

The teacher asks; the AI re-grades with the strongest authoritative tier
(``source_feature=assessment_appeal``), seeing the previous grading and the
student's appeal as fenced context. The submission is never touched while
this runs: the student keeps seeing the current grade. The result lands as a
*candidate* grade revision that the teacher adopts (through the appeal
decision) or ignores.

Concurrency: the re-grade state lives in ``approval_requests.payload_json``
and every transition is a compare-and-set on that column (plus
``status='pending'``), so double clicks, two reviewers, duplicate callbacks
and a callback racing the teacher's decision all resolve to one winner. A
``queued`` state older than ``STALE_QUEUED_MINUTES`` counts as failed so a
lost job never blocks the appeal.

Limits: two AI re-grades per appeal, plus per-offering and global daily caps
counted from the appeal event log.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Mapping

from .approval_workflow_service import (
    ApprovalWorkflowError,
    _load_request_row,
    get_request,
    record_request_event,
)
from .assessment_appeal_service import APPEAL_GRADING, parse_appeal_subject_id

APPEAL_SOURCE_FEATURE = "assessment_appeal"
MAX_REGRADES_PER_APPEAL = 2
STALE_QUEUED_MINUTES = 20


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name) or default))
    except ValueError:
        return default


OFFERING_DAILY_LIMIT = _env_int("AI_APPEAL_REGRADE_OFFERING_DAILY_LIMIT", 20)
GLOBAL_DAILY_LIMIT = _env_int("AI_APPEAL_REGRADE_GLOBAL_DAILY_LIMIT", 100)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _payload(request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(request.get("payload_json") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def effective_regrade_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """The re-grade state as reviewers should see it (a lost job reads as failed)."""
    current = dict(state or {})
    if current.get("status") != "queued":
        return current
    try:
        requested = datetime.fromisoformat(str(current.get("requested_at") or ""))
    except ValueError:
        return current
    if datetime.now() - requested.replace(tzinfo=None) > timedelta(minutes=STALE_QUEUED_MINUTES):
        return {**current, "status": "failed", "error": "AI 长时间未返回结果，可重新发起"}
    return current


def _compare_and_set_state(conn, request: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    """Write ``ai_regrade`` only if the request is still pending and unchanged since read."""
    payload = {**_payload(request), "ai_regrade": dict(state)}
    cursor = conn.execute(
        "UPDATE approval_requests SET payload_json = ?, updated_at = ? "
        "WHERE id = ? AND status = 'pending' AND payload_json = ?",
        (json.dumps(payload, ensure_ascii=False), _now(), int(request["id"]), request.get("payload_json") or ""),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def _today_regrades(conn, class_offering_id: Any) -> tuple[int, int]:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM approval_request_events WHERE event_type = 'ai_regrade_requested' AND created_at >= ?",
        (start,),
    ).fetchone()
    offering = conn.execute(
        """
        SELECT COUNT(*) AS n FROM approval_request_events e JOIN approval_requests r ON r.id = e.request_id
        WHERE e.event_type = 'ai_regrade_requested' AND e.created_at >= ? AND r.class_offering_id = ?
        """,
        (start, int(class_offering_id or 0)),
    ).fetchone()
    return int(offering["n"] or 0), int(total["n"] or 0)


def _appeal_context(conn, request: Mapping[str, Any], submission: Mapping[str, Any]) -> dict[str, Any]:
    from .grading_question_service import current_grade_questions

    grade = current_grade_questions(conn, submission)
    return {
        "reason": request.get("reason") or "",
        "questions": _payload(request).get("questions") or [],
        "previous_score": submission.get("score"),
        "previous_summary": grade.get("summary") or "",
        "previous_questions": grade.get("questions") or [],
    }


def _check_can_request(conn, teacher: Mapping[str, Any], request_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    item = get_request(conn, teacher, request_id)
    if item.get("request_type") != APPEAL_GRADING:
        raise ApprovalWorkflowError(400, "只有“批改有误”申诉可以使用 AI 重改")
    if item.get("status") != "pending":
        raise ApprovalWorkflowError(409, "申诉已处理，不能再发起 AI 重改")
    if not item.get("can_decide"):
        raise ApprovalWorkflowError(403, "你不是该申诉的审批人")
    request = _load_request_row(conn, request_id)
    previous = effective_regrade_state(_payload(request).get("ai_regrade"))
    if previous.get("status") == "queued":
        raise ApprovalWorkflowError(409, "AI 正在重改，请稍候")
    if int(previous.get("attempt") or 0) >= MAX_REGRADES_PER_APPEAL:
        raise ApprovalWorkflowError(409, f"每条申诉最多 AI 重改 {MAX_REGRADES_PER_APPEAL} 次")
    offering_count, global_count = _today_regrades(conn, request.get("class_offering_id"))
    if offering_count >= OFFERING_DAILY_LIMIT or global_count >= GLOBAL_DAILY_LIMIT:
        raise ApprovalWorkflowError(429, "今日 AI 重改次数已达上限，请人工复核或明天再试")
    return request, previous


def _load_graded_submission(conn, request: Mapping[str, Any], teacher_id: int) -> dict[str, Any]:
    from . import ai_grading_service as grading

    submission_id = _payload(request).get("submission_id")
    if not submission_id:
        assignment_id, student_pk_id = parse_appeal_subject_id(request["subject_id"])
        row = conn.execute("SELECT id FROM submissions WHERE assignment_id = ? AND student_pk_id = ? LIMIT 1",
                           (assignment_id, student_pk_id)).fetchone()
        submission_id = row["id"] if row else None
    if not submission_id:
        raise ApprovalWorkflowError(404, "提交记录不存在")
    try:
        submission = grading._load_submission_for_grading(conn, int(submission_id), teacher_id=teacher_id)
    except grading.AIGradingQueueError as exc:
        raise ApprovalWorkflowError(int(exc.status_code), str(exc.detail)) from exc
    if str(submission.get("status") or "") != "graded":
        raise ApprovalWorkflowError(409, "答题当前不是已出分状态，无法 AI 重改")
    return submission


def _build_job(conn, request: Mapping[str, Any], submission: dict[str, Any], token: str) -> dict[str, Any]:
    from . import ai_grading_service as grading

    try:
        files = grading._load_submission_files_for_grading(conn, int(submission["id"]))
        rubric_md = grading._resolve_grading_rubric(submission)
        resolved_files, has_files, has_answers = grading._prepare_grading_inputs(submission, files)
    except grading.AIGradingQueueError as exc:
        raise ApprovalWorkflowError(int(exc.status_code), str(exc.detail)) from exc
    job_data = grading.build_grading_job_data(
        submission=submission, current=submission, rubric_md=rubric_md, resolved_files=resolved_files,
        has_files=has_files, has_answers=has_answers,
        student_profile_context=None,  # fairness: no hidden profile in an appeal re-grade
        attempt_token=token,
        content_fingerprint=grading.build_submission_grading_fingerprint(submission, files, rubric_md=rubric_md),
    )
    job_data["business_context"]["source_feature"] = APPEAL_SOURCE_FEATURE
    job_data["appeal_request_id"] = int(request["id"])
    job_data["appeal_context"] = _appeal_context(conn, request, submission)
    return job_data


async def request_appeal_ai_regrade(request_id: int, teacher: Mapping[str, Any]) -> dict[str, Any]:
    from ..config import AI_DURABLE_JOBS_ENABLED, AI_JOB_POLICY_VERSION
    from ..database import get_db_connection
    from .ai_durable_job_service import create_ai_job

    teacher_id = int(teacher.get("id") or 0)
    with get_db_connection() as conn:
        request, previous = _check_can_request(conn, teacher, request_id)
        submission = _load_graded_submission(conn, request, teacher_id)
        attempt = int(previous.get("attempt") or 0) + 1
        token = f"appeal:{int(request_id)}:{attempt}:{uuid.uuid4().hex}"
        job_data = _build_job(conn, request, submission, token)
        state = {"status": "queued", "attempt": attempt, "token": token, "job_id": None,
                 "requested_at": _now(), "requested_by": teacher_id,
                 # Adoption is refused once the visible grade moved past this revision.
                 "base_revision_id": submission.get("active_grade_revision_id")}
        if not _compare_and_set_state(conn, request, state):
            conn.rollback()
            raise ApprovalWorkflowError(409, "申诉状态已变化（可能已有人发起 AI 重改），请刷新后重试")
        if AI_DURABLE_JOBS_ENABLED:
            job_row, _created = create_ai_job(
                conn, task_type="ai_grading", dedupe_key=f"ai-appeal-regrade:{token}",
                payload=job_data, scope_type="class_offering", scope_id=str(submission.get("class_offering_id") or ""),
                owner_role="teacher", owner_user_pk=teacher_id, source_ref=f"appeal:{int(request_id)}",
                policy_version=AI_JOB_POLICY_VERSION,
            )
            state["job_id"] = int(job_row["id"])
            _compare_and_set_state(conn, _load_request_row(conn, request_id), state)
        record_request_event(conn, int(request_id), "ai_regrade_requested", actor=teacher,
                             note=f"第 {attempt} 次 AI 重改（高档模型）", payload={"job_id": state["job_id"]})
        conn.commit()

    if not AI_DURABLE_JOBS_ENABLED:
        await _dispatch_to_gateway(int(request_id), job_data, submission, teacher_id, state, previous)
    return {"status": "queued", "attempt": attempt, "job_id": state["job_id"]}


async def _dispatch_to_gateway(request_id: int, job_data: dict[str, Any], submission: Mapping[str, Any],
                               teacher_id: int, state: dict[str, Any], previous: Mapping[str, Any]) -> None:
    from ..core import ai_client
    from ..database import get_db_connection
    from .ai_gateway_service import ai_gateway_post

    try:
        response = await ai_gateway_post(
            ai_client, "/api/ai/submit-grading-job", json_payload=job_data,
            task_type="assignment_grading", priority="P0",
            class_offering_id=submission.get("class_offering_id"), student_id=submission.get("student_pk_id"),
            teacher_id=teacher_id, source_ref=f"appeal:{request_id}",
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any dispatch failure must release the queued state
        with get_db_connection() as conn:
            request = _load_request_row(conn, request_id)
            current = _payload(request).get("ai_regrade") or {}
            if current.get("token") == state["token"]:
                # Not dispatched: the attempt is given back so the teacher can retry.
                _compare_and_set_state(conn, request, {
                    **state, "status": "failed", "attempt": int(previous.get("attempt") or 0),
                    "finished_at": _now(), "error": "AI 服务暂不可用，未能发起重改"})
                record_request_event(conn, request_id, "ai_regrade_failed", note="AI 服务暂不可用，未能发起重改")
            conn.commit()
        raise ApprovalWorkflowError(503, "AI 服务暂不可用，请稍后再试或人工复核") from exc


def handle_appeal_regrade_callback(conn, data: Mapping[str, Any]) -> dict[str, Any]:
    """Store an AI re-grade result as a candidate revision (caller commits)."""
    from .grading_feedback_service import normalize_grading_result

    request_id = int(data.get("appeal_request_id") or 0)
    row = conn.execute("SELECT * FROM approval_requests WHERE id = ? LIMIT 1", (request_id,)).fetchone()
    if not row:
        return {"status": "ignored_missing_appeal"}
    request = dict(row)
    state = _payload(request).get("ai_regrade") or {}
    # Only the attempt currently queued may land; retries of a delivered
    # callback and results for superseded attempts are ignored.
    if (request["status"] != "pending" or state.get("status") != "queued"
            or not state.get("token") or state.get("token") != data.get("appeal_regrade_token")):
        return {"status": "ignored_stale_appeal_regrade"}
    normalized = normalize_grading_result(dict(data)) if data.get("status") == "graded" else {}
    if data.get("status") != "graded" or normalized.get("score") is None:
        failed = {**state, "status": "failed", "finished_at": _now(), "error": "AI 未能给出可用结果"}
        if not _compare_and_set_state(conn, request, failed):
            return {"status": "ignored_stale_appeal_regrade"}
        record_request_event(conn, request_id, "ai_regrade_failed", note="AI 重改未能给出可用结果，原成绩保持不变")
        _notify_teacher(conn, request, title="AI 重改失败", body="AI 重改未能给出可用结果，原成绩保持不变，请人工复核。")
        return {"status": "appeal_regrade_failed"}
    # Claim first: the compare-and-set row update serialises this callback
    # against the teacher's decision (which also updates the row first).
    claimed = {**state, "status": "storing"}
    if not _compare_and_set_state(conn, request, claimed):
        return {"status": "ignored_stale_appeal_regrade"}
    revision_id = _insert_candidate(conn, request, state, data, normalized)
    done = {**state, "status": "done", "finished_at": _now(), "candidate_revision_id": revision_id,
            "score": normalized.get("score"), "changed": bool(data.get("changed"))}
    _compare_and_set_state(conn, _load_request_row(conn, request_id), done)
    record_request_event(conn, request_id, "ai_regrade_completed", note="AI 重改完成，候选结果待教师核对",
                         payload={"candidate_revision_id": revision_id})
    _notify_teacher(conn, request, title="AI 重改完成",
                    body=f"AI 复核建议 {normalized.get('score')} 分，请在答题卡中核对并决定是否采纳。")
    return {"status": "appeal_candidate_stored", "candidate_revision_id": revision_id}


def _insert_candidate(conn, request: Mapping[str, Any], state: Mapping[str, Any], data: Mapping[str, Any],
                      normalized: Mapping[str, Any]) -> int:
    from ..db.connection import execute_insert_returning_id, get_configured_db_engine

    # The answer is identified by the appeal itself, never by the callback body.
    submission_id = int(_payload(request).get("submission_id") or data.get("submission_id") or 0)
    audit = {
        "appeal_request_id": int(request["id"]),
        "base_revision_id": state.get("base_revision_id"),
        "questions": normalized.get("questions") or data.get("questions") or [],
        "prior_issues": data.get("prior_issues") or [],
        "appeal_response": str(data.get("appeal_response") or "")[:400],
        "changed": bool(data.get("changed")),
        "quality_audit": data.get("quality_audit") or {},
        "execution_metadata": data.get("execution_metadata") or {},
    }
    next_no = conn.execute("SELECT COALESCE(MAX(revision_no), 0) + 1 AS n FROM submission_grade_revisions WHERE submission_id = ?",
                           (submission_id,)).fetchone()
    return int(execute_insert_returning_id(
        conn,
        """
        INSERT INTO submission_grade_revisions (
            submission_id, ai_job_id, revision_hash, revision_no, status, score, feedback_md,
            quality_audit_json, provenance_json, created_at
        ) VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?)
        """,
        (submission_id, data.get("ai_job_id"), f"appeal:{state['token']}", int(next_no["n"] if next_no else 1),
         normalized.get("score"), str(normalized.get("feedback_md") or ""),
         json.dumps(audit, ensure_ascii=False, sort_keys=True, default=str),
         json.dumps({"source": "appeal_ai", "appeal_request_id": int(request["id"]),
                     "requested_model": data.get("requested_model") or "",
                     "requested_provider": data.get("requested_provider") or ""}, ensure_ascii=False, sort_keys=True),
         _now()),
        engine=get_configured_db_engine(),
    ))


def _notify_teacher(conn, request: Mapping[str, Any], *, title: str, body: str) -> None:
    from . import message_center_service as mc
    from .approval_workflow_service import _REGISTRY, _load_reviewers

    spec = _REGISTRY.get(str(request.get("request_type") or ""))
    link = spec.inbox_link(request, "teacher") if spec and spec.inbox_link else ""
    for reviewer in _load_reviewers(conn, int(request["id"])):
        payload = mc._build_notification_payload(
            recipient_role="teacher", recipient_user_pk=int(reviewer["reviewer_user_pk"]),
            category=mc.MESSAGE_CATEGORY_APPROVAL, title=f"{title}：{request.get('title') or ''}",
            severity="normal", body_preview=body, link_url=link,
            class_offering_id=mc._safe_int(request.get("class_offering_id")),
            ref_type="appeal_ai_regrade", ref_id=f"{int(request['id'])}:{title}:{_now()}",
            metadata={"request_id": int(request["id"]), "request_type": request.get("request_type")},
        )
        mc._insert_notification_if_allowed(conn, payload)
