"""Transactional operation deduplication; caller owns business authorization.

After claim_agent_operation(...).claimed, execute the normal authorized business
service and complete_agent_operation on the SAME connection/transaction. Commit
only after both succeed; otherwise roll back all three. Never perform external
side effects directly between these calls: use a transactional outbox and the
same logical operation_id, or reconcile an unknown external result first.

No function commits, rolls back, creates schema, or silently reclaims executing
operations. Existing executing/failed rows require explicit reconciliation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
import uuid

from fastapi import HTTPException

from .agent_actor_service import resolve_agent_actor, task_actor_identity
from .agent_delegation_service import _assert_session, _hash, _now, _text, verify_task_delegation


MAX_RECEIPT_BYTES = 256 * 1024
MAX_PARAMS_BYTES = 1024 * 1024


def _canonical(value: Any, *, maximum: int) -> str:
    try:
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="操作参数或回执必须是有效 JSON。") from None
    if len(content.encode("utf-8")) > maximum:
        raise HTTPException(status_code=400, detail="操作参数或回执超过大小限制。")
    return content


def _receipt(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["result"] = json.loads(result.pop("result_json"))
    result.pop("source_session_hash", None)
    result.pop("authority_fingerprint", None)
    return result


def _operation_input(operation_id, action, params, resource_revision):
    operation_id, action = _text(operation_id, "operation_id"), _text(action, "action")
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="操作参数必须是对象。")
    if not isinstance(resource_revision, str) or len(resource_revision) > 200:
        raise HTTPException(status_code=400, detail="资源版本无效。")
    params_hash = hashlib.sha256(_canonical(params, maximum=MAX_PARAMS_BYTES).encode("utf-8")).hexdigest()
    return operation_id, action, params_hash


def claim_agent_operation(conn, *, token: str, operation_id: str, action: str, params: dict[str, Any], required_scope: str, resource_revision: str = "", now: int | None = None) -> dict[str, Any]:
    """Atomically occupy one actor-scoped logical action; never self-commit."""
    timestamp = _now(now)
    operation_id, action, params_hash = _operation_input(operation_id, action, params, resource_revision)
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope=required_scope, lock_task=True, now=timestamp)
    identifier = str(uuid.uuid4())
    cursor = conn.execute(
        "INSERT INTO agent_action_executions (id, operation_id, actor_role, actor_id, task_id, attempt_id, fencing_token, delegation_id, action, params_hash, resource_revision, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'executing', ?, ?) "
        "ON CONFLICT (actor_role, actor_id, operation_id) DO NOTHING",
        (identifier, operation_id, grant.actor.role, grant.actor.id, int(grant.task["id"]), grant.attempt["id"], grant.attempt["fencing_token"], grant.delegation["id"], action, params_hash, resource_revision, timestamp, timestamp),
    )
    claimed = cursor.rowcount == 1
    row = conn.execute("SELECT * FROM agent_action_executions WHERE actor_role = ? AND actor_id = ? AND operation_id = ?", (grant.actor.role, grant.actor.id, operation_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="操作占位未成功，请重新核对任务状态。")
    if (row["params_hash"] != params_hash or row["action"] != action or row["resource_revision"] != resource_revision
            or row["source_kind"] != "delegation" or int(row["task_id"]) != int(grant.task["id"])):
        raise HTTPException(status_code=409, detail="同一操作编号已绑定不同参数、动作或资源版本。")
    return {"claimed": claimed, "operation": _receipt(row)}


def complete_agent_operation(conn, *, token: str, operation_id: str, result: dict[str, Any], required_scope: str, now: int | None = None) -> dict[str, Any]:
    """Store the receipt atomically with business changes; reject old attempts."""
    timestamp = _now(now)
    operation_id = _text(operation_id, "operation_id")
    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail="操作回执必须是对象。")
    result_json = _canonical(result, maximum=MAX_RECEIPT_BYTES)
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope=required_scope, lock_task=True, now=timestamp)
    row = conn.execute("SELECT * FROM agent_action_executions WHERE actor_role = ? AND actor_id = ? AND operation_id = ?", (grant.actor.role, grant.actor.id, operation_id)).fetchone()
    if (row is None or row["source_kind"] != "delegation" or row["attempt_id"] != grant.attempt["id"]
            or int(row["fencing_token"] or 0) != int(grant.attempt["fencing_token"])):
        raise HTTPException(status_code=409, detail="操作不属于当前执行租约。")
    if row["status"] == "completed":
        if row["result_json"] != result_json:
            raise HTTPException(status_code=409, detail="已完成操作的回执不能改写。")
        return _receipt(row)
    cursor = conn.execute(
        "UPDATE agent_action_executions SET status = 'completed', result_json = ?, updated_at = ?, completed_at = ? "
        "WHERE id = ? AND status = 'executing' AND attempt_id = ? AND fencing_token = ?",
        (result_json, timestamp, timestamp, row["id"], grant.attempt["id"], grant.attempt["fencing_token"]),
    )
    if cursor.rowcount != 1:
        raise HTTPException(status_code=409, detail="操作状态已变化，不能提交回执。")
    return _receipt(conn.execute("SELECT * FROM agent_action_executions WHERE id = ?", (row["id"],)).fetchone())


def _fresh_user_source(conn, *, user: dict[str, Any], source_session_id: str, task_id: int, now: int):
    """Authorize a new manual action on the current user's ended task.

    A terminal task stays terminal. This source neither issues a delegation nor
    renews a runner attempt, and cannot be used with model/tool bearer tokens.
    """
    session = _text(source_session_id, "source_session_id")
    if user.get("session_id") and str(user["session_id"]) != session:
        raise HTTPException(401, "操作来源与当前登录会话不一致。")
    try:
        actor_id = int(user.get("id") or 0)
        task_id = int(task_id)
    except (TypeError, ValueError):
        raise HTTPException(400, "操作身份或任务编号无效。") from None
    actor_role = str(user.get("role") or "teacher")
    cursor = conn.execute("""
        UPDATE agent_tasks SET status = status
        WHERE id = ? AND status IN ('completed', 'failed', 'canceled')
    """, (task_id,))
    if cursor.rowcount != 1:
        raise HTTPException(409, "只有已结束任务的待确认操作可由当前用户重新授权。")
    task = dict(conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())
    if task_actor_identity(task) != (actor_role, actor_id):
        raise HTTPException(403, "不能确认其他用户任务的操作。")
    actor = resolve_agent_actor(conn, actor_role, actor_id)
    session_hash = _hash(session)
    _assert_session(conn, actor, session_hash, now)
    return task, actor, session_hash


def claim_user_agent_operation(conn, *, user: dict[str, Any], source_session_id: str, task_id: int,
                               operation_id: str, action: str, params: dict[str, Any],
                               resource_revision: str = "", now: int | None = None) -> dict[str, Any]:
    """Claim one fresh authenticated confirmation, in the business transaction.

    The caller verifies the concrete proposal/preview and reuses the domain's
    current authorization before changing data. A receipt never grants rights.
    """
    timestamp = _now(now)
    operation_id, action, params_hash = _operation_input(operation_id, action, params, resource_revision)
    task, actor, session_hash = _fresh_user_source(conn, user=user, source_session_id=source_session_id, task_id=task_id, now=timestamp)
    cursor = conn.execute("""
        INSERT INTO agent_action_executions (
            id, operation_id, actor_role, actor_id, task_id, source_kind,
            source_session_hash, authority_fingerprint, action, params_hash,
            resource_revision, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'user_confirmation', ?, ?, ?, ?, ?, 'executing', ?, ?)
        ON CONFLICT (actor_role, actor_id, operation_id) DO NOTHING
    """, (str(uuid.uuid4()), operation_id, actor.role, actor.id, int(task["id"]), session_hash,
          actor.authority_fingerprint, action, params_hash, resource_revision, timestamp, timestamp))
    row = conn.execute("SELECT * FROM agent_action_executions WHERE actor_role = ? AND actor_id = ? AND operation_id = ?", (actor.role, actor.id, operation_id)).fetchone()
    if (row is None or row["source_kind"] != "user_confirmation" or int(row["task_id"]) != int(task["id"])
            or row["action"] != action or row["params_hash"] != params_hash or row["resource_revision"] != resource_revision):
        raise HTTPException(409, "同一操作编号已绑定不同任务、来源、参数、动作或资源版本。")
    # A new valid login can read a completed receipt. It cannot take over an
    # unknown in-flight write left by a different login or authority context.
    if row["status"] != "completed" and (row["source_session_hash"] != session_hash or row["authority_fingerprint"] != actor.authority_fingerprint):
        raise HTTPException(409, "未完成操作的授权来源已变化，需要先核对原操作结果。")
    return {"claimed": cursor.rowcount == 1, "operation": _receipt(row)}


def complete_user_agent_operation(conn, *, user: dict[str, Any], source_session_id: str, task_id: int,
                                  operation_id: str, result: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    """Commit proof for a fresh confirmation; never revive the ended runner."""
    timestamp = _now(now)
    operation_id = _text(operation_id, "operation_id")
    if not isinstance(result, dict):
        raise HTTPException(400, "操作回执必须是对象。")
    result_json = _canonical(result, maximum=MAX_RECEIPT_BYTES)
    task, actor, session_hash = _fresh_user_source(conn, user=user, source_session_id=source_session_id, task_id=task_id, now=timestamp)
    row = conn.execute("SELECT * FROM agent_action_executions WHERE actor_role = ? AND actor_id = ? AND operation_id = ?", (actor.role, actor.id, operation_id)).fetchone()
    if row is None or row["source_kind"] != "user_confirmation" or int(row["task_id"]) != int(task["id"]):
        raise HTTPException(409, "操作不属于本次用户确认。")
    if row["source_session_hash"] != session_hash or row["authority_fingerprint"] != actor.authority_fingerprint:
        raise HTTPException(401, "操作期间登录会话或权限已变化，请重新核对结果。")
    if row["status"] == "completed":
        if row["result_json"] != result_json:
            raise HTTPException(409, "已完成操作的回执不能改写。")
        return _receipt(row)
    cursor = conn.execute("""
        UPDATE agent_action_executions SET status = 'completed', result_json = ?, updated_at = ?, completed_at = ?
        WHERE id = ? AND status = 'executing' AND source_kind = 'user_confirmation' AND source_session_hash = ?
    """, (result_json, timestamp, timestamp, row["id"], session_hash))
    if cursor.rowcount != 1:
        raise HTTPException(409, "操作状态已变化，不能提交回执。")
    return _receipt(conn.execute("SELECT * FROM agent_action_executions WHERE id = ?", (row["id"],)).fetchone())
