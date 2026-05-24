from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

import httpx

from .academic_integration_service import (
    STATUS_FAILED,
    STATUS_UNAVAILABLE,
    STATUS_UNCHECKED,
    STATUS_VERIFIED,
    decrypt_academic_secret,
    encrypt_academic_secret,
)


SMART_CLASSROOM_HTTP_TIMEOUT_SECONDS = 18.0


@dataclass(frozen=True)
class SmartClassroomProfile:
    platform_code: str
    platform_name: str
    adapter_key: str
    auth_method: str
    base_url: str
    api_base_url: str
    login_url: str
    username_label: str = "工号 / 统一账号"
    password_label: str = "智慧课堂密码"
    note: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "platform_code": self.platform_code,
            "platform_name": self.platform_name,
            "adapter_key": self.adapter_key,
            "auth_method": self.auth_method,
            "base_url": self.base_url,
            "api_base_url": self.api_base_url,
            "login_url": self.login_url,
            "username_label": self.username_label,
            "password_label": self.password_label,
            "note": self.note,
        }

    def access_method(self) -> dict[str, Any]:
        return {
            "platform_code": self.platform_code,
            "platform_name": self.platform_name,
            "adapter_key": self.adapter_key,
            "auth_method": self.auth_method,
            "base_url": self.base_url,
            "api_base_url": self.api_base_url,
            "login_url": self.login_url,
        }


