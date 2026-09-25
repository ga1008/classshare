"""Task lifecycle for the Agents-SDK runtime: attempt setup, park, resume, finalize.

A task runs in *segments*. A segment ends by finishing (completed / failed /
canceled) or by *parking* the task back in the queue without holding a slot:

  running ──ask_user──▶ queued + waiting_input ──answer──▶ queued + resume_pending ──claim──▶ running
  running ──pause────▶ queued + paused         ──resume──▶ queued + resume_pending ──claim──▶ running

The model conversation (``history_json``) is saved when parking so the next
segment continues exactly where it stopped. Every transition is fenced by the
attempt token, so a stale worker can never overwrite a newer one.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from ...config import AGENT_TASK_MAX_RUNTIME_SECONDS, AGENT_TASK_PARKED_TTL_HOURS
from ...database import get_db_connection
from ..agent_actor_service import resolve_live_task_actor, task_actor_identity
from ..agent_delegation_service import create_task_attempt, finish_task_attempt, issue_task_delegation
from ..agent_runtime_schema import ensure_agent_runtime_schema
from ..agent_task_service import (
    AGENT_RUNTIME_PROVIDER,
    RESUME_PRIORITY,
    RUNTIME_PAUSED,
    RUNTIME_RESUME_PENDING,
    RUNTIME_WAITING_INPUT,
    _clean_text,
    _is_task_owner,
    _load_json,
    append_task_event,
    finish_agent_task,
    utcnow_iso,
)
from .recorder import clip, redact

TOOLS_SCOPES = ["platform:read", "platform:write", "web:fetch"]
MAX_HISTORY_BYTES = 6 * 1024 * 1024
STALE_ATTEMPT_GRACE_SECONDS = 180


def _json(value: Any) -> str:
    def default(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(exclude_unset=True)
        return str(item)

    return json.dumps(value, ensure_ascii=False, default=default)


# ---------------------------------------------------------------- run state rows

def load_run_state(conn, task_id: int) -> dict[str, Any]:
    ensure_agent_runtime_schema(conn)
    row = conn.execute("SELECT * FROM agent_run_states WHERE task_id = ?", (int(task_id),)).fetchone()
    if not row:
        return {"task_id": int(task_id), "history": [], "pending_question": None, "answer": None,
                "pause_requested": 0, "segments": 0, "questions_asked": 0}
    item = dict(row)
    item["history"] = _load_json(item.get("history_json"), [])
    item["pending_question"] = _load_json(item.get("pending_question_json"), None) if item.get("pending_question_json") else None
    item["answer"] = _load_json(item.get("answer_json"), None) if item.get("answer_json") else None
    return item


def _upsert_state(conn, task_id: int, **fields: Any) -> None:
    ensure_agent_runtime_schema(conn)
    fields["updated_at"] = utcnow_iso()
    exists = conn.execute("SELECT 1 FROM agent_run_states WHERE task_id = ?", (int(task_id),)).fetchone()
    if exists:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE agent_run_states SET {assignments} WHERE task_id = ?", (*fields.values(), int(task_id)))
    else:
        columns = ", ".join(["task_id", *fields])
        marks = ", ".join("?" for _ in range(len(fields) + 1))
        conn.execute(f"INSERT INTO agent_run_states ({columns}) VALUES ({marks})", (int(task_id), *fields.values()))


# ---------------------------------------------------------------- attempt setup

def setup_attempt(task_id: int) -> tuple[dict[str, Any], Any, dict[str, Any], str]:
    """Create a fenced attempt and a tools credential bound to the user's live authority."""
    with get_db_connection() as conn:
        task, actor = resolve_live_task_actor(conn, task_id)
        attempt = create_task_attempt(conn, task_id=task_id, worker_id=str(task.get("worker_id") or "agent-worker"),
                                      startup_key=str(uuid.uuid4()), lease_seconds=60)
        source: dict[str, Any] = {"source_session_hash": task.get("source_session_hash"),
                                  "source_session_key": task.get("source_session_key")}
        scopes = list(TOOLS_SCOPES)
        if task.get("persistent_authorization_id"):
            from ..agent_delegation_service import _assert_persistent

            source = {"persistent_authorization_id": task["persistent_authorization_id"]}
            authority = _assert_persistent(conn, identifier=source["persistent_authorization_id"], actor=actor,
                                            scopes=["platform:read"], now=int(time.time()))
            allowed = set(json.loads(authority["scopes_json"]))
            scopes = [scope for scope in TOOLS_SCOPES if scope in allowed]
        token = issue_task_delegation(conn, task_id=task_id, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                                      purpose="tools", scopes=scopes, ttl_seconds=AGENT_TASK_MAX_RUNTIME_SECONDS + 120,
                                      **source)["token"]
        conn.execute("UPDATE agent_tasks SET runtime_provider = ?, updated_at = ? WHERE id = ?",
                     (AGENT_RUNTIME_PROVIDER, utcnow_iso(), int(task_id)))
        conn.commit()
    return task, actor, attempt, token


