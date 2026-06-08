"""校园公文通 (doc.gxufl.com) teacher integration — credential lifecycle + login.

The school 公文通 only authenticates teachers through the unified-auth (统一认证)
OAuth2 flow, whose login page requires a captcha. We solve the captcha with the
platform's own multimodal model (``/api/ai/chat`` vision capability), then drive
the standard authorization-code exchange to obtain a session token.

Login flow (reverse-engineered, see memory ``gongwen-integration``):
  1. ``GET  {api}/user/ssoLogin``                -> authorize URL (sso.gxufl.edu.cn)
  2. ``GET  {sso}/captcha``                      -> GIF (sets JSESSIONID) -> OCR
  3. ``GET  {sso}/oauth2/doLogin?name&pwd=md5&captcha&agree=on`` -> {code:200}
  4. ``GET  authorize URL`` (302)                -> redirect ...?code=<CODE>
  5. ``GET  {api}/user/ssoCallback?code=<CODE>`` -> {result:{token}}

This module mirrors ``smart_classroom_integration_service`` for the credential
CRUD so the management UI and probes stay consistent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.parse as urlparse
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

import httpx

from ..core import ai_client
from .academic_integration_service import (
    STATUS_CHALLENGE,
    STATUS_FAILED,
    STATUS_UNAVAILABLE,
    STATUS_UNCHECKED,
    STATUS_VERIFIED,
    decrypt_academic_secret,
    encrypt_academic_secret,
)


GONGWEN_HTTP_TIMEOUT_SECONDS = 25.0
CAPTCHA_MAX_ATTEMPTS = 4
AI_OCR_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class GongwenSystemProfile:
    system_code: str
    system_name: str
    adapter_key: str
    auth_method: str
    base_url: str
    api_base_url: str
    sso_base_url: str
    login_url: str
    client_id: str
    username_label: str = "统一认证账号"
    password_label: str = "统一认证密码"
    note: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "system_code": self.system_code,
            "system_name": self.system_name,
            "adapter_key": self.adapter_key,
            "auth_method": self.auth_method,
            "base_url": self.base_url,
            "api_base_url": self.api_base_url,
            "sso_base_url": self.sso_base_url,
            "login_url": self.login_url,
            "username_label": self.username_label,
            "password_label": self.password_label,
            "note": self.note,
        }

    def access_method(self) -> dict[str, Any]:
        return {
            "system_code": self.system_code,
            "system_name": self.system_name,
            "adapter_key": self.adapter_key,
            "auth_method": self.auth_method,
            "base_url": self.base_url,
            "api_base_url": self.api_base_url,
            "sso_base_url": self.sso_base_url,
            "login_url": self.login_url,
            "client_id": self.client_id,
        }


GONGWEN_SYSTEM_PROFILES: dict[str, GongwenSystemProfile] = {
    "gxufl": GongwenSystemProfile(
        system_code="gxufl",
        system_name="校园公文通",
        adapter_key="gongwen_oauth_captcha_v1",
        auth_method="sso_captcha",
        base_url="https://doc.gxufl.com",
        api_base_url="https://doc_api.gxufl.com/api",
        sso_base_url="https://sso.gxufl.edu.cn",
        login_url="https://doc.gxufl.com/login",
        client_id="1015",
        note="统一认证 OAuth2：登录页验证码由多模态模型识别，登录后通过 code 换取访问令牌。",
    ),
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json_object(raw_value: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if raw_value in (None, ""):
        return dict(fallback or {})
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return dict(fallback or {})
    return parsed if isinstance(parsed, dict) else dict(fallback or {})


def _normalize_username(value: Any) -> str:
    username = re.sub(r"\s+", "", str(value or ""))
    if not username:
        raise ValueError("请填写统一认证账号。")
    if len(username) > 120:
        raise ValueError("统一认证账号过长，请检查后再保存。")
    return username


def _normalize_password(value: Any) -> str:
    password = str(value or "")
    if not password:
        raise ValueError("请填写统一认证密码。")
    if len(password) > 256:
        raise ValueError("统一认证密码过长，请检查后再保存。")
    return password


def list_gongwen_system_profiles() -> list[dict[str, Any]]:
    return [profile.to_public_dict() for profile in GONGWEN_SYSTEM_PROFILES.values()]


def get_gongwen_system_profile(system_code: Any) -> GongwenSystemProfile:
    normalized = str(system_code or "").strip().lower()
    profile = GONGWEN_SYSTEM_PROFILES.get(normalized)
    if profile is None:
        raise ValueError("暂不支持该校园公文通对接。")
    return profile


def normalize_gongwen_credential_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = get_gongwen_system_profile(payload.get("system_code") or payload.get("system") or "gxufl")
    return {
        "system_code": profile.system_code,
        "username": _normalize_username(payload.get("username")),
        "password": _normalize_password(payload.get("password")),
        "enabled": bool(payload.get("enabled", True)),
    }


def _failure_result(
    profile: GongwenSystemProfile,
    status: str,
    message: str,
    *,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "message": message,
        "system_code": profile.system_code,
        "system_name": profile.system_name,
        "checked_at": _now_iso(),
        "elapsed_ms": elapsed_ms,
        "access_method": profile.access_method(),
    }


def _success_result(
    profile: GongwenSystemProfile,
    *,
    username: str,
    display_name: str = "",
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": STATUS_VERIFIED,
        "message": "校园公文通账号已通过统一认证校验。",
        "system_code": profile.system_code,
        "system_name": profile.system_name,
        "username": username,
        "display_name": display_name,
        "checked_at": _now_iso(),
        "elapsed_ms": elapsed_ms,
        "access_method": profile.access_method(),
    }


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LanShare-Gongwen-Sync/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    }


async def _ocr_captcha(image_bytes: bytes, content_type: str) -> str:
    """Recognise the captcha text via the platform multimodal model."""
    if not image_bytes:
        return ""
    mime = (content_type or "").split(";")[0].strip() or "image/gif"
    # Some vision providers reject GIF — convert to PNG when Pillow is available.
    data_bytes, data_mime = image_bytes, mime
    if mime == "image/gif":
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                buffer = io.BytesIO()
                img.convert("RGB").save(buffer, format="PNG")
                data_bytes, data_mime = buffer.getvalue(), "image/png"
        except Exception:
            data_bytes, data_mime = image_bytes, mime
    data_url = f"data:{data_mime};base64,{base64.b64encode(data_bytes).decode('ascii')}"
    payload = {
        "system_prompt": (
            "你是验证码识别器。图片是一张包含 4 个英文字母或数字的校园登录验证码。"
            "请只识别其中的字符，忽略干扰线和颜色，输出 JSON。"
        ),
        "messages": [],
        "new_message": "识别这张验证码图片中的字符，返回 {\"captcha\": \"xxxx\"}，全部小写，不要解释。",
        "base64_urls": [],
        "image_inputs": [{"url": data_url, "label": "验证码图片"}],
        "file_texts": [],
        "model_capability": "vision",
        "task_type": "vision",
        "response_format": "json",
        "task_priority": "interactive",
        "task_label": "gongwen_captcha_ocr",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=AI_OCR_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"验证码识别服务不可用：{str(exc)[:120]}") from exc
    parsed = data.get("response_json") if isinstance(data, dict) else None
    raw_text = ""
    if isinstance(parsed, dict):
        raw_text = str(parsed.get("captcha") or parsed.get("code") or parsed.get("text") or "")
    elif isinstance(data, dict):
        raw_text = str(data.get("response_text") or "")
    captcha = re.sub(r"[^0-9a-zA-Z]", "", raw_text).lower()
    return captcha


async def _exchange_code_for_token(
    client: httpx.AsyncClient,
    profile: GongwenSystemProfile,
    authorize_url: str,
) -> str:
    """After a successful doLogin, follow authorize to capture the OAuth code."""
    code = ""
    current = authorize_url
    for _ in range(6):
        response = await client.get(current, headers={"Referer": authorize_url})
        location = response.headers.get("location", "")
        if response.status_code in (301, 302, 303, 307, 308) and location:
            query = urlparse.parse_qs(urlparse.urlparse(location).query)
            if "code" in query:
                code = (query.get("code") or [""])[0]
                break
            current = location if location.startswith("http") else profile.sso_base_url + location
            continue
        break
    if not code:
        raise ValueError("统一认证已登录，但未能获取授权码，请稍后重试。")
    callback = await client.get(
        f"{profile.api_base_url}/user/ssoCallback",
        params={"code": code},
        headers={"Origin": profile.base_url, "Referer": f"{profile.base_url}/"},
    )
    callback.raise_for_status()
    body = callback.json()
    result = body.get("result") if isinstance(body, dict) else None
    token = str(result.get("token") or "") if isinstance(result, dict) else ""
    if not token:
        message = (body.get("message") if isinstance(body, dict) else "") or "公文通换取访问令牌失败。"
        raise ValueError(str(message)[:200])
    return token


async def _login_gongwen_client(
    client: httpx.AsyncClient,
    profile: GongwenSystemProfile,
    *,
    username: str,
    password: str,
) -> tuple[dict[str, Any], str]:
    """Drive the full OAuth2 + captcha login. Returns (result, token)."""
    started_at = time.monotonic()
    pwd_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()

    def elapsed() -> int:
        return int((time.monotonic() - started_at) * 1000)

    try:
        sso_login = await client.get(
            f"{profile.api_base_url}/user/ssoLogin",
            headers={"Origin": profile.base_url, "Referer": f"{profile.base_url}/"},
        )
        sso_login.raise_for_status()
        authorize_url = str((sso_login.json() or {}).get("result") or "")
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return _failure_result(profile, STATUS_UNAVAILABLE, f"无法连接校园公文通：{str(exc)[:160]}", elapsed_ms=elapsed()), ""
    if not authorize_url.startswith("http"):
        return _failure_result(profile, STATUS_UNAVAILABLE, "校园公文通未返回统一认证入口地址。", elapsed_ms=elapsed()), ""

    last_message = "统一认证登录失败，请检查账号、密码后重试。"
    for attempt in range(1, CAPTCHA_MAX_ATTEMPTS + 1):
        try:
            # Prime the SSO session + fetch a captcha bound to it.
            await client.get(authorize_url, headers={"Referer": f"{profile.base_url}/"})
            captcha_resp = await client.get(
                f"{profile.sso_base_url}/captcha",
                headers={"Referer": authorize_url},
            )
            captcha_resp.raise_for_status()
        except httpx.HTTPError as exc:
            return _failure_result(profile, STATUS_UNAVAILABLE, f"获取验证码失败：{str(exc)[:140]}", elapsed_ms=elapsed()), ""

        try:
            captcha_text = await _ocr_captcha(captcha_resp.content, captcha_resp.headers.get("content-type", ""))
        except ValueError as exc:
            return _failure_result(profile, STATUS_UNAVAILABLE, str(exc), elapsed_ms=elapsed()), ""
        if len(captcha_text) < 3:
            last_message = "验证码识别结果异常，正在重试。"
            continue

        try:
            do_login = await client.get(
                f"{profile.sso_base_url}/oauth2/doLogin",
                params={"name": username, "pwd": pwd_md5, "captcha": captcha_text, "agree": "on"},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": authorize_url},
            )
            login_body = do_login.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            return _failure_result(profile, STATUS_UNAVAILABLE, f"统一认证登录请求失败：{str(exc)[:140]}", elapsed_ms=elapsed()), ""

        code_value = login_body.get("code") if isinstance(login_body, dict) else None
        message = str((login_body or {}).get("msg") or (login_body or {}).get("message") or "")
        if code_value in (200, "200"):
            try:
                token = await _exchange_code_for_token(client, profile, authorize_url)
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                return _failure_result(profile, STATUS_UNAVAILABLE, str(exc)[:200], elapsed_ms=elapsed()), ""
            client.headers["Authorization"] = token
            display_name = await _load_display_name(client, profile)
            return _success_result(profile, username=username, display_name=display_name, elapsed_ms=elapsed()), token

        last_message = message or last_message
        if "验证码" in message:
            # Wrong captcha — refetch and retry.
            continue
        # Wrong account/password — no point retrying the captcha.
        return _failure_result(profile, STATUS_FAILED, message or "统一认证账号或密码错误。", elapsed_ms=elapsed()), ""

    return _failure_result(profile, STATUS_CHALLENGE, last_message, elapsed_ms=elapsed()), ""


async def _load_display_name(client: httpx.AsyncClient, profile: GongwenSystemProfile) -> str:
    try:
        info = await client.get(
            f"{profile.api_base_url}/user/info",
            headers={"Origin": profile.base_url, "Referer": f"{profile.base_url}/"},
        )
        if info.status_code < 400:
            result = (info.json() or {}).get("result") or {}
            if isinstance(result, dict):
                return str(result.get("realName") or result.get("name") or "").strip()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        pass
    return ""


def _new_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(GONGWEN_HTTP_TIMEOUT_SECONDS, connect=10.0)
    return httpx.AsyncClient(
        timeout=timeout,
        headers=_browser_headers(),
        verify=False,
        follow_redirects=False,
    )


async def verify_gongwen_credential(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_gongwen_credential_payload(payload)
    profile = get_gongwen_system_profile(normalized["system_code"])
    async with _new_client() as client:
        result, _token = await _login_gongwen_client(
            client,
            profile,
            username=normalized["username"],
            password=normalized["password"],
        )
        return result


@asynccontextmanager
async def open_authenticated_gongwen_client(
    access_payload: dict[str, Any],
) -> AsyncIterator[tuple[httpx.AsyncClient, GongwenSystemProfile, str]]:
    """Open an authenticated 公文通 client for internal sync jobs."""
    profile = get_gongwen_system_profile(access_payload.get("system_code") or "gxufl")
    username = _normalize_username(access_payload.get("username"))
    password = _normalize_password(access_payload.get("password"))
    client = _new_client()
    try:
        result, token = await _login_gongwen_client(client, profile, username=username, password=password)
        if result.get("status") != STATUS_VERIFIED or not token:
            raise ValueError(str(result.get("message") or "校园公文通登录校验未通过。"))
        yield client, profile, token
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Credential persistence (table: teacher_gongwen_credentials)
# --------------------------------------------------------------------------- #


def _load_access_method(raw_value: Any, profile: GongwenSystemProfile) -> dict[str, Any]:
    parsed = _load_json_object(raw_value, profile.access_method())
    return parsed if parsed else profile.access_method()


def serialize_gongwen_credential(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    profile = GONGWEN_SYSTEM_PROFILES.get(str(row_dict.get("system_code") or "").strip().lower())
    fallback_profile = profile or GONGWEN_SYSTEM_PROFILES["gxufl"]
    return {
        "id": int(row_dict["id"]),
        "teacher_id": int(row_dict["teacher_id"]),
        "system_code": str(row_dict.get("system_code") or ""),
        "system_name": str(row_dict.get("system_name") or ""),
        "adapter_key": str(row_dict.get("adapter_key") or ""),
        "auth_method": str(row_dict.get("auth_method") or ""),
        "base_url": str(row_dict.get("base_url") or ""),
        "api_base_url": str(row_dict.get("api_base_url") or ""),
        "sso_base_url": str(row_dict.get("sso_base_url") or ""),
        "login_url": str(row_dict.get("login_url") or ""),
        "username": str(row_dict.get("username") or ""),
        "display_name": str(row_dict.get("display_name") or ""),
        "enabled": bool(row_dict.get("enabled")),
        "has_password": bool(row_dict.get("password_encrypted")),
        "last_status": str(row_dict.get("last_status") or STATUS_UNCHECKED),
        "last_status_at": str(row_dict.get("last_status_at") or ""),
        "last_error": str(row_dict.get("last_error") or ""),
        "last_verified_at": str(row_dict.get("last_verified_at") or ""),
        "created_at": str(row_dict.get("created_at") or ""),
        "updated_at": str(row_dict.get("updated_at") or ""),
        "access_method": _load_access_method(row_dict.get("access_method_json"), fallback_profile),
    }


def list_teacher_gongwen_credentials(conn, teacher_id: int | str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM teacher_gongwen_credentials
        WHERE teacher_id = ?
        ORDER BY enabled DESC, last_verified_at DESC, updated_at DESC, id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    return [serialize_gongwen_credential(row) for row in rows]


def get_teacher_gongwen_credential(conn, teacher_id: int | str, credential_id: int | str):
    row = conn.execute(
        """
        SELECT *
        FROM teacher_gongwen_credentials
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (int(credential_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        raise ValueError("校园公文通凭据不存在。")
    return row


def save_verified_gongwen_credential(
    conn,
    teacher_id: int | str,
    payload: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_gongwen_credential_payload(payload)
    profile = get_gongwen_system_profile(normalized["system_code"])
    if verification.get("status") != STATUS_VERIFIED:
        raise ValueError("校园公文通账号尚未通过校验，不能保存。")

    now = _now_iso()
    checked_at = str(verification.get("checked_at") or now)
    access_method_json = _json_dumps(profile.access_method())
    conn.execute(
        """
        INSERT INTO teacher_gongwen_credentials (
            teacher_id, system_code, system_name, adapter_key, auth_method,
            base_url, api_base_url, sso_base_url, login_url, username,
            password_encrypted, display_name, enabled, last_status, last_status_at,
            last_error, last_verified_at, access_method_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id, system_code, auth_method) DO UPDATE SET
            system_name = excluded.system_name,
            adapter_key = excluded.adapter_key,
            base_url = excluded.base_url,
            api_base_url = excluded.api_base_url,
            sso_base_url = excluded.sso_base_url,
            login_url = excluded.login_url,
            username = excluded.username,
            password_encrypted = excluded.password_encrypted,
            display_name = excluded.display_name,
            enabled = excluded.enabled,
            last_status = excluded.last_status,
            last_status_at = excluded.last_status_at,
            last_error = excluded.last_error,
            last_verified_at = excluded.last_verified_at,
            access_method_json = excluded.access_method_json,
            updated_at = excluded.updated_at
        """,
        (
            int(teacher_id),
            profile.system_code,
            profile.system_name,
            profile.adapter_key,
            profile.auth_method,
            profile.base_url,
            profile.api_base_url,
            profile.sso_base_url,
            profile.login_url,
            normalized["username"],
            encrypt_academic_secret(normalized["password"]),
            str(verification.get("display_name") or ""),
            1 if normalized["enabled"] else 0,
            STATUS_VERIFIED,
            checked_at,
            "",
            checked_at,
            access_method_json,
            now,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT *
        FROM teacher_gongwen_credentials
        WHERE teacher_id = ? AND system_code = ? AND auth_method = ?
        LIMIT 1
        """,
        (int(teacher_id), profile.system_code, profile.auth_method),
    ).fetchone()
    return serialize_gongwen_credential(row)


def build_saved_gongwen_verification_payload(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    password = decrypt_academic_secret(row_dict.get("password_encrypted"))
    if not password:
        raise ValueError("已保存的校园公文通密码无法解密，请重新录入。")
    return {
        "system_code": str(row_dict.get("system_code") or ""),
        "username": str(row_dict.get("username") or ""),
        "password": password,
        "enabled": bool(row_dict.get("enabled")),
    }


def update_gongwen_credential_verification_status(
    conn,
    teacher_id: int | str,
    credential_id: int | str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    get_teacher_gongwen_credential(conn, teacher_id, credential_id)
    now = _now_iso()
    status = str(verification.get("status") or STATUS_UNCHECKED)
    checked_at = str(verification.get("checked_at") or now)
    is_verified = status == STATUS_VERIFIED
    conn.execute(
        """
        UPDATE teacher_gongwen_credentials
        SET last_status = ?,
            last_status_at = ?,
            last_error = ?,
            last_verified_at = CASE WHEN ? THEN ? ELSE last_verified_at END,
            display_name = CASE WHEN ? THEN ? ELSE display_name END,
            updated_at = ?
        WHERE id = ? AND teacher_id = ?
        """,
        (
            status,
            checked_at,
            "" if is_verified else str(verification.get("message") or "")[:500],
            1 if is_verified else 0,
            checked_at,
            1 if is_verified else 0,
            str(verification.get("display_name") or ""),
            now,
            int(credential_id),
            int(teacher_id),
        ),
    )
    return serialize_gongwen_credential(get_teacher_gongwen_credential(conn, teacher_id, credential_id))


def delete_teacher_gongwen_credential(conn, teacher_id: int | str, credential_id: int | str) -> int:
    get_teacher_gongwen_credential(conn, teacher_id, credential_id)
    cursor = conn.execute(
        "DELETE FROM teacher_gongwen_credentials WHERE id = ? AND teacher_id = ?",
        (int(credential_id), int(teacher_id)),
    )
    return int(cursor.rowcount or 0)


def load_teacher_gongwen_access_method(conn, teacher_id: int | str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM teacher_gongwen_credentials
        WHERE teacher_id = ?
          AND enabled = 1
          AND last_status = ?
        ORDER BY last_verified_at DESC, updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), STATUS_VERIFIED),
    ).fetchone()
    if row is None:
        return None
    payload = build_saved_gongwen_verification_payload(row)
    payload["access_method"] = _load_access_method(
        row["access_method_json"],
        get_gongwen_system_profile(row["system_code"] or "gxufl"),
    )
    payload["credential_id"] = int(row["id"])
    return payload
