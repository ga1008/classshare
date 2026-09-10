"""Explicit administrator account operations with atomic authority transitions.

There is no password input, arbitrary SQL, callback or generic Web-write proxy.
The normal teacher-account services remain the business rule authority. Only
this module's registered self-authority changes may finish using the authority
held at transaction entry; the general operation ledger stays fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
import uuid
from typing import Any

from fastapi import HTTPException


_ID = {"type": "int", "required": True}
_REVISION = {"type": "str", "required": True, "max_chars": 64}
_ORG_FIELDS = {key: {"type": "str", "max_chars": 200} for key in ("school_code", "school_name", "college", "department")}
_BASE = {"teacher_id": _ID, "expected_revision": _REVISION}


def _definition(label, fields, description):
    return {"label": label, "done_label": label + "已保存", "risk": "high", "execution_mode": "execute",
            "roles": ["teacher"], "requires_super_admin": True, "fields": {**_BASE, **fields}, "description": description}


IDENTITY_ACTION_DEFINITIONS = {
    "manage_teacher_account": _definition("修改教师资料", {
        "name": {"type": "str", "max_chars": 200}, "email": {"type": "str", "max_chars": 320},
        **{key: {"type": "str", "max_chars": 200} for key in ("phone", "wechat", "qq", "homepage_url")},
        "description": {"type": "text", "max_chars": 5000}, **_ORG_FIELDS,
    }, "按正常超管权限修改教师资料与主组织归属；先读取 identity.teacher 获得 expected_revision。未传字段保留原值。"),
    "upsert_teacher_membership": _definition("保存教师任教归属", {**_ORG_FIELDS, "is_primary": {"type": "int", "minimum": 0, "maximum": 1}},
        "按正常组织目录规则新增或恢复学校任教归属；is_primary 只接受0或1，同学校规则与普通Web相同。"),
    "set_teacher_primary_membership": _definition("设置默认任教归属", {"membership_id": _ID}, "选择现有启用任教归属作为默认组织。"),
    "deactivate_teacher_membership": _definition("停用任教归属", {"membership_id": _ID}, "停用指定归属，正常业务要求至少保留一个启用归属。"),
    "deactivate_teacher_account": _definition("停用教师账户", {}, "停用登录并保留历史数据；不得停用自己或最后一名启用超管。"),
    "grant_teacher_super_admin": _definition("授予教师超管权限", {}, "向启用教师授予普通Web支持的超管权限。"),
    "revoke_teacher_super_admin": _definition("撤销教师超管权限", {}, "保留至少一名启用超管；撤销本人权限会提交回执后注销本人会话并停止本次Agent。"),
}
IDENTITY_TRANSACTIONAL_ACTIONS = frozenset(IDENTITY_ACTION_DEFINITIONS)
_SELF_AUTHORITY_ACTIONS = IDENTITY_TRANSACTIONAL_ACTIONS - {"deactivate_teacher_account", "grant_teacher_super_admin"}
_ORGANIZATION_AUTHORITY_ACTIONS = frozenset({"update_organization_college", "update_organization_department"})
_SELF_AUTHORITY_ACTIONS = _SELF_AUTHORITY_ACTIONS | _ORGANIZATION_AUTHORITY_ACTIONS
IDENTITY_READ_KEYS = frozenset({"identity.teachers", "identity.teacher", "identity.memberships"})


def identity_read_catalog():
    return [
        {"key": "identity.teachers", "title": "管理员教师账号列表", "parameters": {
            "q": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000}}},
        {"key": "identity.teacher", "title": "教师账号与当前变更版本", "parameters": {"teacher_id": {"type": "integer", "minimum": 1, "required": True}}},
        {"key": "identity.memberships", "title": "教师全部任教组织归属", "parameters": {"teacher_id": {"type": "integer", "minimum": 1, "required": True}}},
    ]


def _revision(teacher):
    return hashlib.sha256(json.dumps(teacher, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _admin_grant(conn, token, *, write=False, lock_task=False):
    from .agent_delegation_service import verify_task_delegation
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope="platform:write" if write else "platform:read", lock_task=lock_task)
    if grant.actor.role != "teacher" or not grant.actor.is_super_admin:
        raise HTTPException(403, "当前账号没有教师账户管理权限。")
    return grant


def _positive(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise HTTPException(400, "账号或成员编号无效。")
    return value


def _teacher(conn, teacher_id):
    from .teacher_account_service import get_teacher_account
    try:
        return get_teacher_account(conn, teacher_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


def read_identity_management(conn, token, operation_key, params=None):
    """Bounded domain read for Web account pages without a JSON list endpoint."""
    _admin_grant(conn, token)
    params = {} if params is None else params
    if operation_key not in IDENTITY_READ_KEYS or not isinstance(params, dict):
        raise HTTPException(400, "账号读取能力或参数无效。")
    if operation_key == "identity.teachers":
        if set(params) - {"q", "limit", "offset"}:
            raise HTTPException(400, "账号列表包含未知参数。")
        query, limit, offset = params.get("q", ""), params.get("limit", 20), params.get("offset", 0)
        if (not isinstance(query, str) or len(query) > 200 or any(0xD800 <= ord(c) <= 0xDFFF for c in query)
                or type(limit) is not int or not 1 <= limit <= 50
                or type(offset) is not int or not 0 <= offset <= 10000):
            raise HTTPException(400, "账号列表范围无效。")
        # IDs first; use the ordinary serializer for the bounded result page.
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute("SELECT id FROM teachers WHERE lower(name) LIKE lower(?) ESCAPE '\\' OR lower(email) LIKE lower(?) ESCAPE '\\' ORDER BY id LIMIT ? OFFSET ?",
                            (pattern, pattern, limit + 1, offset)).fetchall()
        items = [_teacher(conn, int(row["id"])) for row in rows[:limit]]
        return {"items": [{"teacher": teacher, "revision": _revision(teacher)} for teacher in items], "has_more": len(rows) > limit,
                "next_offset": offset + limit if len(rows) > limit else None}
    if set(params) != {"teacher_id"}:
        raise HTTPException(400, "请提供明确教师编号。")
    teacher = _teacher(conn, _positive(params["teacher_id"]))
    if operation_key == "identity.memberships":
        return {"teacher_id": teacher["id"], "memberships": teacher["memberships"], "revision": _revision(teacher)}
    return {"teacher": teacher, "revision": _revision(teacher)}


def _normalize(action, params):
    if action not in IDENTITY_TRANSACTIONAL_ACTIONS or not isinstance(params, dict):
        raise HTTPException(400, "未注册的账号操作。")
    fields = IDENTITY_ACTION_DEFINITIONS[action]["fields"]
    if set(params) - set(fields):
        raise HTTPException(400, "账号操作包含未知参数；密码必须使用用户安全输入流程。")
    clean = {}
    for key, spec in fields.items():
        if key not in params:
            if spec.get("required"):
                raise HTTPException(400, f"缺少 {key}。")
            continue
        value = params[key]
        if key == "is_primary":
            if type(value) is not int or value not in (0, 1):
                raise HTTPException(400, "is_primary 仅接受0或1。")
        elif spec["type"] == "int":
            _positive(value)
        elif not isinstance(value, str) or len(value) > spec.get("max_chars", 200):
            raise HTTPException(400, f"{key} 长度或类型无效。")
        elif any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise HTTPException(400, "字段包含无效字符。")
        clean[key] = value
    if len(clean["expected_revision"]) != 64 or any(c not in "0123456789abcdef" for c in clean["expected_revision"]):
        raise HTTPException(400, "请先读取当前账号版本。")
    if action == "manage_teacher_account" and set(clean) == set(_BASE):
        raise HTTPException(400, "请提供要修改的资料字段。")
    return clean


@dataclass
class _TransitionProof:
    connection: Any
    grant: Any
    operation_id: str
    record_id: str
    action: str
    params_hash: str
    savepoint: str
    used: bool = False


@dataclass
class _UserTransitionProof:
    connection: Any
    actor: Any
    task_id: int
    session_hash: str
    operation_id: str
    record_id: str
    action: str
    params_hash: str
    savepoint: str
    used: bool = False


def _release_transition_savepoint(conn, proof):
    # The marker disappears if a caller commits between business and receipt.
    # RELEASE never commits the enclosing ledger/business transaction.
    try:
        conn.execute("RELEASE SAVEPOINT " + proof.savepoint)
    except Exception:
        raise HTTPException(409, "权限变更必须与业务回执在同一事务内完成。") from None
    proof.used = True


def _revoke_changed_authority(conn, *, task_id, operation_id, action, actor_id, self_changed, affected_teacher_ids):
    targets = {_positive(item) for item in affected_teacher_ids}
    if self_changed:
        targets.add(actor_id)
    now = int(time.time())
    for target in sorted(targets):
        conn.execute("UPDATE agent_task_delegations SET status='revoked',revoked_at=?,revoke_reason='account_authority_changed' WHERE actor_role='teacher' AND actor_id=? AND status='active'",
                     (now, target))
        conn.execute("UPDATE agent_persistent_authorizations SET status='revoked',revoked_at=?,revoke_reason='account_authority_changed' WHERE actor_role='teacher' AND actor_id=? AND status='active'",
                     (now, target))
    from .agent_task_service import append_task_event
    append_task_event(conn, task_id, "account_operation_committed", "账号管理操作已取得事务回执。", {
        "operation_id": operation_id, "action": action, "affected_teacher_ids": sorted(targets), "agent_stop_required": self_changed}, commit=False)


def _finish_self_transition(conn, proof, *, expected_fingerprint, result):
    """Internal single-transaction exception for an exact registered mutation.

    Task, source session, lease, fence, delegation and operation all remain
    valid and locked. Only the fingerprint change caused by this adapter may
    differ. No caller-provided boolean or bearer grants this exception.
    """
    from .agent_actor_service import resolve_agent_actor
    from .agent_delegation_service import _assert_session
    from .agent_operation_service import MAX_RECEIPT_BYTES, _canonical, _receipt
    grant, now = proof.grant, int(time.time())
    if proof.connection is not conn or proof.action not in _SELF_AUTHORITY_ACTIONS:
        raise HTTPException(403, "账号变更收尾证明无效。")
    current = resolve_agent_actor(conn, grant.actor.role, grant.actor.id)
    if current.authority_fingerprint != expected_fingerprint:
        raise HTTPException(409, "账号权限发生了其他变化。")
    _assert_session(conn, grant.actor, grant.delegation["source_session_hash"], now)
    row = conn.execute("""SELECT d.id FROM agent_task_delegations d
        JOIN agent_task_attempts a ON a.id=d.attempt_id AND a.fencing_token=d.fencing_token
        JOIN agent_tasks t ON t.id=d.task_id
        WHERE d.id=? AND d.status='active' AND d.expires_at>? AND a.status='running' AND a.lease_expires_at>?
          AND t.status='running' AND (t.cancel_requested_at IS NULL OR t.cancel_requested_at='')
          AND a.fencing_token=(SELECT MAX(a2.fencing_token) FROM agent_task_attempts a2 WHERE a2.task_id=t.id)""",
                       (grant.delegation["id"], now, now)).fetchone()
    if not row:
        raise HTTPException(401, "账号变更期间执行授权已停止。")
    cursor = conn.execute("""UPDATE agent_action_executions SET status='completed',result_json=?,completed_at=?,updated_at=?
        WHERE id=? AND operation_id=? AND action=? AND params_hash=? AND status='executing'
          AND task_id=? AND actor_role=? AND actor_id=? AND attempt_id=? AND fencing_token=? AND delegation_id=?""",
        (_canonical(result, maximum=MAX_RECEIPT_BYTES), now, now, proof.record_id, proof.operation_id, proof.action, proof.params_hash,
         grant.task["id"], grant.actor.role, grant.actor.id, grant.attempt["id"], grant.attempt["fencing_token"], grant.delegation["id"]))
    if cursor.rowcount != 1:
        raise HTTPException(409, "账号变更回执已变化。")
    return _receipt(conn.execute("SELECT * FROM agent_action_executions WHERE id=?", (proof.record_id,)).fetchone())


def begin_authority_changing_operation(conn, token, operation_id, action, params):
    """Internal adapter API. Call before acquiring any domain/task locks."""
    from . import teacher_account_service as domain
    from .agent_delegation_service import _assert_session
    from .agent_operation_service import claim_agent_operation
    if action not in IDENTITY_TRANSACTIONAL_ACTIONS | _ORGANIZATION_AUTHORITY_ACTIONS:
        raise HTTPException(403, "该操作未登记权限变更事务。")
    _admin_grant(conn, token, write=True)
    domain.lock_teacher_account_management(conn)
    grant = _admin_grant(conn, token, write=True, lock_task=True)
    if not grant.delegation.get("source_session_hash"):
        raise HTTPException(403, "账号权限变更需要当前用户登录会话授权。")
    conn.execute("UPDATE user_sessions SET expires_at=expires_at WHERE session_user_key=?", (grant.actor.key,))
    _assert_session(conn, grant.actor, grant.delegation["source_session_hash"], int(time.time()))
    claimed = claim_agent_operation(conn, token=token, operation_id=operation_id, action=action, params=params, required_scope="platform:write")
    operation = claimed["operation"]
    proof = None
    if claimed["claimed"]:
        savepoint = "agent_identity_" + uuid.uuid4().hex
        conn.execute("SAVEPOINT " + savepoint)
        proof = _TransitionProof(conn, grant, operation_id, operation["id"], action, operation["params_hash"], savepoint)
    return {**claimed, "proof": proof}


def begin_user_authority_changing_operation(conn, *, user, source_session_id, task_id, operation_id, action, params):
    """Fresh owner-confirmed terminal proposal; never restores runner authority.

    The authenticated HTTP caller must already verify the concrete proposal and
    preview. Only this fixed administrator adapter set can use the proof, and
    the original source session, task owner and transaction remain bound.
    """
    from . import teacher_account_service as domain
    from .agent_actor_service import resolve_agent_actor
    from .agent_delegation_service import _assert_session
    from .agent_operation_service import _fresh_user_source, claim_user_agent_operation
    if action not in IDENTITY_TRANSACTIONAL_ACTIONS | _ORGANIZATION_AUTHORITY_ACTIONS:
        raise HTTPException(403, "该操作未登记权限变更事务。")
    if not isinstance(user, dict) or user.get("role") != "teacher":
        raise HTTPException(403, "当前账号没有教师账户管理权限。")
    actor = resolve_agent_actor(conn, "teacher", user.get("id"))
    if not actor.is_super_admin:
        raise HTTPException(403, "当前账号没有教师账户管理权限。")
    domain.lock_teacher_account_management(conn)
    task, actor, session_hash = _fresh_user_source(conn, user=user, source_session_id=source_session_id,
                                                 task_id=task_id, now=int(time.time()))
    if not actor.is_super_admin:
        raise HTTPException(403, "当前账号没有教师账户管理权限。")
    conn.execute("UPDATE user_sessions SET expires_at=expires_at WHERE session_user_key=?", (actor.key,))
    _assert_session(conn, actor, session_hash, int(time.time()))
    claimed = claim_user_agent_operation(conn, user=user, source_session_id=source_session_id, task_id=task["id"],
                                         operation_id=operation_id, action=action, params=params)
    proof = None
    if claimed["claimed"]:
        operation = claimed["operation"]
        savepoint = "agent_identity_" + uuid.uuid4().hex
        conn.execute("SAVEPOINT " + savepoint)
        proof = _UserTransitionProof(conn, actor, int(task["id"]), session_hash, operation_id,
                                     operation["id"], action, operation["params_hash"], savepoint)
    return {**claimed, "proof": proof}


def complete_user_authority_changing_operation(conn, proof, result, *, affected_teacher_ids=()):
    """One-use transaction proof for a fresh signed-in user's own proposal."""
    from .agent_actor_service import resolve_agent_actor, task_actor_identity
    from .agent_delegation_service import _assert_session
    from .agent_operation_service import MAX_RECEIPT_BYTES, _canonical, _receipt
    if (not isinstance(proof, _UserTransitionProof) or proof.connection is not conn or proof.used
            or proof.action not in IDENTITY_TRANSACTIONAL_ACTIONS | _ORGANIZATION_AUTHORITY_ACTIONS):
        raise HTTPException(403, "权限变更收尾证明无效或已使用。")
    _release_transition_savepoint(conn, proof)
    actor, now = resolve_agent_actor(conn, proof.actor.role, proof.actor.id), int(time.time())
    _assert_session(conn, actor, proof.session_hash, now)
    task = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (proof.task_id,)).fetchone()
    if (not task or task["status"] not in {"completed", "failed", "canceled"}
            or task_actor_identity(dict(task)) != (actor.role, actor.id)):
        raise HTTPException(409, "待确认操作的任务归属或状态已变化。")
    self_changed = actor.authority_fingerprint != proof.actor.authority_fingerprint
    if self_changed and proof.action not in _SELF_AUTHORITY_ACTIONS:
        raise HTTPException(403, "该操作不允许改变当前执行人的权限。")
    result = {**result, "authority_transition": self_changed, "agent_stop_required": self_changed}
    cursor = conn.execute("""UPDATE agent_action_executions SET status='completed',result_json=?,completed_at=?,updated_at=?
        WHERE id=? AND operation_id=? AND action=? AND params_hash=? AND status='executing'
          AND task_id=? AND actor_role=? AND actor_id=? AND source_kind='user_confirmation'
          AND source_session_hash=? AND authority_fingerprint=? AND attempt_id IS NULL AND delegation_id IS NULL""",
        (_canonical(result, maximum=MAX_RECEIPT_BYTES), now, now, proof.record_id, proof.operation_id, proof.action, proof.params_hash,
         proof.task_id, actor.role, actor.id, proof.session_hash, proof.actor.authority_fingerprint))
    if cursor.rowcount != 1:
        raise HTTPException(409, "账号变更回执已变化。")
    _revoke_changed_authority(conn, task_id=proof.task_id, operation_id=proof.operation_id, action=proof.action,
                              actor_id=actor.id, self_changed=self_changed, affected_teacher_ids=affected_teacher_ids)
    return _receipt(conn.execute("SELECT * FROM agent_action_executions WHERE id=?", (proof.record_id,)).fetchone())


