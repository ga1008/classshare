"""User-only credential input on reviewed proposals; passwords never reach DSH."""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException

_IDENTITY = {"teacher_id": {"type": "int", "required": True},
             "expected_revision": {"type": "str", "required": True, "max_chars": 64}}
_PROFILE = {"name": {"type": "str", "required": True, "max_chars": 200},
            "email": {"type": "str", "required": True, "max_chars": 320},
            "is_super_admin": {"type": "bool", "required": True},
            **{name: {"type": "str", "max_chars": 200} for name in ("school_code", "school_name", "college", "department")}}


def _definition(label, fields, description):
    return {"label": label, "done_label": label + "已完成", "risk": "high", "execution_mode": "secure_input",
            "roles": ["teacher"], "requires_super_admin": True, "fields": fields, "description": description,
            "secure_fields": [{"name": "password", "label": "新口令", "type": "password", "required": True,
                               "min_length": 8, "max_length": 72}]}


SECURE_ACTION_DEFINITIONS = {
    "create_teacher_account_secure": _definition("创建教师账户", _PROFILE,
        "创建此前未注册邮箱的教师；公共字段可由Agent准备，密码只能在用户安全输入表单填写。请输出待确认提案，不能向模型询问密码。"),
    "restore_teacher_account_secure": _definition("恢复已停用教师账户", {**_IDENTITY, **_PROFILE},
        "恢复指定已停用教师，先读取identity.teacher的expected_revision，明确核对资料与管理员标志；密码由用户安全输入。"),
    "reset_teacher_password_secure": _definition("重置教师口令", _IDENTITY,
        "按正常超管权限重置启用教师口令并撤销其登录与Agent授权；先读取identity.teacher版本，密码由用户安全输入。"),
}


def secure_action_catalog(*, actor_role, is_super_admin):
    if actor_role != 'teacher' or not is_super_admin:
        return []
    return [{"action": key, **value} for key, value in SECURE_ACTION_DEFINITIONS.items()]


def _revoke_account_sessions(conn, teacher_id):
    from .account_credentials_service import credentials_changed

    credentials_changed(conn, role='teacher', user_id=teacher_id, invalidate_sessions=True)


def dispatch_user_secure_action(conn, *, user, source_session_id, task_id, operation_id, action, params, secure_inputs):
    from .agent_action_registry import _secret_key_bytes, validate_action_params
    from .agent_actor_service import resolve_agent_actor
    from .agent_identity_management_adapter import _revision
    from .agent_operation_service import claim_user_agent_operation, complete_user_agent_operation
    from . import teacher_account_service as domain

    if action not in SECURE_ACTION_DEFINITIONS:
        raise HTTPException(400, "未知的安全输入动作。")
    if not isinstance(secure_inputs, dict) or set(secure_inputs) != {'password'}:
        raise HTTPException(400, "请通过安全输入表单填写新口令。")
    password = secure_inputs['password']
    if (not isinstance(password, str) or not 8 <= len(password) <= 72
            or any(0xD800 <= ord(char) <= 0xDFFF or char == '\x00' for char in password)
            or len(password.encode()) > 72):
        raise HTTPException(400, "口令长度无效，请填写至少8位且不超过72字节的口令。")
    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors:
        raise HTTPException(400, '；'.join(errors[:4]))
    actor = resolve_agent_actor(conn, str(user.get('role') or ''), user.get('id'))
    if actor.role != 'teacher' or not actor.is_super_admin:
        raise HTTPException(403, "当前账号没有教师账户管理权限。")
    domain.lock_teacher_account_management(conn)
    if action != 'create_teacher_account_secure':
        from .account_credentials_service import prepare_credentials_change

        prepare_credentials_change(conn, role='teacher', user_id=clean['teacher_id'])
    # Bind retries to this secret without storing plaintext or an offline-
    # guessable password digest in task parameters or receipts.
    binding = '\0'.join((actor.key, str(task_id), operation_id, action, password)).encode()
    fingerprint = hmac.new(_secret_key_bytes(), b'agent-user-secret-v1\0' + binding, hashlib.sha256).hexdigest()
    claim = claim_user_agent_operation(conn, user=user, source_session_id=source_session_id, task_id=task_id,
        operation_id=operation_id, action=action, params={**clean, 'secure_input_fingerprint': fingerprint})
    actor = resolve_agent_actor(conn, actor.role, actor.id)
    if not actor.is_super_admin:
        raise HTTPException(403, "当前账号已失去教师账户管理权限。")
    if not claim['claimed']:
        if claim['operation']['status'] != 'completed':
            raise HTTPException(409, "该账户操作尚无确定结果，请先核对。")
        return {'operation_id': operation_id, 'replayed': True, 'result': claim['operation']['result']}
    before = None
    if action != 'create_teacher_account_secure':
        try:
            before = domain.get_teacher_account(conn, clean['teacher_id'])
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        if _revision(before) != clean['expected_revision']:
            raise HTTPException(409, "账户资料已变化，请重新读取并预览。")
    try:
        if action == 'reset_teacher_password_secure':
            after = domain.reset_teacher_password(conn, teacher_id=clean['teacher_id'], password=password)
        else:
            existing = domain._teacher_exists_by_email(conn, domain.normalize_teacher_email(clean['email']))
            if action == 'create_teacher_account_secure' and existing:
                raise HTTPException(409, "该邮箱已有账户，请明确核对现有账户或使用恢复操作。")
            if action == 'restore_teacher_account_secure' and (before['is_active'] or not existing or int(existing['id']) != int(before['id'])):
                raise HTTPException(409, "恢复对象必须仍是该邮箱对应的已停用账户。")
            after = domain.create_teacher_account(conn, actor_teacher_id=actor.id, password=password,
                **{key: value for key, value in clean.items() if key not in _IDENTITY})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    result = {'url': '/manage/system/teachers', 'label': SECURE_ACTION_DEFINITIONS[action]['done_label'],
              'ref_id': after['id'], 'teacher': after, 'revision': _revision(after),
              'requires_relogin': action == 'reset_teacher_password_secure' and int(after['id']) == actor.id}
    # Complete while the current source is still valid. Credential reset and
    # all session revocations then commit together with this public receipt.
    complete_user_agent_operation(conn, user=user, source_session_id=source_session_id, task_id=task_id,
                                   operation_id=operation_id, result=result)
    if action != 'create_teacher_account_secure':
        _revoke_account_sessions(conn, int(after['id']))
    return {'operation_id': operation_id, 'replayed': False, 'result': result}