SMART_CLASSROOM_PROFILES: dict[str, SmartClassroomProfile] = {
    "gxufl_smart_classroom": SmartClassroomProfile(
        platform_code="gxufl_smart_classroom",
        platform_name="广外智慧课堂",
        adapter_key="gxufl_smart_classroom_v3",
        auth_method="password_token",
        base_url="https://edu.gxufl.edu.cn",
        api_base_url="https://edu_api.gxufl.com/api",
        login_url="https://edu.gxufl.edu.cn/login",
        note="智慧课堂 V3：表单登录后返回临时 Token，后续点名接口通过 Authorization 请求头访问。",
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
        raise ValueError("请填写智慧课堂账号。")
    if len(username) > 120:
        raise ValueError("智慧课堂账号过长，请检查后再保存。")
    return username


def _normalize_password(value: Any) -> str:
    password = str(value or "")
    if not password:
        raise ValueError("请填写智慧课堂密码。")
    if len(password) > 256:
        raise ValueError("智慧课堂密码过长，请检查后再保存。")
    return password


def list_smart_classroom_profiles() -> list[dict[str, Any]]:
    return [profile.to_public_dict() for profile in SMART_CLASSROOM_PROFILES.values()]


def get_smart_classroom_profile(platform_code: Any) -> SmartClassroomProfile:
    normalized = str(platform_code or "").strip().lower()
    profile = SMART_CLASSROOM_PROFILES.get(normalized)
    if profile is None:
        raise ValueError("暂不支持该智慧课堂平台对接。")
    return profile


def normalize_smart_classroom_credential_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = get_smart_classroom_profile(
        payload.get("platform_code") or payload.get("platform") or "gxufl_smart_classroom"
    )
    return {
        "platform_code": profile.platform_code,
        "username": _normalize_username(payload.get("username")),
        "password": _normalize_password(payload.get("password")),
        "enabled": bool(payload.get("enabled", True)),
    }


def _failure_result(
    profile: SmartClassroomProfile,
    status: str,
    message: str,
    *,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    checked_at = _now_iso()
    return {
        "ok": False,
        "status": status,
        "message": message,
        "platform_code": profile.platform_code,
        "platform_name": profile.platform_name,
        "checked_at": checked_at,
        "elapsed_ms": elapsed_ms,
        "access_method": profile.access_method(),
    }


def _success_result(
    profile: SmartClassroomProfile,
    *,
    username: str,
    display_name: str = "",
    elapsed_ms: int = 0,
    user_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checked_at = _now_iso()
    return {
        "ok": True,
        "status": STATUS_VERIFIED,
        "message": "智慧课堂账号已通过登录校验。",
        "platform_code": profile.platform_code,
        "platform_name": profile.platform_name,
        "username": username,
        "display_name": display_name,
        "checked_at": checked_at,
        "elapsed_ms": elapsed_ms,
        "access_method": profile.access_method(),
        "user_info": user_info or {},
    }


def _smart_headers(profile: SmartClassroomProfile) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 LanShare Smart Classroom Sync/1.0",
        "Accept": "application/json, text/plain, */*",
        "Origin": profile.base_url,
        "Referer": f"{profile.base_url}/",
    }


async def _login_smart_classroom_client(
    client: httpx.AsyncClient,
    profile: SmartClassroomProfile,
    *,
    username: str,
    password: str,
) -> tuple[dict[str, Any], str]:
    started_at = time.monotonic()
    try:
        response = await client.post(
            "/login",
            data={"username": username, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        return (
            _failure_result(
                profile,
                STATUS_UNAVAILABLE,
                f"无法连接智慧课堂或智慧课堂响应异常：{str(exc)[:180]}",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            ),
            "",
        )

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    err_no = payload.get("errNo")
    token = str(payload.get("token") or "").strip()
    if err_no not in (0, "0") or not token:
        message = (
            payload.get("msg")
            or payload.get("message")
            or payload.get("errMsg")
            or "智慧课堂账号或密码校验失败。"
        )
        return _failure_result(profile, STATUS_FAILED, str(message)[:260], elapsed_ms=elapsed_ms), ""

    if payload.get("isTeacher") is False:
        return (
            _failure_result(profile, STATUS_FAILED, "当前智慧课堂账号不是教师账号，无法同步点名记录。", elapsed_ms=elapsed_ms),
            "",
        )

    client.headers["Authorization"] = token
    display_name = str(
        payload.get("realName")
        or payload.get("name")
        or payload.get("nickName")
        or ""
    ).strip()

    user_info: dict[str, Any] = {}
    try:
        info_response = await client.get("/user/getUserInfo", params={"_t": int(time.time() * 1000)})
        if info_response.status_code < 400:
            info_payload = info_response.json()
            if isinstance(info_payload, dict):
                user_info = info_payload
                display_name = str(
                    info_payload.get("realName")
                    or info_payload.get("name")
                    or display_name
                    or ""
                ).strip()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        user_info = {}

    return (
        _success_result(
            profile,
            username=username,
            display_name=display_name,
            elapsed_ms=elapsed_ms,
            user_info=user_info,
        ),
        token,
    )


async def verify_smart_classroom_credential(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_smart_classroom_credential_payload(payload)
    profile = get_smart_classroom_profile(normalized["platform_code"])
    timeout = httpx.Timeout(SMART_CLASSROOM_HTTP_TIMEOUT_SECONDS, connect=8.0)
    async with httpx.AsyncClient(
        base_url=profile.api_base_url,
        timeout=timeout,
        headers=_smart_headers(profile),
        verify=False,
        follow_redirects=False,
    ) as client:
        result, _token = await _login_smart_classroom_client(
            client,
            profile,
            username=normalized["username"],
            password=normalized["password"],
        )
        return result


@asynccontextmanager
async def open_authenticated_smart_classroom_client(
    access_payload: dict[str, Any],
) -> AsyncIterator[tuple[httpx.AsyncClient, SmartClassroomProfile, dict[str, Any]]]:
    profile = get_smart_classroom_profile(access_payload.get("platform_code") or "gxufl_smart_classroom")
    username = _normalize_username(access_payload.get("username"))
    password = _normalize_password(access_payload.get("password"))
    if profile.adapter_key != "gxufl_smart_classroom_v3" or profile.auth_method != "password_token":
        raise ValueError("该智慧课堂适配器暂不支持自动登录会话。")

    timeout = httpx.Timeout(SMART_CLASSROOM_HTTP_TIMEOUT_SECONDS, connect=8.0)
    client = httpx.AsyncClient(
        base_url=profile.api_base_url,
        timeout=timeout,
        headers=_smart_headers(profile),
        verify=False,
        follow_redirects=False,
    )
    try:
        login_result, _token = await _login_smart_classroom_client(
            client,
            profile,
            username=username,
            password=password,
        )
        if login_result.get("status") != STATUS_VERIFIED:
            raise ValueError(str(login_result.get("message") or "智慧课堂登录校验未通过。"))
        yield client, profile, login_result
    finally:
        await client.aclose()


def _load_access_method(raw_value: Any, profile: SmartClassroomProfile) -> dict[str, Any]:
    parsed = _load_json_object(raw_value, profile.access_method())
    return parsed if parsed else profile.access_method()


def serialize_smart_classroom_credential(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    profile = SMART_CLASSROOM_PROFILES.get(str(row_dict.get("platform_code") or "").strip().lower())
    fallback_profile = profile or SmartClassroomProfile(
        platform_code=str(row_dict.get("platform_code") or ""),
        platform_name=str(row_dict.get("platform_name") or "未知平台"),
        adapter_key=str(row_dict.get("adapter_key") or ""),
        auth_method=str(row_dict.get("auth_method") or ""),
        base_url=str(row_dict.get("base_url") or ""),
        api_base_url=str(row_dict.get("api_base_url") or ""),
        login_url=str(row_dict.get("login_url") or ""),
    )
    return {
        "id": int(row_dict["id"]),
        "teacher_id": int(row_dict["teacher_id"]),
        "platform_code": str(row_dict.get("platform_code") or ""),
        "platform_name": str(row_dict.get("platform_name") or ""),
        "adapter_key": str(row_dict.get("adapter_key") or ""),
        "auth_method": str(row_dict.get("auth_method") or ""),
        "base_url": str(row_dict.get("base_url") or ""),
        "api_base_url": str(row_dict.get("api_base_url") or ""),
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


def list_teacher_smart_classroom_credentials(conn, teacher_id: int | str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM teacher_smart_classroom_credentials
        WHERE teacher_id = ?
        ORDER BY enabled DESC, last_verified_at DESC, updated_at DESC, id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    return [serialize_smart_classroom_credential(row) for row in rows]


def get_teacher_smart_classroom_credential(conn, teacher_id: int | str, credential_id: int | str):
    row = conn.execute(
        """
        SELECT *
        FROM teacher_smart_classroom_credentials
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (int(credential_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        raise ValueError("智慧课堂凭据不存在。")
    return row


def save_verified_smart_classroom_credential(
    conn,
    teacher_id: int | str,
    payload: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_smart_classroom_credential_payload(payload)
    profile = get_smart_classroom_profile(normalized["platform_code"])
    if verification.get("status") != STATUS_VERIFIED:
        raise ValueError("智慧课堂账号尚未通过校验，不能保存。")

    now = _now_iso()
    checked_at = str(verification.get("checked_at") or now)
    access_method_json = _json_dumps(profile.access_method())
    conn.execute(
        """
        INSERT INTO teacher_smart_classroom_credentials (
            teacher_id, platform_code, platform_name, adapter_key, auth_method,
            base_url, api_base_url, login_url, username, password_encrypted,
            display_name, enabled, last_status, last_status_at, last_error,
            last_verified_at, access_method_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id, platform_code, auth_method) DO UPDATE SET
            platform_name = excluded.platform_name,
            adapter_key = excluded.adapter_key,
            base_url = excluded.base_url,
            api_base_url = excluded.api_base_url,
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
            profile.platform_code,
            profile.platform_name,
            profile.adapter_key,
            profile.auth_method,
            profile.base_url,
            profile.api_base_url,
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
        FROM teacher_smart_classroom_credentials
        WHERE teacher_id = ? AND platform_code = ? AND auth_method = ?
        LIMIT 1
        """,
        (int(teacher_id), profile.platform_code, profile.auth_method),
    ).fetchone()
    return serialize_smart_classroom_credential(row)


def build_saved_smart_classroom_verification_payload(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    password = decrypt_academic_secret(row_dict.get("password_encrypted"))
    if not password:
        raise ValueError("已保存的智慧课堂密码无法解密，请重新录入。")
    return {
        "platform_code": str(row_dict.get("platform_code") or ""),
        "username": str(row_dict.get("username") or ""),
        "password": password,
        "enabled": bool(row_dict.get("enabled")),
    }


def update_smart_classroom_credential_verification_status(
    conn,
    teacher_id: int | str,
    credential_id: int | str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    get_teacher_smart_classroom_credential(conn, teacher_id, credential_id)
    now = _now_iso()
    status = str(verification.get("status") or STATUS_UNCHECKED)
    checked_at = str(verification.get("checked_at") or now)
    is_verified = status == STATUS_VERIFIED
    conn.execute(
        """
        UPDATE teacher_smart_classroom_credentials
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
    return serialize_smart_classroom_credential(
        get_teacher_smart_classroom_credential(conn, teacher_id, credential_id)
    )


def delete_teacher_smart_classroom_credential(conn, teacher_id: int | str, credential_id: int | str) -> int:
    get_teacher_smart_classroom_credential(conn, teacher_id, credential_id)
    cursor = conn.execute(
        """
        DELETE FROM teacher_smart_classroom_credentials
        WHERE id = ? AND teacher_id = ?
        """,
        (int(credential_id), int(teacher_id)),
    )
    return int(cursor.rowcount or 0)


def load_teacher_smart_classroom_access_method(conn, teacher_id: int | str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM teacher_smart_classroom_credentials
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
    payload = build_saved_smart_classroom_verification_payload(row)
    payload["access_method"] = _load_access_method(
        row["access_method_json"],
        get_smart_classroom_profile(row["platform_code"] or "gxufl_smart_classroom"),
    )
    payload["credential_id"] = int(row["id"])
    return payload