def complete_authority_changing_operation(conn, proof, result, *, affected_teacher_ids=()):
    """Finish only the transaction admitted by begin; never broadens tokens.

    Domain adapters supply IDs whose authority changed. Other actors' task rows
    are not locked; revoked credentials and live fingerprints stop their next
    request. For the caller itself, the result explicitly requests a stop.
    """
    from .agent_actor_service import resolve_agent_actor
    from .agent_operation_service import _receipt
    if (not isinstance(proof, _TransitionProof) or proof.connection is not conn or proof.used
            or proof.action not in IDENTITY_TRANSACTIONAL_ACTIONS | _ORGANIZATION_AUTHORITY_ACTIONS):
        raise HTTPException(403, "权限变更收尾证明无效或已使用。")
    _release_transition_savepoint(conn, proof)
    grant = proof.grant
    actor = resolve_agent_actor(conn, grant.actor.role, grant.actor.id)
    self_changed = actor.authority_fingerprint != grant.actor.authority_fingerprint
    result = {**result, "authority_transition": self_changed, "agent_stop_required": self_changed}
    if self_changed:
        receipt = _finish_self_transition(conn, proof, expected_fingerprint=actor.authority_fingerprint, result=result)
    else:
        # Reuse exactly the ordinary validation chain without keeping raw token
        # in the proof. It is a server-owned reference, not an HTTP bearer.
        from .agent_delegation_service import verify_stored_task_delegation
        from .agent_operation_service import MAX_RECEIPT_BYTES, _canonical
        verify_stored_task_delegation(conn, grant.delegation["id"], purpose="tools", required_scope="platform:write", lock_task=True)
        now = int(time.time())
        cursor = conn.execute("""UPDATE agent_action_executions SET status='completed',result_json=?,completed_at=?,updated_at=?
            WHERE id=? AND operation_id=? AND action=? AND params_hash=? AND status='executing'
              AND task_id=? AND actor_role=? AND actor_id=? AND attempt_id=? AND fencing_token=? AND delegation_id=?""",
            (_canonical(result, maximum=MAX_RECEIPT_BYTES), now, now, proof.record_id, proof.operation_id, proof.action, proof.params_hash,
             grant.task["id"], actor.role, actor.id, grant.attempt["id"], grant.attempt["fencing_token"], grant.delegation["id"]))
        if cursor.rowcount != 1:
            raise HTTPException(409, "账号操作回执已变化。")
        receipt = _receipt(conn.execute("SELECT * FROM agent_action_executions WHERE id=?", (proof.record_id,)).fetchone())
    _revoke_changed_authority(conn, task_id=grant.task["id"], operation_id=proof.operation_id, action=proof.action,
                              actor_id=grant.actor.id, self_changed=self_changed, affected_teacher_ids=affected_teacher_ids)
    return receipt