def _lock_current(conn, attempt: dict[str, Any]) -> dict[str, Any] | None:
    """Lock the task row; return it only if this attempt is still the latest and running."""
    task_id = int(attempt["task_id"])
    conn.execute("UPDATE agent_tasks SET status = status WHERE id = ?", (task_id,))
    task = conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
    latest = conn.execute("SELECT MAX(fencing_token) AS fence FROM agent_task_attempts WHERE task_id = ?", (task_id,)).fetchone()
    if not task or task["status"] != "running" or int(latest["fence"] or 0) != int(attempt["fencing_token"]):
        return None
    return dict(task)


# ---------------------------------------------------------------- supplements

def take_pending_supplements(task_id: int) -> list[str]:
    """Mark undelivered supplements as delivered and return their text (one short lock)."""
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_tasks SET status = status WHERE id = ?", (int(task_id),))
        row = conn.execute("SELECT context_snapshot_json FROM agent_tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not row:
            conn.rollback()
            return []
        context = _load_json(row["context_snapshot_json"], {})
        options = context.get("agent_options") if isinstance(context.get("agent_options"), dict) else {}
        items = [item for item in options.get("pending_supplements") or [] if isinstance(item, dict)]
        lines = []
        for item in items:
            if item.get("delivery_status") != "delivered":
                message = _clean_text(item.get("message"), max_chars=4000)
                if message:
                    lines.append(f"- {message}")
                item["delivery_status"] = "delivered"
        if not lines:
            conn.rollback()
            return []
        options["pending_supplements"] = items
        context["agent_options"] = options
        conn.execute("UPDATE agent_tasks SET context_snapshot_json = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(context, ensure_ascii=False), utcnow_iso(), int(task_id)))
        append_task_event(conn, int(task_id), "supplements_delivered", f"Agent 已接收 {len(lines)} 条补充说明。",
                          {"count": len(lines)}, commit=False)
        conn.commit()
        return lines


def mark_finalizing(task_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_tasks SET runtime_status = 'finalizing', updated_at = ? WHERE id = ? AND status = 'running'",
                     (utcnow_iso(), int(task_id)))
        conn.commit()


# ---------------------------------------------------------------- resume input

def consume_resume_state(task_id: int) -> tuple[list[Any], dict[str, Any] | None, list[dict[str, Any]] | None]:
    """Return (history, answered_question, answers) and clear them so they are used once."""
    with get_db_connection() as conn:
        state = load_run_state(conn, task_id)
        history = state["history"] if isinstance(state["history"], list) else []
        question, answer = state.get("pending_question"), state.get("answer")
        # pause_requested is kept: a pause asked for while queued is honoured
        # before the first model call (park_task clears it).
        _upsert_state(conn, task_id, pending_question_json="", answer_json="", segments=int(state.get("segments") or 0) + 1)
        conn.commit()
    return history, (question if answer else None), (answer if question else None)


# ---------------------------------------------------------------- park

def park_task(attempt: dict[str, Any], *, reason: str, history: list[Any], question: dict[str, Any] | None = None,
              usage: dict[str, Any] | None = None) -> str:
    """Save the conversation and return the task to the queue without a slot.

    Returns "parked", or "canceled"/"stale" when the caller must not park."""
    serialized = _json(history)
    if len(serialized.encode("utf-8")) > MAX_HISTORY_BYTES:
        raise ValueError("对话过长，无法保存进度")
    with get_db_connection() as conn:
        task = _lock_current(conn, attempt)
        if task is None:
            conn.rollback()
            return "stale"
        if task.get("cancel_requested_at"):
            conn.rollback()
            return "canceled"
        task_id = int(task["id"])
        now = utcnow_iso()
        fields: dict[str, Any] = {"history_json": serialized, "pause_requested": 0, "pause_requested_by": "",
                                  "usage_json": _json(usage or {})}
        if question is not None:
            question = {**question, "id": f"q-{uuid.uuid4().hex[:12]}", "created_at": now}
            state = load_run_state(conn, task_id)
            fields.update(pending_question_json=_json(question), answer_json="",
                          questions_asked=int(state.get("questions_asked") or 0) + 1)
        _upsert_state(conn, task_id, **fields)
        conn.execute("UPDATE agent_tasks SET status = 'queued', runtime_status = ?, worker_id = NULL, updated_at = ? "
                     "WHERE id = ? AND status = 'running'", (reason, now, task_id))
        finish_task_attempt(conn, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"], status="completed")
        if question is not None:
            append_task_event(conn, task_id, "question_requested", f"需要你确认：{clip(question.get('title'), 80)}",
                              {"question": redact(question)}, commit=False)
            _notify_question(conn, task, question)
        else:
            append_task_event(conn, task_id, "task_paused", "已暂停：进度已保存并让出队列，恢复后从当前步骤继续。",
                              {"runtime_status": reason}, commit=False)
        conn.commit()
    return "parked"


def _notify_question(conn, task: dict[str, Any], question: dict[str, Any]) -> None:
    try:
        from ..message_center_service import create_agent_task_notification

        role, actor_id = task_actor_identity(task)
        create_agent_task_notification(
            conn, recipient_role=role, recipient_user_pk=actor_id,
            title=f"❓ Agent 需要你确认：{clip(task.get('title') or 'Agent 任务', 30)}",
            body_preview=clip(question.get("title"), 120), link_url=f"/dashboard?agent_task={int(task['id'])}",
            ref_id=f"agent-task:{int(task['id'])}:{question['id']}", actor_display_name="LanShare Agent",
            metadata={"agent_task_id": int(task["id"]), "question_id": question["id"]}, allow_duplicates=False)
    except Exception as exc:
        print(f"[AGENT_SDK] question notification failed for task {task.get('id')}: {exc}")


# ---------------------------------------------------------------- answer (called by the web app)

def pending_question_for(conn, task_id: int) -> dict[str, Any] | None:
    return load_run_state(conn, task_id).get("pending_question")


def answer_parked_question(conn, task_id: int, *, user: dict[str, Any], question_id: str,
                           answers: list[dict[str, Any]]) -> dict[str, Any]:
    conn.execute("UPDATE agent_tasks SET status = status WHERE id = ?", (int(task_id),))
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在。")
    task = dict(row)
    if not _is_task_owner(task, int(user["id"]), str(user.get("role") or "teacher")):
        raise HTTPException(403, "只能回答自己任务中的疑问。")
    if task.get("status") != "queued" or task.get("runtime_status") != RUNTIME_WAITING_INPUT:
        raise HTTPException(409, "该任务当前没有等待回答的疑问。")
    question = pending_question_for(conn, task_id) or {}
    if not question or question.get("id") != str(question_id):
        raise HTTPException(409, "疑问已更新，请刷新后再回答。")
    normalized = _normalize_answers(question, answers)
    _upsert_state(conn, task_id, answer_json=_json(normalized))
    conn.execute("UPDATE agent_tasks SET runtime_status = ?, priority = CASE WHEN priority < ? THEN ? ELSE priority END, "
                 "updated_at = ? WHERE id = ?", (RUNTIME_RESUME_PENDING, RESUME_PRIORITY, RESUME_PRIORITY, utcnow_iso(), int(task_id)))
    summary = "；".join(
        f"{item['question_text']} → {'、'.join(item['selected'])}{('（' + clip(item['custom'], 40) + '）') if item['custom'] else ''}"
        for item in normalized)
    append_task_event(conn, int(task_id), "question_answered", f"你已回答：{clip(summary, 200)}",
                      {"question_id": question["id"], "answers": normalized}, commit=False)
    conn.commit()
    return {"question_id": question["id"], "answers": normalized}


def _normalize_answers(question: dict[str, Any], answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(answers, list):
        raise HTTPException(400, "answers 必须是列表。")
    by_id = {str(item.get("id")): item for item in answers if isinstance(item, dict)}
    normalized = []
    for item in question.get("questions") or []:
        answer = by_id.get(str(item.get("id"))) or {}
        allowed = {option["label"] for option in item.get("options") or []}
        selected = [str(value) for value in answer.get("selected") or [] if str(value) in allowed]
        if not item.get("multi_select"):
            selected = selected[:1]
        custom = _clean_text(answer.get("custom"), max_chars=2000)
        if not selected and not custom:
            raise HTTPException(400, f"请回答：{item.get('question')}")
        normalized.append({"id": item.get("id"), "question_text": clip(item.get("question"), 80),
                           "selected": selected, "custom": custom})
    return normalized


# ---------------------------------------------------------------- finalize

def finalize_task(attempt: dict[str, Any], *, status: str, summary: str, detail: dict[str, Any], error: str = "") -> bool:
    """Write the terminal result once, fenced to the current attempt."""
    from ..agent_platform_request_service import mark_abandoned_platform_requests_uncertain
    from ..agent_task_receipts import platform_request_observations, verified_platform_operations

    with get_db_connection() as conn:
        task = _lock_current(conn, attempt)
        if task is None:
            conn.rollback()
            return False
        task_id = int(task["id"])
        if task.get("cancel_requested_at") and status != "canceled":
            status, error = "canceled", ""
            summary = "任务已取消；取消前已执行的操作保留在回执中。"
        rows = conn.execute("SELECT operation_id, actor_role, actor_id, action, status, result_json FROM agent_action_executions "
                            "WHERE task_id = ? ORDER BY created_at", (task_id,)).fetchall()
        operations, blockers = verified_platform_operations(conn, task, rows)
        mark_abandoned_platform_requests_uncertain(conn, task_id=task_id, attempt_id=attempt["id"])
        observations, request_blockers = platform_request_observations(conn, task)
        detail = {**detail, "platform_operations": operations, "platform_requests": observations,
                  "completion_blockers": blockers + request_blockers}
        _upsert_state(conn, task_id, history_json="[]", pending_question_json="", answer_json="", pause_requested=0)
        conn.execute("UPDATE agent_tasks SET runtime_status = ? WHERE id = ?", (status, task_id))
        finish_task_attempt(conn, attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                            status=status if status in {"completed", "failed", "canceled"} else "failed")
        finish_agent_task(conn, task_id, status=status, result_summary=summary, result_detail=redact(detail), error_message=error)
    return True


def fail_unstarted(task: dict[str, Any], message: str) -> None:
    """The run could not create an attempt (authority lost, model missing)."""
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_tasks SET status = status WHERE id = ?", (int(task["id"]),))
        row = conn.execute("SELECT status, worker_id FROM agent_tasks WHERE id = ?", (int(task["id"]),)).fetchone()
        if not row or row["status"] != "running" or str(row["worker_id"] or "") != str(task.get("worker_id") or ""):
            conn.rollback()
            return
        conn.execute("UPDATE agent_tasks SET runtime_status = 'failed' WHERE id = ?", (int(task["id"]),))
        finish_agent_task(conn, int(task["id"]), status="failed", result_summary="",
                          result_detail={"provider": AGENT_RUNTIME_PROVIDER}, error_message=message)


def renew_lease(attempt: dict[str, Any]) -> None:
    from ..agent_delegation_service import renew_task_attempt

    with get_db_connection() as conn:
        renew_task_attempt(conn, task_id=attempt["task_id"], attempt_id=attempt["id"], fencing_token=attempt["fencing_token"],
                           worker_id=attempt["worker_id"], lease_seconds=60)
        conn.commit()


def run_signals(attempt: dict[str, Any]) -> dict[str, bool]:
    """Cheap poll for cancel / pause requests during a segment."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT status, cancel_requested_at FROM agent_tasks WHERE id = ?", (int(attempt["task_id"]),)).fetchone()
        state = load_run_state(conn, int(attempt["task_id"]))
    canceled = not row or row["status"] != "running" or bool(row["cancel_requested_at"])
    return {"canceled": canceled, "pause": bool(state.get("pause_requested"))}


# ---------------------------------------------------------------- housekeeping (worker loop 1)

def recover_stale_tasks() -> int:
    """Fail running tasks whose worker died (lease expired). Nothing is replayed."""
    now = int(time.time())
    candidates = []
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id, started_at, worker_id FROM agent_tasks WHERE status = 'running' AND runtime_provider = ?",
                            (AGENT_RUNTIME_PROVIDER,)).fetchall()
        for row in rows:
            attempt = conn.execute("SELECT * FROM agent_task_attempts WHERE task_id = ? ORDER BY fencing_token DESC LIMIT 1",
                                   (int(row["id"]),)).fetchone()
            if attempt and attempt["status"] == "running" and int(attempt["lease_expires_at"] or 0) < now - 15:
                candidates.append(dict(attempt))
            elif (attempt is None or attempt["status"] != "running") and _older_than(row["started_at"], STALE_ATTEMPT_GRACE_SECONDS):
                candidates.append({"task_id": int(row["id"]), "worker_id": row["worker_id"], "orphan": True})
    recovered = 0
    for attempt in candidates:
        if attempt.get("orphan"):
            fail_unstarted({"id": attempt["task_id"], "worker_id": attempt["worker_id"]}, "Agent 执行器未能继续该任务，请重试。")
            recovered += 1
        elif finalize_task(attempt, status="failed", summary="", detail={"provider": AGENT_RUNTIME_PROVIDER, "recovered": True},
                           error="Agent 执行器意外中断，任务未完成。已执行的操作保留在回执中，可在任务卡片上重试或追问。"):
            recovered += 1
    return recovered


def expire_parked_tasks() -> int:
    """Cancel tasks left waiting for an answer / paused longer than the TTL."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=AGENT_TASK_PARKED_TTL_HOURS)).isoformat()
    expired = 0
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id FROM agent_tasks WHERE status = 'queued' AND runtime_status IN (?, ?) AND updated_at < ?",
                            (RUNTIME_WAITING_INPUT, RUNTIME_PAUSED, cutoff)).fetchall()
        for row in rows:
            now = utcnow_iso()
            conn.execute("UPDATE agent_tasks SET status = 'canceled', runtime_status = 'expired', completed_at = ?, updated_at = ? "
                         "WHERE id = ? AND status = 'queued'", (now, now, int(row["id"])))
            append_task_event(conn, int(row["id"]), "canceled",
                              f"超过 {AGENT_TASK_PARKED_TTL_HOURS} 小时未继续，任务已自动关闭；可以重试或追问。",
                              {"expired": True}, commit=False)
            expired += 1
        conn.commit()
    return expired


def _older_than(value: Any, seconds: int) -> bool:
    try:
        started = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds() > seconds
