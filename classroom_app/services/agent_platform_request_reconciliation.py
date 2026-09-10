"""Current-owner manual declarations, separate from observed HTTP facts.

No function commits or executes a platform request. An uncertain write may be
released only after its task and actual host execution have ended. A human
declaration is not business verification and never overwrites a late response.
"""
import hashlib
import json
import time
import uuid

from fastapi import HTTPException

from .agent_actor_service import resolve_agent_actor, task_actor_identity
from .agent_delegation_service import _assert_session, _hash, _text


_TERMINAL = {"completed", "failed", "canceled"}


def _source(conn, *, user, source_session_id, task_id, lock=False):
    session = _text(source_session_id, "source_session_id")
    if user.get("session_id") and str(user["session_id"]) != session:
        raise HTTPException(401, "核对来源与当前登录会话不一致。")
    if lock:
        conn.execute("UPDATE agent_tasks SET status=status WHERE id=?", (int(task_id),))
    row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在。")
    task = dict(row)
    actor = resolve_agent_actor(conn, user.get("role"), user.get("id"))
    if task_actor_identity(task) != (actor.role, actor.id):
        raise HTTPException(403, "只能核对自己任务的平台请求。")
    if lock:
        conn.execute("UPDATE user_sessions SET expires_at=expires_at WHERE session_user_key=?", (actor.key,))
    session_hash = _hash(session)
    _assert_session(conn, actor, session_hash, int(time.time()))
    return task, actor, session_hash


def _identifier(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "平台请求编号无效。") from None
    return value


def _row(conn, *, task_id, actor, request_id, lock=False):
    parameters = (_identifier(request_id), int(task_id), actor.role, actor.id)
    if lock:
        # Same row lock works in both engines. Settlement only locks this row;
        # task-before-request ordering agrees with request admission.
        conn.execute("UPDATE agent_platform_requests SET id=id WHERE id=? AND task_id=? AND actor_role=? AND actor_id=?", parameters)
    row = conn.execute("SELECT * FROM agent_platform_requests WHERE id=? AND task_id=? AND actor_role=? AND actor_id=?", parameters).fetchone()
    if row is None:
        raise HTTPException(404, "当前任务没有该平台请求。")
    return dict(row)


def _revision(row):
    # Opaque optimistic revision also changes when an HTTP result arrives or
    # the host records its end. Secrets themselves never leave this service.
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _block_reason(task, row):
    if row["reconciliation_status"] == "cleared":
        return "已保存人工核对声明；声明不能改写，请结合后续真实观察核对。"
    if not row["mutates"]:
        return "该请求为读取操作，无需解除写入意图占位。"
    if row["status"] not in {"uncertain", "submitted"}:
        return "已有明确 HTTP 观察或请求尚在执行，请先查看原请求状态。"
    if task["status"] not in _TERMINAL:
        return "原任务尚未结束，不能解除操作占位。"
    if row["host_execution_finished_at"] is None:
        return "执行是否结束尚未获得证明，仍需执行恢复核查。"
    return ""


def _view(task, row, *, details=True):
    result = json.loads(row["result_json"])
    parameters = json.loads(row["request_json"]).get("parameters", {}) if details else {}
    reason = _block_reason(task, row)
    by = None if row["reconciled_by_id"] is None else {"role": row["reconciled_by_role"], "id": row["reconciled_by_id"]}
    return {
        "id": row["id"], "task_id": row["task_id"], "operation_id": row["operation_id"],
        "capability_key": row["capability_key"], "method": row["method"], "path": row["path"], "mutates": bool(row["mutates"]),
        "summary": f"{row['method']} {row['path']} ({row['capability_key']})", "request": {"parameters": parameters}, "details_loaded": details,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "host_execution_finished_at": row["host_execution_finished_at"], "revision": _revision(row),
        "observation": {"status": row["status"], "http_status": result.get("http_status"), "result": result if details else {},
                        "settled_at": row["settled_at"], "verified_business": False},
        "reconciliation": {"status": row["reconciliation_status"], "resolution": row["reconciliation_resolution"],
            "at": row["reconciled_at"], "by": by, "note": row["reconciliation_note"] or "", "verified_business": False,
            "late_http_observation": bool(row["reconciled_at"] is not None and row["settled_at"] is not None
                                          and row["settled_at"] >= row["reconciled_at"])},
        "can_reconcile_occurred": not reason, "can_reconcile_not_occurred": not reason, "block_reason": reason,
    }