def _execute(conn, actor, action, clean, before):
    from . import teacher_account_service as domain
    target = clean["teacher_id"]
    params = {key: value for key, value in clean.items() if key not in _BASE}
    if action == "manage_teacher_account":
        merged = {key: before.get(key, "") for key in IDENTITY_ACTION_DEFINITIONS[action]["fields"] if key not in _BASE}
        domain.update_teacher_account(conn, teacher_id=target, **{**merged, **params})
    elif action == "upsert_teacher_membership":
        domain.upsert_teacher_membership(conn, teacher_id=target, actor_teacher_id=actor.id, **params)
    elif action == "set_teacher_primary_membership":
        domain.set_teacher_primary_membership(conn, teacher_id=target, **params)
    elif action == "deactivate_teacher_membership":
        domain.deactivate_teacher_membership(conn, teacher_id=target, actor_teacher_id=actor.id, **params)
    elif action == "deactivate_teacher_account":
        domain.deactivate_teacher_account(conn, teacher_id=target, actor_teacher_id=actor.id)
    elif action == "grant_teacher_super_admin":
        domain.grant_teacher_super_admin(conn, teacher_id=target)
    elif action == "revoke_teacher_super_admin":
        domain.revoke_teacher_super_admin(conn, teacher_id=target)
    return _teacher(conn, target)


