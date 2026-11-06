"""One self-password-change transaction for profile JSON and student forms.

Password CPU work owns no connection. The final actor-locked transaction checks
both the credentials read before that work and the session which authorized the
request. It revokes remembered devices and reissues this device atomically.
"""
from datetime import datetime, timezone
import sqlite3

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from ..db.sessions import get_user_session
from ..dependencies import build_session_user_key
from .account_credentials_service import credentials_changed, prepare_credentials_change
from .password_verification_service import prepare_changed_password
from .remember_login_service import current_device_remembered, reissue_for_current_device
from .student_auth_service import get_student_auth_record_by_pk, validate_student_password
from .student_lifecycle_service import normalize_student_enrollment_status, student_enrollment_status_label
from .teacher_account_service import TEACHER_PASSWORD_HINT, validate_teacher_password


def _read_account(conn, role, user_id):
    row = (get_student_auth_record_by_pk(conn, user_id) if role == "student" else
           conn.execute("SELECT * FROM teachers WHERE id = ? LIMIT 1", (user_id,)).fetchone())
    if row is None:
        raise HTTPException(404, "学生账号不存在。" if role == "student" else "教师账号不存在。")
    account = dict(row)
    if role == "student":
        status = normalize_student_enrollment_status(account.get("enrollment_status"))
        if status != "active":
            raise HTTPException(403, f"该学生已设置为{student_enrollment_status_label(status)}，暂不纳入课堂学习。")
        if account.get("password_reset_required"):
            raise HTTPException(400, "当前账号处于重置流程，请重新登录后设置密码。")
    elif int(account.get("is_active", 1) or 0) != 1:
        raise HTTPException(403, "教师账号已停用，请联系管理员。")
    return account


def _credential_revision(account):
    return account.get("hashed_password"), account.get("password_updated_at")


def _request_session_is_current(conn, request, user, role, user_id):
    if user.get("auth_channel") == "mp":
        # MP has its own opaque sessions and intentionally no Web session_id.
        # Revalidate that same credential under the actor lock; never create a
        # Web session or silently treat missing Web identity as permission.
        from .wechat_mp_service import _hash_token
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            return False
        bearer = authorization[7:].strip()
        if not bearer:
            return False
        # A row share lock orders the final write before a concurrent MP logout
        # (or observes its committed revocation). No sliding renewal occurs in
        # this credential transaction. Binding revocation updates this same
        # session row before committing, so no second lock order is introduced.
        lock_sql = "" if isinstance(conn, sqlite3.Connection) else " FOR SHARE OF s"
        session = conn.execute(
            "SELECT s.expires_at FROM mp_sessions s WHERE s.token_hash = ? AND s.revoked = 0 "
            "AND s.user_role = ? AND s.user_pk = ? AND (s.openid = '' OR EXISTS ("
            "SELECT 1 FROM wechat_bindings b WHERE b.openid = s.openid AND b.status = 'active' "
            "AND b.user_role = s.user_role AND b.user_pk = s.user_pk))" + lock_sql,
            (_hash_token(bearer), role, user_id),
        ).fetchone()
        if session is None:
            return False
        try:
            expiry = datetime.fromisoformat(session["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return expiry > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return False
    session = get_user_session(build_session_user_key(user_id, role), conn=conn)
    return bool(user.get("session_id") and session and session["session_id"] == user["session_id"])


async def change_own_password(request, user, current_password, new_password, confirm_password,
                              *, connection_factory, success_message):
    if new_password != confirm_password:
        raise HTTPException(400, "两次输入的新密码不一致。")
    if current_password == new_password:
        raise HTTPException(400, "新密码不能与当前密码相同。")
    role = str(user.get("role") or "").strip().lower()
    if role not in {"student", "teacher"}:
        raise HTTPException(403, "当前身份不能修改密码。")
    user_id = int(user["id"])
    if role == "student":
        error = validate_student_password(new_password)
        if error:
            raise HTTPException(400, error)
    else:
        try:
            validate_teacher_password(new_password)
        except ValueError as exc:
            raise HTTPException(400, str(exc) or TEACHER_PASSWORD_HINT) from exc

    def read_candidate():
        with connection_factory() as conn:
            return _read_account(conn, role, user_id)

    candidate = await run_in_threadpool(read_candidate)
    if not candidate.get("hashed_password"):
        raise HTTPException(400, "当前密码错误。")
    encoded = await prepare_changed_password(current_password, new_password, candidate["hashed_password"])
    if encoded is None:
        raise HTTPException(400, "当前密码错误。")

    def finish_change():
        with connection_factory() as conn:
            prepare_credentials_change(conn, role=role, user_id=user_id)
            current = _read_account(conn, role, user_id)
            if _credential_revision(current) != _credential_revision(candidate):
                raise HTTPException(409, "修改期间账号凭据已更新，请重新输入当前密码。")
            if not _request_session_is_current(conn, request, user, role, user_id):
                raise HTTPException(401, "登录状态已失效，请重新登录后修改密码。",
                                    headers={"X-Auth-Session-Invalidated": "1"})
            # Read this proof under the same actor lock, before revoking all
            # devices. No pre-transaction remember snapshot can revive a grant.
            remembered_until = current_device_remembered(request, user, conn=conn)
            credentials_changed(conn, role=role, user_id=user_id)
            table = "students" if role == "student" else "teachers"
            reset_sql = ", password_reset_required = 0" if role == "student" else ""
            conn.execute(f"UPDATE {table} SET hashed_password = ?, password_updated_at = ?{reset_sql} WHERE id = ?",
                         (encoded, datetime.now().isoformat(), user_id))
            response = JSONResponse({"status": "success", "message": success_message})
            if remembered_until:
                reissue_for_current_device(request, response, user, remembered_until, conn=conn)
            conn.commit()
        return response

    return await run_in_threadpool(finish_change)