def list_user_platform_requests(conn, *, user, source_session_id, task_id, limit=20, offset=0):
    if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or not 0 <= offset <= 10000:
        raise HTTPException(400, "请求列表范围无效。")
    task, actor, _ = _source(conn, user=user, source_session_id=source_session_id, task_id=task_id)
    rows = conn.execute("SELECT * FROM agent_platform_requests WHERE task_id=? AND actor_role=? AND actor_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                        (int(task_id), actor.role, actor.id, limit + 1, offset)).fetchall()
    return {"requests": [_view(task, dict(row), details=False) for row in rows[:limit]], "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None}


def get_user_platform_request(conn, *, user, source_session_id, task_id, request_id):
    task, actor, _ = _source(conn, user=user, source_session_id=source_session_id, task_id=task_id)
    return _view(task, _row(conn, task_id=task_id, actor=actor, request_id=request_id))


def reconcile_user_platform_request(conn, *, user, source_session_id, task_id, request_id,
                                    resolution, note, expected_revision):
    if not isinstance(resolution, str) or resolution not in {"occurred", "not_occurred"}:
        raise HTTPException(400, "请选择已发生或未发生。")
    if (not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000
            or any(0xD800 <= ord(c) <= 0xDFFF or (ord(c) < 32 and c not in "\n\r\t") for c in note)):
        raise HTTPException(400, "请填写不超过1000字的核对依据。")
    note = note.strip()
    if (not isinstance(expected_revision, str) or len(expected_revision) != 64
            or any(c not in "0123456789abcdef" for c in expected_revision)):
        raise HTTPException(400, "请重新读取请求状态后核对。")
    task, actor, session_hash = _source(conn, user=user, source_session_id=source_session_id, task_id=task_id, lock=True)
    row = _row(conn, task_id=task_id, actor=actor, request_id=request_id, lock=True)
    if row["reconciliation_status"] == "cleared":
        if row["reconciliation_resolution"] != resolution or row["reconciliation_note"] != note:
            raise HTTPException(409, "已保存的人工声明不能改写。")
        return {"request": _view(task, row), "replayed": True}
    if _revision(row) != expected_revision:
        raise HTTPException(409, "请求已产生新观察或执行状态变化，请刷新后核对。")
    reason = _block_reason(task, row)
    if reason:
        raise HTTPException(409, reason)
    now = int(time.time())
    changed = conn.execute("""UPDATE agent_platform_requests
        SET reconciliation_status='cleared',reconciliation_resolution=?,reconciled_at=?,
            reconciled_by_role=?,reconciled_by_id=?,reconciled_session_hash=?,reconciliation_note=?
        WHERE id=? AND task_id=? AND actor_role=? AND actor_id=? AND reconciliation_status='pending'
            AND host_execution_finished_at IS NOT NULL AND status IN ('uncertain','submitted')""",
        (resolution, now, actor.role, actor.id, session_hash, note, request_id, int(task_id), actor.role, actor.id))
    if changed.rowcount != 1:
        raise HTTPException(409, "请求状态已变化，请重新核对。")
    from .agent_task_service import append_task_event
    append_task_event(conn, int(task_id), "platform_request_reconciled", "用户已保存平台请求的人工核对声明。", {
        "request_id": request_id, "resolution": resolution, "verified_business": False,
        "follow_up": "仅可创建新任务并取得新授权后继续；原工具授权不会恢复。"}, commit=False)
    row = _row(conn, task_id=task_id, actor=actor, request_id=request_id)
    return {"request": _view(task, row), "replayed": False}