def _dispatch_claimed_identity(conn, claimed, operation_id, action, clean, *, fresh_user=False):
    from .agent_actor_service import resolve_agent_actor
    operation = claimed["operation"]
    if not claimed["claimed"]:
        if operation["status"] != "completed":
            raise HTTPException(409, "账号操作尚无确定回执，不能重复执行。")
        return {"operation_id": operation_id, "replayed": True, "result": operation["result"]}
    before = _teacher(conn, clean["teacher_id"])
    if _revision(before) != clean["expected_revision"]:
        raise HTTPException(409, "账号或组织归属已变化，请重新读取后提交。")
    proof = claimed["proof"]
    actor = proof.actor if fresh_user else proof.grant.actor
    old_authority = resolve_agent_actor(conn, "teacher", clean["teacher_id"]).authority_fingerprint if before["is_active"] else None
    try:
        after = _execute(conn, actor, action, clean, before)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    new_authority = resolve_agent_actor(conn, "teacher", clean["teacher_id"]).authority_fingerprint if after["is_active"] else None
    result = {"url": "/manage/system/teachers", "label": IDENTITY_ACTION_DEFINITIONS[action]["done_label"], "ref_id": clean["teacher_id"],
              "teacher": after, "revision": _revision(after)}
    complete = complete_user_authority_changing_operation if fresh_user else complete_authority_changing_operation
    receipt = complete(conn, proof, result,
        affected_teacher_ids=[clean["teacher_id"]] if old_authority != new_authority else [])
    result = receipt["result"]
    if action == "deactivate_teacher_account" or (result["authority_transition"] and action == "revoke_teacher_super_admin"):
        from ..dependencies import invalidate_session_for_user
        invalidate_session_for_user(str(clean["teacher_id"]), "teacher", conn=conn)
    return {"operation_id": operation_id, "replayed": False, "result": result}


def dispatch_identity_write(conn, token, operation_id, action, params):
    clean = _normalize(action, params)
    claimed = begin_authority_changing_operation(conn, token, operation_id, action, clean)
    return _dispatch_claimed_identity(conn, claimed, operation_id, action, clean)


def dispatch_user_identity_write(conn, *, user, source_session_id, task_id, operation_id, action, params):
    clean = _normalize(action, params)
    claimed = begin_user_authority_changing_operation(conn, user=user, source_session_id=source_session_id,
        task_id=task_id, operation_id=operation_id, action=action, params=clean)
    return _dispatch_claimed_identity(conn, claimed, operation_id, action, clean, fresh_user=True)
