"""WeChat Mini Program access service (微信小程序端认证与会话).

Responsibilities:

- exchange a ``wx.login()`` code for an ``openid`` via the official
  ``jscode2session`` endpoint (AppSecret stays server-side only);
- manage ``wechat_bindings`` (one account per openid; several openids per account);
- issue / resolve opaque ``mp_sessions`` bearer tokens with sliding
  expiry (30 days, refreshed on activity) — see ``schema_wechat_mp``
  for why this is separate from the IP-bound web JWT sessions;
- short-lived signed *bind tickets* so the client never re-sends the
  single-use wx code during the first-time binding flow;
- database-backed rate limiting for binding attempts (identity probing 防护).

All SQL uses ``?`` placeholders through ``get_db_connection()`` facade
(auto-adapted for postgres), per the life-tip incident convention.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from jose import JWTError, jwt

from ..config import ALGORITHM, SECRET_KEY
from ..db.schema_wechat_mp import ensure_wechat_mp_schema
from .student_lifecycle_service import is_active_student_status

MP_SESSION_TTL_DAYS = 30
MP_SESSION_TOUCH_INTERVAL_MINUTES = 60
BIND_TICKET_EXPIRE_MINUTES = 10
BIND_RATE_MAX_ATTEMPTS = 5
BIND_RATE_WINDOW_MINUTES = 10
_JSCODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

class WechatMpError(ValueError):
    """User-facing mini-program auth error (message is safe to display)."""


def ensure_wechat_mp_runtime(conn: Any) -> None:
    ensure_wechat_mp_schema(conn)


# ---------------------------------------------------------------------------
# WeChat platform credentials + code2session
# ---------------------------------------------------------------------------

def get_wechat_mp_credentials() -> tuple[str, str]:
    """AppID/AppSecret from env (canonical names first, legacy fallback)."""
    appid = os.getenv("WECHAT_MP_APPID") or os.getenv("AppID") or ""
    secret = os.getenv("WECHAT_MP_APPSECRET") or os.getenv("AppSecret") or ""
    if not appid.strip() or not secret.strip():
        raise WechatMpError("服务器未配置微信小程序凭据，请联系管理员。")
    return appid.strip(), secret.strip()


def exchange_code_for_openid(code: str) -> dict[str, str]:
    """Call jscode2session and return ``{"openid", "unionid"}``.

    The returned ``session_key`` is deliberately dropped — we never use
    WeChat encrypted user data, so storing it would only add risk.
    """
    normalized_code = str(code or "").strip()
    if not normalized_code:
        raise WechatMpError("缺少微信登录凭据（code）。")
    appid, secret = get_wechat_mp_credentials()
    try:
        response = httpx.get(
            _JSCODE2SESSION_URL,
            params={
                "appid": appid,
                "secret": secret,
                "js_code": normalized_code,
                "grant_type": "authorization_code",
            },
            timeout=8.0,
        )
        payload = response.json()
    except Exception as exc:
        print(f"[WECHAT_MP] jscode2session 请求失败: {exc}")
        raise WechatMpError("微信登录服务暂时不可用，请稍后重试。") from exc

    errcode = int(payload.get("errcode") or 0)
    if errcode:
        # 40029 invalid code / 40163 code been used / 45011 rate limited
        print(f"[WECHAT_MP] jscode2session 错误: {errcode} {payload.get('errmsg')}")
        if errcode in (40029, 40163):
            raise WechatMpError("微信登录凭据已失效，请重新进入小程序。")
        if errcode == 45011:
            raise WechatMpError("登录尝试过于频繁，请稍后重试。")
        raise WechatMpError("微信登录失败，请稍后重试。")

    openid = str(payload.get("openid") or "").strip()
    if not openid:
        raise WechatMpError("微信登录失败：未获取到用户标识。")
    return {"openid": openid, "unionid": str(payload.get("unionid") or "").strip()}


# ---------------------------------------------------------------------------
# Bind tickets (signed, short-lived, carry the openid between requests)
# ---------------------------------------------------------------------------

def build_bind_ticket(openid: str, unionid: str = "") -> str:
    issued_at = datetime.now(timezone.utc)
    payload = {
        "purpose": "mp_bind_ticket",
        "openid": openid,
        "unionid": unionid or "",
        "jti": secrets.token_urlsafe(24),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=BIND_TICKET_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_bind_ticket(ticket: str) -> Optional[dict]:
    if not ticket:
        return None
    try:
        payload = jwt.decode(ticket, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if (
        payload.get("purpose") != "mp_bind_ticket"
        or not payload.get("openid")
        or not payload.get("jti")
        or not payload.get("exp")
    ):
        return None
    # jose checks exp at whole-second precision. Reject the exact expiry instant
    # too, before cleanup can remove its consumed receipt and permit a replay.
    try:
        if float(payload["exp"]) <= datetime.now(timezone.utc).timestamp():
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return payload


def consume_bind_ticket(conn: Any, ticket: str) -> Optional[dict]:
    """Claim a verified ticket once, in the transaction that creates the binding.

    ON CONFLICT avoids aborting a PostgreSQL transaction on replay. A rollback
    releases the claim, allowing recovery when binding fails before committing.
    Never trust a decoded payload supplied by the caller at this boundary.
    """
    payload = decode_bind_ticket(ticket)
    if not payload:
        return None
    ensure_wechat_mp_schema(conn)
    now = datetime.now(timezone.utc)
    conn.execute(
        "DELETE FROM mp_consumed_bind_tickets WHERE expires_at <= ?", (now.isoformat(),)
    )
    expires_at = datetime.fromtimestamp(float(payload["exp"]), timezone.utc)
    cursor = conn.execute(
        """
        INSERT INTO mp_consumed_bind_tickets (ticket_id, expires_at) VALUES (?, ?)
        ON CONFLICT (ticket_id) DO NOTHING
        """,
        (payload["jti"], expires_at.isoformat()),
    )
    return payload if cursor.rowcount == 1 else None


# ---------------------------------------------------------------------------
# Bind-attempt rate limiting (shared between workers, per openid / per IP)
# ---------------------------------------------------------------------------

def check_bind_rate_limit(conn: Any, *keys: str) -> None:
    """Reserve one attempt in a rolling window; caller commits before checking credentials.

    Upsert locks each key before reading its window on both database engines.
    Sort keys to avoid lock-order inversion when concurrent identities share IPs.
    This function does not commit the caller's other work.
    """
    ensure_wechat_mp_schema(conn)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=BIND_RATE_WINDOW_MINUTES)
    conn.execute("DELETE FROM mp_bind_rate_limits WHERE updated_at < ?", (cutoff.isoformat(),))
    hashes = sorted({_hash_token(str(key).strip()) for key in keys if str(key or "").strip()})
    windows = []
    for key_hash in hashes:
        conn.execute(
            """
            INSERT INTO mp_bind_rate_limits (key_hash, updated_at) VALUES (?, ?)
            ON CONFLICT (key_hash) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (key_hash, now.isoformat()),
        )
        row = conn.execute(
            "SELECT attempt_times FROM mp_bind_rate_limits WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        attempts = [stamp for stamp in json.loads(row["attempt_times"]) if stamp > cutoff.timestamp()]
        if len(attempts) >= BIND_RATE_MAX_ATTEMPTS:
            raise WechatMpError("绑定尝试次数过多，请 10 分钟后再试。")
        windows.append((key_hash, attempts))
    for key_hash, attempts in windows:
        attempts.append(now.timestamp())
        conn.execute(
            "UPDATE mp_bind_rate_limits SET attempt_times = ? WHERE key_hash = ?",
            (json.dumps(attempts), key_hash),
        )


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------

def find_active_binding(conn: Any, openid: str) -> Optional[dict]:
    ensure_wechat_mp_schema(conn)
    row = conn.execute(
        "SELECT * FROM wechat_bindings WHERE openid = ? AND status = 'active' LIMIT 1",
        (str(openid).strip(),),
    ).fetchone()
    return dict(row) if row else None


def create_binding(
    conn: Any,
    *,
    user_role: str,
    user_pk: int,
    openid: str,
    unionid: str = "",
) -> dict:
    """Bind an openid to an account.

    The openid is the natural key: rebinding the same WeChat identity to
    a different account simply repoints the row (old account keeps no
    stale claim on this phone).
    """
    ensure_wechat_mp_schema(conn)
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO wechat_bindings (user_role, user_pk, openid, unionid, status, bound_at, last_login_at, updated_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT (openid)
        DO UPDATE SET user_role = excluded.user_role,
                      user_pk = excluded.user_pk,
                      unionid = excluded.unionid,
                      status = 'active',
                      bound_at = excluded.bound_at,
                      last_login_at = excluded.last_login_at,
                      updated_at = excluded.updated_at
        """,
        (str(user_role), int(user_pk), str(openid).strip(), unionid or "", now, now, now),
    )
    binding = find_active_binding(conn, openid)
    if not binding:
        raise WechatMpError("绑定失败，请稍后重试。")
    # The upsert holds this openid row lock until commit. A concurrent rebind
    # must wait, then invalidate all sessions created by the preceding bind.
    conn.execute(
        "UPDATE mp_sessions SET revoked = 1 WHERE openid = ? AND revoked = 0",
        (str(openid).strip(),),
    )
    return binding


def touch_binding_login(conn: Any, openid: str) -> None:
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE wechat_bindings SET last_login_at = ?, updated_at = ? WHERE openid = ?",
        (now, now, str(openid).strip()),
    )


def revoke_binding(conn: Any, *, user_role: str, user_pk: int) -> int:
    """Admin/self unbind: revoke every binding + session of the account."""
    ensure_wechat_mp_schema(conn)
    cursor = conn.execute(
        """
        UPDATE wechat_bindings SET status = 'revoked', updated_at = ?
        WHERE user_role = ? AND user_pk = ? AND status = 'active'
        """,
        (datetime.now().isoformat(), str(user_role), int(user_pk)),
    )
    revoked = cursor.rowcount if cursor.rowcount is not None else 0
    conn.execute(
        "UPDATE mp_sessions SET revoked = 1 WHERE user_role = ? AND user_pk = ?",
        (str(user_role), int(user_pk)),
    )
    return revoked


def revoke_openid_binding(conn: Any, *, openid: str, user_role: str, user_pk: int) -> int:
    """Unbind only the current WeChat identity, preserving other devices/accounts."""
    ensure_wechat_mp_schema(conn)
    normalized_openid = str(openid or "").strip()
    if not normalized_openid:
        return 0
    cursor = conn.execute(
        """
        UPDATE wechat_bindings SET status = 'revoked', updated_at = ?
        WHERE openid = ? AND user_role = ? AND user_pk = ? AND status = 'active'
        """,
        (datetime.now().isoformat(), normalized_openid, str(user_role), int(user_pk)),
    )
    conn.execute(
        """
        UPDATE mp_sessions SET revoked = 1
        WHERE openid = ? AND user_role = ? AND user_pk = ? AND revoked = 0
        """,
        (normalized_openid, str(user_role), int(user_pk)),
    )
    return max(cursor.rowcount or 0, 0)


# ---------------------------------------------------------------------------
# Sessions (opaque bearer tokens, sliding expiry)
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_mp_session(conn: Any, *, user_role: str, user_pk: int, openid: str = "") -> str:
    ensure_wechat_mp_schema(conn)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO mp_sessions (token_hash, user_role, user_pk, openid, issued_at, expires_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _hash_token(token),
            str(user_role),
            int(user_pk),
            str(openid or "").strip(),
            now.isoformat(),
            (now + timedelta(days=MP_SESSION_TTL_DAYS)).isoformat(),
            now.isoformat(),
        ),
    )
    return token


def resolve_mp_session(conn: Any, token: str) -> Optional[dict]:
    """Return the session row for a valid token, applying sliding expiry.

    None means invalid / expired / revoked — the caller answers 401.
    """
    normalized = str(token or "").strip()
    if not normalized:
        return None
    ensure_wechat_mp_schema(conn)
    row = conn.execute(
        "SELECT * FROM mp_sessions WHERE token_hash = ? AND revoked = 0 LIMIT 1",
        (_hash_token(normalized),),
    ).fetchone()
    if not row:
        return None
    session = dict(row)
    if session.get("openid"):
        binding = find_active_binding(conn, session["openid"])
        if (
            not binding
            or binding["user_role"] != session["user_role"]
            or int(binding["user_pk"]) != int(session["user_pk"])
        ):
            revoke_mp_session(conn, normalized)
            return None
    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(session["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    if expires_at <= now:
        return None

    # Sliding renewal, throttled so不是每个请求都写库。
    try:
        last_seen = datetime.fromisoformat(session["last_seen_at"])
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        last_seen = now - timedelta(days=1)
    if now - last_seen > timedelta(minutes=MP_SESSION_TOUCH_INTERVAL_MINUTES):
        conn.execute(
            "UPDATE mp_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
            (
                now.isoformat(),
                (now + timedelta(days=MP_SESSION_TTL_DAYS)).isoformat(),
                session["id"],
            ),
        )
    return session


def revoke_mp_session(conn: Any, token: str) -> bool:
    normalized = str(token or "").strip()
    if not normalized:
        return False
    ensure_wechat_mp_schema(conn)
    cursor = conn.execute(
        "UPDATE mp_sessions SET revoked = 1 WHERE token_hash = ?",
        (_hash_token(normalized),),
    )
    return bool(cursor.rowcount)


# ---------------------------------------------------------------------------
# User loading (shape mirrors the web token payload so downstream services
# that expect ``user["id"]`` / ``user["role"]`` keep working unchanged)
# ---------------------------------------------------------------------------

def load_mp_user(conn: Any, session: dict) -> Optional[dict]:
    role = str(session.get("user_role") or "")
    user_pk = int(session.get("user_pk") or 0)
    if role == "student":
        row = conn.execute(
            """
            SELECT s.id, s.name, s.student_id_number, s.school_code, s.department,
                   s.enrollment_status,
                   c.name AS class_name
            FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
            """,
            (user_pk,),
        ).fetchone()
        if not row or not is_active_student_status(row["enrollment_status"]):
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "student_id_number": row["student_id_number"],
            "class_name": row["class_name"],
            "school_code": row["school_code"],
            "department": row["department"],
            "role": "student",
        }
    if role == "teacher":
        row = conn.execute(
            "SELECT id, name, email FROM teachers WHERE id = ? AND COALESCE(is_active, 1) = 1 LIMIT 1",
            (user_pk,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "role": "teacher",
        }
    return None
