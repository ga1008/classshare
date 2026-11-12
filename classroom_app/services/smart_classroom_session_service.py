"""One encrypted school session per external account, shared by all workers.

Saved, verified credentials recover expired sessions automatically. The database
lease coalesces concurrent workers, and repeated failures have a persisted
cooldown. Callers decide whether a rejected operation is safe to replay; school
writes must always be read back first. No transaction spans network I/O.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..db import get_db_connection
from .academic_integration_service import (
    _secret_key_bytes, decrypt_academic_secret, encrypt_academic_secret,
)

LOGIN_LEASE_MS = 60_000
RECOVERY_COOLDOWN_MS = 60_000


class SmartClassroomSessionError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.verification_result: dict[str, Any] | None = None
        super().__init__(message or {
            "needs_teacher_reauth": "智慧课堂会话正在自动恢复；连续失效时会短暂等待后重试，本地点名不受影响。",
            "session_busy": "智慧课堂账号正在建立会话，请稍后重试。",
            "credential_changed": "智慧课堂账号配置已变更或停用，请重新确认当前账号与课堂同步授权。",
            "session_refreshed": "智慧课堂会话已自动恢复，正在回读上次操作结果后继续同步。",
            "captcha_required": "智慧课堂要求完成验证码验证，当前登录接口未提供可自动提交的验证码挑战；请在学校登录页完成验证后重试，本地记录已保留。",
            "credential_invalid": "智慧课堂未接受已保存的账号密码，请检查账号配置。",
        }.get(code, "智慧课堂会话暂不可用，请教师重新验证账号。"))


@dataclass(frozen=True)
class SchoolSession:
    external_account_key: str
    token_version: int
    token: str = field(repr=False)
    login_result: dict[str, Any] = field(default_factory=dict)


def _now_ms() -> int:
    return int(time.time() * 1000)


def account_key(access: dict[str, Any]) -> str:
    platform = str(access.get("platform_code") or "gxufl_smart_classroom").strip().lower()
    username = re.sub(r"\s+", "", str(access.get("username") or ""))
    return hashlib.sha256(f"{platform}\0{username}".encode()).hexdigest()


def _fingerprint(access: dict[str, Any]) -> str:
    # A keyed digest prevents offline password guessing from a database dump.
    raw = f"smart-classroom-session\0{account_key(access)}\0{access.get('password') or ''}"
    return hmac.new(_secret_key_bytes(), raw.encode(), hashlib.sha256).hexdigest()


def _validate_saved_access(conn, access: dict[str, Any]):
    from .smart_classroom_integration_service import credential_generation
    credential_id = access.get("credential_id")
    if not credential_id or not access.get("credential_generation"):
        raise SmartClassroomSessionError("credential_changed")
    row = conn.execute("SELECT * FROM teacher_smart_classroom_credentials WHERE id=?",
                       (int(credential_id),)).fetchone()
    if (row is None or not row["enabled"] or row["last_status"] != "verified"
            or credential_generation(row) != access["credential_generation"]
            or account_key(dict(row)) != account_key(access)
            or not hmac.compare_digest(decrypt_academic_secret(row["password_encrypted"]).encode(),
                                       str(access.get("password") or "").encode())):
        raise SmartClassroomSessionError("credential_changed")
    return row


def grant_background_login(conn, access: dict[str, Any], scope: str, *, expires_at_ms: int) -> dict[str, Any]:
    """Retain legacy classroom authorization receipts for audit compatibility.

    Session recovery now uses verified saved credentials and the account lease;
    these receipts do not gate token recovery or authorize school mutations.
    """
    _validate_saved_access(conn, access)
    scope = str(scope or "").strip()
    if not scope or len(scope) > 200:
        raise ValueError("课堂同步授权范围无效。")
    now = _now_ms()
    if int(expires_at_ms) <= now:
        raise ValueError("课堂同步授权已过期。")
    expiry = min(int(expires_at_ms), now + 24 * 60 * 60 * 1000)
    key = account_key(access)
    grant_id = hashlib.sha256(
        f"{key}\0{access['credential_id']}\0{access['credential_generation']}\0{scope}".encode()).hexdigest()
    conn.execute("""INSERT INTO smart_classroom_session_grants
        (id,external_account_key,credential_id,credential_generation,scope_key,expires_at_ms,created_at_ms)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
        (grant_id, key, int(access["credential_id"]), access["credential_generation"], scope, expiry, now))
    return {"scope": scope, "expires_at_ms": expiry}


def revoke_credential_sessions(conn, row: Any, *, preserve_access: dict[str, Any] | None = None):
    """Revoke grants in the credential edit transaction, retaining a fresh verify.

    Only an active cache with exactly the newly verified password may survive
    save. This transfers verification to the new generation without relogin.
    """
    value = dict(row)
    conn.execute("DELETE FROM smart_classroom_session_grants WHERE credential_id=?", (int(value["id"]),))
    key = account_key(value)
    keep_fingerprint = (_fingerprint(preserve_access)
                        if preserve_access and account_key(preserve_access) == key else "")
    conn.execute("""UPDATE smart_classroom_sessions SET token_encrypted='',token_version=token_version+1,
        state='needs_teacher_reauth',login_owner='',login_until_ms=0,error_code='credential_changed',updated_at_ms=?
        WHERE external_account_key=? AND NOT(state='active' AND secret_fingerprint=?)""",
        (_now_ms(), key, keep_fingerprint))


