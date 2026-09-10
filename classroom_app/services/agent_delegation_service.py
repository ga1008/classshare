"""Opaque task credentials backed by live platform identity and durable leases.

Only trusted platform code calls issue/create functions after authenticating the
user and their intent. Scope membership is an upper bound, not resource access:
every business tool must still use its regular platform authorization service.

All functions use the supplied connection and NEVER commit, roll back, run DDL,
or consult the process session cache. Callers own transaction boundaries. Task
row locks serialize attempt changes/cancellation with operation transactions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
import uuid

from fastapi import HTTPException

from .agent_actor_service import AgentActor, resolve_agent_actor, resolve_live_task_actor


TOKEN_PREFIX = "lsagt_"
MAX_DELEGATION_TTL_SECONDS = 24 * 60 * 60
MAX_LEASE_SECONDS = 60 * 60
MAX_PERSISTENT_TTL_SECONDS = 366 * 24 * 60 * 60


@dataclass(frozen=True)
class VerifiedDelegation:
    actor: AgentActor
    task: dict[str, Any]
    attempt: dict[str, Any]
    delegation: dict[str, Any]


def _now(value: int | None) -> int:
    return int(time.time()) if value is None else int(value)


def _text(value: Any, name: str, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or any(ord(char) < 32 for char in value):
        raise HTTPException(status_code=400, detail=f"{name} 无效。")
    return value.strip()


def _duration(value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise HTTPException(status_code=400, detail="授权或租约有效期超出允许范围。")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scopes(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 200:
        raise HTTPException(status_code=400, detail="必须指定明确的授权范围。")
    result = sorted({_text(item, "scope", 160) for item in value})
    if any("*" in item for item in result):
        raise HTTPException(status_code=400, detail="授权范围必须是明确的能力名称。")
    return result


def _stored_scopes(row: dict[str, Any]) -> list[str]:
    try:
        return _scopes(json.loads(row["scopes_json"]))
    except (KeyError, TypeError, ValueError, HTTPException):
        raise HTTPException(status_code=401, detail="授权记录的范围无效。") from None


def _actor_matches(row: dict[str, Any], actor: AgentActor) -> bool:
    return str(row.get("actor_role")) == actor.role and int(row.get("actor_id") or 0) == actor.id


def _expiry_epoch(value: Any) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


def _assert_session(conn, actor: AgentActor, expected_hash: str, now: int) -> None:
    row = conn.execute(
        "SELECT session_id, user_id, role, expires_at FROM user_sessions WHERE session_user_key = ? LIMIT 1",
        (actor.key,),
    ).fetchone()
    if not row or str(row["role"]) != actor.role or str(row["user_id"]) != str(actor.id):
        raise HTTPException(status_code=401, detail="源登录会话已撤销。")
    if _expiry_epoch(row["expires_at"]) <= now or not hmac.compare_digest(_hash(str(row["session_id"])), expected_hash):
        raise HTTPException(status_code=401, detail="源登录会话已过期或已更换。")


def lock_task_authority(conn, task_id: int) -> None:
    """Acquire a task row write lock before tool writes or attempt transitions.

    This neutral UPDATE works in both engines without global engine detection.
    Cancellation must update the same task row, ordering it with business commit.
    SQLite callers already in a deferred read transaction should begin IMMEDIATE
    before reading; a database-busy exception must roll back the entire operation.
    """
    cursor = conn.execute(
        "UPDATE agent_tasks SET status = status WHERE id = ? AND status = 'running' "
        "AND (cancel_requested_at IS NULL OR cancel_requested_at = '')",
        (int(task_id),),
    )
    if cursor.rowcount != 1:
        raise HTTPException(status_code=401, detail="任务已停止执行，授权已撤销。")


def assert_current_attempt(conn, *, task_id: int, attempt_id: str, fencing_token: int, now: int | None = None, lock_task: bool = False) -> tuple[dict[str, Any], AgentActor, dict[str, Any]]:
    timestamp = _now(now)
    if lock_task:
        lock_task_authority(conn, task_id)
    task, actor = resolve_live_task_actor(conn, int(task_id))
    row = conn.execute(
        "SELECT * FROM agent_task_attempts WHERE id = ? AND task_id = ? LIMIT 1",
        (str(attempt_id), int(task_id)),
    ).fetchone()
    attempt = dict(row) if row else {}
    newest = conn.execute("SELECT MAX(fencing_token) AS current_fence FROM agent_task_attempts WHERE task_id = ?", (int(task_id),)).fetchone()
    if not attempt or attempt.get("status") != "running" or int(attempt.get("fencing_token") or 0) != int(fencing_token) or int(newest["current_fence"] or 0) != int(fencing_token) or int(attempt.get("lease_expires_at") or 0) <= timestamp:
        raise HTTPException(status_code=409, detail="Agent 执行租约已失效或被接管。")
    if not _actor_matches(attempt, actor):
        raise HTTPException(status_code=401, detail="任务执行身份已发生变化。")
    return task, actor, attempt


def create_task_attempt(conn, *, task_id: int, worker_id: str, startup_key: str, lease_seconds: int = 60, now: int | None = None) -> dict[str, Any]:
    timestamp = _now(now)
    worker_id, startup_key = _text(worker_id, "worker_id"), _text(startup_key, "startup_key")
    lease_seconds = _duration(lease_seconds, MAX_LEASE_SECONDS)
    lock_task_authority(conn, task_id)
    _, actor = resolve_live_task_actor(conn, task_id)
    duplicate = conn.execute("SELECT * FROM agent_task_attempts WHERE task_id = ? AND startup_key = ? LIMIT 1", (int(task_id), startup_key)).fetchone()
    if duplicate:
        row = dict(duplicate)
        if row["worker_id"] != worker_id:
            raise HTTPException(status_code=409, detail="启动幂等键已用于其他执行器。")
        assert_current_attempt(conn, task_id=task_id, attempt_id=row["id"], fencing_token=row["fencing_token"], now=timestamp)
        return row
    newest = conn.execute("SELECT * FROM agent_task_attempts WHERE task_id = ? ORDER BY fencing_token DESC LIMIT 1", (int(task_id),)).fetchone()
    if newest and newest["status"] == "running" and int(newest["lease_expires_at"]) > timestamp:
        raise HTTPException(status_code=409, detail="该任务已有有效执行租约。")
    fence = int(newest["fencing_token"]) + 1 if newest else 1
    conn.execute("UPDATE agent_task_attempts SET status = 'superseded', updated_at = ?, finished_at = ? WHERE task_id = ? AND status = 'running'", (timestamp, timestamp, int(task_id)))
    revoke_task_delegations(conn, task_id=task_id, reason="attempt_superseded", now=timestamp)
    identifier = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO agent_task_attempts (id, task_id, actor_role, actor_id, fencing_token, worker_id, startup_key, status, lease_expires_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
        (identifier, int(task_id), actor.role, actor.id, fence, worker_id, startup_key, timestamp + lease_seconds, timestamp, timestamp),
    )
    return dict(conn.execute("SELECT * FROM agent_task_attempts WHERE id = ?", (identifier,)).fetchone())


def renew_task_attempt(conn, *, task_id: int, attempt_id: str, fencing_token: int, worker_id: str, lease_seconds: int = 60, now: int | None = None) -> dict[str, Any]:
    timestamp = _now(now)
    lease_seconds = _duration(lease_seconds, MAX_LEASE_SECONDS)
    _, _, attempt = assert_current_attempt(conn, task_id=task_id, attempt_id=attempt_id, fencing_token=fencing_token, now=timestamp, lock_task=True)
    if attempt["worker_id"] != worker_id:
        raise HTTPException(status_code=409, detail="只有当前执行器可以续租。")
    conn.execute("UPDATE agent_task_attempts SET lease_expires_at = ?, updated_at = ? WHERE id = ? AND fencing_token = ? AND status = 'running'", (timestamp + lease_seconds, timestamp, attempt_id, int(fencing_token)))
    return dict(conn.execute("SELECT * FROM agent_task_attempts WHERE id = ?", (attempt_id,)).fetchone())


def finish_task_attempt(conn, *, attempt_id: str, fencing_token: int, status: str, now: int | None = None) -> None:
    if status not in {"completed", "failed", "canceled"}:
        raise HTTPException(status_code=400, detail="无效的执行结束状态。")
    timestamp = _now(now)
    row = conn.execute("SELECT * FROM agent_task_attempts WHERE id = ?", (str(attempt_id),)).fetchone()
    if not row or int(row["fencing_token"]) != int(fencing_token):
        raise HTTPException(status_code=409, detail="执行记录与租约不匹配。")
    # Cleanup is allowed after task cancellation/deletion; it grants no access.
    conn.execute("UPDATE agent_tasks SET status = status WHERE id = ?", (int(row["task_id"]),))
    row = conn.execute("SELECT * FROM agent_task_attempts WHERE id = ?", (str(attempt_id),)).fetchone()
    current = conn.execute("SELECT MAX(fencing_token) AS fence FROM agent_task_attempts WHERE task_id = ?", (int(row["task_id"]),)).fetchone()
    if int(current["fence"]) != int(fencing_token):
        raise HTTPException(status_code=409, detail="旧执行器不能结束新的执行。")
    if row["status"] not in {"running", status}:
        raise HTTPException(status_code=409, detail="执行记录已进入其他结束状态。")
    conn.execute("UPDATE agent_task_attempts SET status = ?, updated_at = ?, finished_at = ? WHERE id = ? AND fencing_token = ? AND status = 'running'", (status, timestamp, timestamp, attempt_id, int(fencing_token)))
    conn.execute("UPDATE agent_task_delegations SET status = 'revoked', revoked_at = ?, revoke_reason = ? WHERE attempt_id = ? AND status = 'active'", (timestamp, "attempt_" + status, attempt_id))


def create_persistent_authorization(conn, *, actor_role: str, actor_id: int, source_session_id: str, scopes: list[str], intent_reference: str, ttl_seconds: int, now: int | None = None) -> dict[str, Any]:
    """Record an already-consented platform rule/subscription, not model consent."""
    timestamp = _now(now)
    actor = resolve_agent_actor(conn, actor_role, actor_id)
    from .account_credentials_service import lock_actor_authorization_transition
    lock_actor_authorization_transition(conn, role=actor.role, user_id=actor.id)
    timestamp = _now(now)
    actor = resolve_agent_actor(conn, actor_role, actor_id)
    session_hash = _hash(_text(source_session_id, "source_session_id", 512))
    _assert_session(conn, actor, session_hash, timestamp)
    scope_list = _scopes(scopes)
    identifier = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO agent_persistent_authorizations (id, actor_role, actor_id, authority_fingerprint, scopes_json, intent_reference, source_session_hash, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (identifier, actor.role, actor.id, actor.authority_fingerprint, json.dumps(scope_list), _text(intent_reference, "intent_reference"), session_hash, timestamp, timestamp + _duration(ttl_seconds, MAX_PERSISTENT_TTL_SECONDS)),
    )
    return dict(conn.execute("SELECT * FROM agent_persistent_authorizations WHERE id = ?", (identifier,)).fetchone())


def _assert_persistent(conn, *, identifier: str, actor: AgentActor, scopes: list[str], now: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_persistent_authorizations WHERE id = ? LIMIT 1", (identifier,)).fetchone()
    result = dict(row) if row else {}
    if not result or result.get("status") != "active" or int(result.get("expires_at") or 0) <= now or not _actor_matches(result, actor):
        raise HTTPException(status_code=401, detail="持续授权已失效或不属于任务主体。")
    if not hmac.compare_digest(result["authority_fingerprint"], actor.authority_fingerprint):
        raise HTTPException(status_code=401, detail="账号权限已变化，请重新确认持续授权。")
    if not set(scopes).issubset(_stored_scopes(result)):
        raise HTTPException(status_code=403, detail="任务超出持续授权范围。")
    return result


def revoke_persistent_authorization(conn, *, authorization_id: str, actor_role: str, actor_id: int, reason: str = "user_revoked", now: int | None = None) -> None:
    timestamp = _now(now)
    actor = resolve_agent_actor(conn, actor_role, actor_id)
    row = conn.execute("SELECT * FROM agent_persistent_authorizations WHERE id = ?", (authorization_id,)).fetchone()
    if not row or not _actor_matches(dict(row), actor):
        raise HTTPException(status_code=403, detail="不能撤销其他主体的持续授权。")
    reason = _text(reason, "reason")
    conn.execute("UPDATE agent_persistent_authorizations SET status = 'revoked', revoked_at = ?, revoke_reason = ? WHERE id = ? AND status = 'active'", (timestamp, reason, authorization_id))
    conn.execute("UPDATE agent_task_delegations SET status = 'revoked', revoked_at = ?, revoke_reason = ? WHERE persistent_authorization_id = ? AND status = 'active'", (timestamp, reason, authorization_id))


def issue_task_delegation(conn, *, task_id: int, attempt_id: str, fencing_token: int, purpose: str, scopes: list[str], source_session_id: str | None = None, source_session_hash: str | None = None, source_session_key: str | None = None, persistent_authorization_id: str | None = None, ttl_seconds: int = 900, now: int | None = None) -> dict[str, Any]:
    timestamp = _now(now)
    if purpose not in {"tools", "model"}:
        raise HTTPException(status_code=400, detail="委托用途无效。")
    if sum(bool(item) for item in (source_session_id, source_session_hash, persistent_authorization_id)) != 1:
        raise HTTPException(status_code=400, detail="委托必须绑定登录会话或持续授权中的一种。")
    scope_list = _scopes(scopes)
    expires_at = timestamp + _duration(ttl_seconds, MAX_DELEGATION_TTL_SECONDS)
    _, actor, _ = assert_current_attempt(conn, task_id=task_id, attempt_id=attempt_id, fencing_token=fencing_token, now=timestamp, lock_task=True)
    from .account_credentials_service import lock_actor_authorization_transition
    lock_actor_authorization_transition(conn, role=actor.role, user_id=actor.id)
    timestamp = _now(now)
    expires_at = timestamp + _duration(ttl_seconds, MAX_DELEGATION_TTL_SECONDS)
    _, actor, _ = assert_current_attempt(conn, task_id=task_id, attempt_id=attempt_id, fencing_token=fencing_token, now=timestamp)
    if source_session_key is not None and (source_session_key != actor.key or persistent_authorization_id):
        raise HTTPException(status_code=401, detail="源登录会话与任务身份不一致。")
    session_hash = None
    if source_session_id or source_session_hash:
        if source_session_hash:
            if not isinstance(source_session_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_session_hash):
                raise HTTPException(status_code=400, detail="源登录会话摘要无效。")
            session_hash = source_session_hash
        else:
            session_hash = _hash(_text(source_session_id, "source_session_id", 512))
        _assert_session(conn, actor, session_hash, timestamp)
    else:
        persistent = _assert_persistent(conn, identifier=str(persistent_authorization_id), actor=actor, scopes=scope_list, now=timestamp)
        expires_at = min(expires_at, int(persistent["expires_at"]))
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    identifier = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO agent_task_delegations (id, token_hash, task_id, attempt_id, fencing_token, actor_role, actor_id, authority_fingerprint, purpose, scopes_json, source_session_hash, persistent_authorization_id, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (identifier, _hash(token), int(task_id), attempt_id, int(fencing_token), actor.role, actor.id, actor.authority_fingerprint, purpose, json.dumps(scope_list), session_hash, persistent_authorization_id, timestamp, expires_at),
    )
    # The raw credential is returned exactly here; no logging/persistence helper
    # receives it. Callers pass it directly to the isolated runner/control plane.
    return {"id": identifier, "token": token, "task_id": int(task_id), "attempt_id": attempt_id, "fencing_token": int(fencing_token), "purpose": purpose, "scopes": scope_list, "expires_at": expires_at}


def verify_task_delegation(conn, token: str, *, purpose: str, required_scope: str | None = None, lock_task: bool = False, now: int | None = None) -> VerifiedDelegation:
    timestamp = _now(now)
    if purpose not in {"tools", "model"} or not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or not 40 <= len(token) <= 160:
        raise HTTPException(status_code=401, detail="任务委托无效。")
    row = conn.execute("SELECT * FROM agent_task_delegations WHERE token_hash = ? LIMIT 1", (_hash(token),)).fetchone()
    return _verify_delegation_record(conn, row, purpose=purpose, required_scope=required_scope, lock_task=lock_task, timestamp=timestamp)


def verify_stored_task_delegation(conn, delegation_id: str, *, purpose: str, required_scope: str | None = None,
                                  lock_task: bool = False, now: int | None = None) -> VerifiedDelegation:
    """Recheck a server-owned delegation reference without storing raw tokens.

    Internal only: the caller must authenticate the current user and establish
    ownership of the stored question/reference first. An external bearer token
    is never replaced by knowing this id. All live grant checks are identical.
    """
    if purpose not in {"tools", "model"}:
        raise HTTPException(status_code=401, detail="任务委托无效。")
    identifier = _text(delegation_id, "delegation_id")
    row = conn.execute("SELECT * FROM agent_task_delegations WHERE id = ? LIMIT 1", (identifier,)).fetchone()
    return _verify_delegation_record(conn, row, purpose=purpose, required_scope=required_scope, lock_task=lock_task, timestamp=_now(now))


def _verify_delegation_record(conn, row, *, purpose, required_scope, lock_task, timestamp):
    delegation = dict(row) if row else {}
    if not delegation or delegation.get("status") != "active" or delegation.get("purpose") != purpose or int(delegation.get("expires_at") or 0) <= timestamp:
        raise HTTPException(status_code=401, detail="任务委托无效、已过期或已撤销。")
    task, actor, attempt = assert_current_attempt(conn, task_id=delegation["task_id"], attempt_id=delegation["attempt_id"], fencing_token=delegation["fencing_token"], now=timestamp, lock_task=lock_task)
    if lock_task:
        cursor = conn.execute("UPDATE agent_task_delegations SET status = status WHERE id = ? AND status = 'active' AND expires_at > ?", (delegation["id"], timestamp))
        if cursor.rowcount != 1:
            raise HTTPException(status_code=401, detail="任务委托已撤销。")
    if not _actor_matches(delegation, actor) or not hmac.compare_digest(delegation["authority_fingerprint"], actor.authority_fingerprint):
        raise HTTPException(status_code=401, detail="账号身份或权限已变化，任务委托已失效。")
    scopes = _stored_scopes(delegation)
    if required_scope is not None and required_scope not in scopes:
        raise HTTPException(status_code=403, detail="当前委托未授权此能力。")
    if delegation.get("source_session_hash") and not delegation.get("persistent_authorization_id"):
        _assert_session(conn, actor, delegation["source_session_hash"], timestamp)
    elif delegation.get("persistent_authorization_id") and not delegation.get("source_session_hash"):
        _assert_persistent(conn, identifier=delegation["persistent_authorization_id"], actor=actor, scopes=scopes, now=timestamp)
    else:
        raise HTTPException(status_code=401, detail="任务委托缺少有效授权来源。")
    return VerifiedDelegation(actor=actor, task=task, attempt=attempt, delegation=delegation)


def revoke_task_delegations(conn, *, task_id: int, reason: str = "task_stopped", now: int | None = None) -> int:
    cursor = conn.execute("UPDATE agent_task_delegations SET status = 'revoked', revoked_at = ?, revoke_reason = ? WHERE task_id = ? AND status = 'active'", (_now(now), _text(reason, "reason"), int(task_id)))
    return int(cursor.rowcount)