def mark_session_unauthorized(session: SchoolSession):
    """Fence stale responses: an old client's 401 cannot revoke a newer token."""
    now = _now_ms()
    with get_db_connection() as conn:
        conn.execute("""UPDATE smart_classroom_sessions SET state='needs_teacher_reauth',token_encrypted='',
            error_code='unauthorized',next_login_at_ms=CASE
                WHEN recovery_used>0 AND last_login_ms>? THEN last_login_ms+? ELSE 0 END,updated_at_ms=?
            WHERE external_account_key=? AND token_version=? AND state='active'""",
            (now - RECOVERY_COOLDOWN_MS, RECOVERY_COOLDOWN_MS, now,
             session.external_account_key, session.token_version))
        conn.commit()


def session_public_status(conn, access: dict[str, Any]) -> dict[str, Any]:
    _validate_saved_access(conn, access)
    row = conn.execute("SELECT state,last_login_ms,next_login_at_ms,error_code FROM smart_classroom_sessions "
                       "WHERE external_account_key=?", (account_key(access),)).fetchone()
    return dict(row) if row else {"state": "empty", "last_login_ms": 0, "next_login_at_ms": 0, "error_code": ""}


def _claim_or_load(access, *, login_scope, allow_login, force_reauth, owner):
    now, key, fingerprint = _now_ms(), account_key(access), _fingerprint(access)
    with get_db_connection() as conn:
        if not force_reauth or access.get("credential_id"):
            _validate_saved_access(conn, access)
        conn.execute("""INSERT INTO smart_classroom_sessions(external_account_key,secret_fingerprint,updated_at_ms)
                        VALUES(?,?,?) ON CONFLICT(external_account_key) DO NOTHING""", (key, fingerprint, now))
        row = dict(conn.execute("SELECT * FROM smart_classroom_sessions WHERE external_account_key=?", (key,)).fetchone())
        if row["login_owner"] and int(row["login_until_ms"]) > now:
            conn.commit()
            return None
        if not force_reauth and row["secret_fingerprint"] != fingerprint:
            raise SmartClassroomSessionError("needs_teacher_reauth")
        if not force_reauth and row["state"] == "active":
            token = decrypt_academic_secret(row["token_encrypted"])
            if token:
                conn.commit()
                return SchoolSession(key, int(row["token_version"]), token)
        recovery = row["state"] != "empty" or bool(row["login_owner"])
        if not force_reauth and recovery:
            if not allow_login:
                raise SmartClassroomSessionError("needs_teacher_reauth")
            if int(row["next_login_at_ms"]) > now:
                code = row["error_code"] if row["error_code"] in {"captcha_required", "credential_invalid"} else "needs_teacher_reauth"
                raise SmartClassroomSessionError(code)
        claimed = conn.execute("""UPDATE smart_classroom_sessions
            SET login_owner=?,login_until_ms=?,state='needs_teacher_reauth',token_encrypted='',
                secret_fingerprint=?,error_code='login_pending',recovery_used=?,updated_at_ms=?
            WHERE external_account_key=? AND token_version=? AND login_until_ms<=?""",
            (owner, now + LOGIN_LEASE_MS, fingerprint,
             0 if force_reauth else (1 if recovery else int(row["recovery_used"])), now,
             key, int(row["token_version"]), now)).rowcount
        if not claimed:
            conn.commit()
            return None
        conn.commit()
        return owner


async def acquire_session(
    access: dict[str, Any], login: Callable[[], Awaitable[tuple[dict[str, Any], str]]], *,
    login_scope: str | None = None, allow_login: bool = True, force_reauth: bool = False,
) -> SchoolSession:
    owner = uuid.uuid4().hex
    claim = None
    for delay in (0, .15, .3, .6, 1.2, 2.4, 3.0):
        if delay:
            await asyncio.sleep(delay)
        claim = _claim_or_load(access, login_scope=login_scope, allow_login=allow_login,
                               force_reauth=force_reauth, owner=owner)
        if claim is not None:
            break
    if claim is None:
        raise SmartClassroomSessionError("session_busy")
    if isinstance(claim, SchoolSession):
        return claim
    key = account_key(access)
    try:
        result, token = await login()
        if result.get("status") != "verified" or not token:
            code = str(result.get("error_code") or "needs_teacher_reauth")
            error = SmartClassroomSessionError(code, str(result.get("message") or "智慧课堂登录未通过。"))
            error.verification_result = result
            raise error
        with get_db_connection() as conn:
            if not force_reauth or access.get("credential_id"):
                _validate_saved_access(conn, access)
            updated = conn.execute("""UPDATE smart_classroom_sessions SET token_encrypted=?,token_version=token_version+1,
                state='active',login_owner='',login_until_ms=0,last_login_ms=?,next_login_at_ms=0,error_code='',updated_at_ms=?
                WHERE external_account_key=? AND login_owner=? AND login_until_ms>?""",
                (encrypt_academic_secret(token), _now_ms(), _now_ms(), key, owner, _now_ms())).rowcount
            if not updated:
                raise SmartClassroomSessionError("credential_changed")
            version = conn.execute("SELECT token_version FROM smart_classroom_sessions WHERE external_account_key=?", (key,)).fetchone()["token_version"]
            conn.commit()
        return SchoolSession(key, int(version), token, result)
    except BaseException as exc:
        # A timeout/cancellation may have already replaced the school's token.
        # Persist uncertainty; never treat that as a fresh-cache bootstrap.
        with get_db_connection() as conn:
            now = _now_ms()
            conn.execute("""UPDATE smart_classroom_sessions SET state='needs_teacher_reauth',token_encrypted='',
                login_owner='',login_until_ms=0,error_code=?,next_login_at_ms=?,updated_at_ms=?
                WHERE external_account_key=? AND login_owner=?""",
                (getattr(exc, "code", "login_failed"), now + RECOVERY_COOLDOWN_MS, now, key, owner))
            conn.commit()
        raise
