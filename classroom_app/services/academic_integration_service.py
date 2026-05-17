from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import rsa

from ..config import SECRET_KEY


STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_CHALLENGE = "challenge_required"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNCHECKED = "unchecked"

ACADEMIC_HTTP_TIMEOUT_SECONDS = 18.0
ACADEMIC_SECRET_PREFIX = "ls-academic-v1:"


@dataclass(frozen=True)
class AcademicSystemProfile:
    school_code: str
    school_name: str
    adapter_key: str
    auth_method: str
    base_url: str
    login_path: str
    login_url: str
    public_key_path: str = ""
    home_path: str = "/xtgl/index_initMenu.html"
    username_label: str = "账号"
    password_label: str = "密码"
    note: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "school_code": self.school_code,
            "school_name": self.school_name,
            "adapter_key": self.adapter_key,
            "auth_method": self.auth_method,
            "base_url": self.base_url,
            "login_url": self.login_url,
            "username_label": self.username_label,
            "password_label": self.password_label,
            "note": self.note,
        }

    def access_method(self) -> dict[str, Any]:
        return {
            "school_code": self.school_code,
            "school_name": self.school_name,
            "adapter_key": self.adapter_key,
            "auth_method": self.auth_method,
            "base_url": self.base_url,
            "login_path": self.login_path,
            "login_url": self.login_url,
            "public_key_path": self.public_key_path,
            "home_path": self.home_path,
        }


ACADEMIC_SYSTEM_PROFILES: dict[str, AcademicSystemProfile] = {
    "gxufl": AcademicSystemProfile(
        school_code="gxufl",
        school_name="广西外国语学院",
        adapter_key="zfsoft_v9",
        auth_method="password_rsa",
        base_url="https://jwxt.gxufl.com",
        login_path="/xtgl/login_slogin.html",
        login_url="https://jwxt.gxufl.com/xtgl/login_slogin.html",
        public_key_path="/xtgl/login_getPublicKey.html",
        home_path="/xtgl/index_initMenu.html",
        username_label="教职工号 / 统一账号",
        password_label="教务系统密码",
        note="正方教务 V9：登录页获取 CSRF 与 RSA 公钥，提交加密密码后确认会话。",
    ),
}


def _now_iso() -> str:
    return datetime.now().isoformat()


def _secret_key_bytes() -> bytes:
    return hashlib.sha256(str(SECRET_KEY or "lanshare-academic-secret").encode("utf-8")).digest()


def _secret_keystream(nonce: bytes, length: int) -> bytes:
    key = _secret_key_bytes()
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:length])


def encrypt_academic_secret(value: Any) -> str:
    raw_value = str(value or "")
    if not raw_value:
        return ""
    nonce = os.urandom(16)
    plaintext = raw_value.encode("utf-8")
    stream = _secret_keystream(nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    signature = hmac.new(_secret_key_bytes(), nonce + ciphertext, hashlib.sha256).digest()[:16]
    return ACADEMIC_SECRET_PREFIX + base64.urlsafe_b64encode(nonce + signature + ciphertext).decode("ascii")


def decrypt_academic_secret(value: Any) -> str:
    token = str(value or "")
    if not token or not token.startswith(ACADEMIC_SECRET_PREFIX):
        return ""
    try:
        payload = base64.urlsafe_b64decode(token[len(ACADEMIC_SECRET_PREFIX) :].encode("ascii"))
        nonce = payload[:16]
        signature = payload[16:32]
        ciphertext = payload[32:]
        expected = hmac.new(_secret_key_bytes(), nonce + ciphertext, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(signature, expected):
            return ""
        stream = _secret_keystream(nonce, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return ""


def list_academic_system_profiles() -> list[dict[str, Any]]:
    return [profile.to_public_dict() for profile in ACADEMIC_SYSTEM_PROFILES.values()]


def get_academic_system_profile(school_code: Any) -> AcademicSystemProfile:
    normalized = str(school_code or "").strip().lower()
    profile = ACADEMIC_SYSTEM_PROFILES.get(normalized)
    if profile is None:
        raise ValueError("暂不支持该学校的教务系统对接。")
    return profile


def _normalize_username(value: Any) -> str:
    username = re.sub(r"\s+", "", str(value or ""))
    if not username:
        raise ValueError("请填写教务系统账号。")
    if len(username) > 120:
        raise ValueError("教务系统账号过长，请检查后再保存。")
    return username


def _normalize_password(value: Any) -> str:
    password = str(value or "")
    if not password:
        raise ValueError("请填写教务系统密码。")
    if len(password) > 256:
        raise ValueError("教务系统密码过长，请检查后再保存。")
    return password


def normalize_academic_credential_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = get_academic_system_profile(payload.get("school_code") or payload.get("school"))
    return {
        "school_code": profile.school_code,
        "username": _normalize_username(payload.get("username")),
        "password": _normalize_password(payload.get("password")),
        "enabled": bool(payload.get("enabled", True)),
    }


def _strip_tags(raw_html: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(raw_html or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_input_value(page_html: str, input_name_or_id: str) -> str:
    normalized = re.escape(input_name_or_id)
    patterns = (
        rf"<input\b(?=[^>]*(?:name|id)=['\"]{normalized}['\"])(?=[^>]*\bvalue\s*=\s*['\"]([^'\"]*)['\"])[^>]*>",
        rf"<input\b(?=[^>]*(?:name|id)=['\"]{normalized}['\"])(?=[^>]*\bvalue\s*=\s*([^\s>]+))[^>]*>",
    )
    for pattern in patterns:
        match = re.search(pattern, page_html, re.IGNORECASE | re.DOTALL)
        if match:
            return html.unescape(str(match.group(1) or "").strip())
    return ""


def _extract_login_tip(page_html: str) -> str:
    for pattern in (
        r"<p\b(?=[^>]*\bid=['\"]tips['\"])[^>]*>(.*?)</p>",
        r"<div\b(?=[^>]*\bid=['\"]tips['\"])[^>]*>(.*?)</div>",
        r"<span\b(?=[^>]*\bid=['\"]tips['\"])[^>]*>(.*?)</span>",
    ):
        match = re.search(pattern, page_html, re.IGNORECASE | re.DOTALL)
        if match:
            tip = _strip_tags(match.group(1))
            if tip:
                return tip[:260]
    return ""


def _looks_like_login_page(page_html: str) -> bool:
    lowered = str(page_html or "").lower()
    return "login_slogin.html" in lowered and 'name="yhm"' in lowered and 'name="mm"' in lowered


def _looks_like_authenticated_page(page_html: str) -> bool:
    if _looks_like_login_page(page_html):
        return False
    return any(marker in page_html for marker in ("退出", "个人信息", "我的桌面", "index_initMenu"))


def _location_requires_interaction(location: str) -> bool:
    lowered = str(location or "").lower()
    return any(marker in lowered for marker in ("xgmm", "modify", "dxyz", "sms", "captcha", "rqyzm"))


def _is_login_location(location: str) -> bool:
    lowered = str(location or "").lower()
    return not lowered or "login_slogin" in lowered or "login_init" in lowered


def _failure_result(profile: AcademicSystemProfile, status: str, message: str, *, elapsed_ms: int = 0) -> dict[str, Any]:
    checked_at = _now_iso()
    return {
        "ok": False,
        "status": status,
        "message": message,
        "school_code": profile.school_code,
        "school_name": profile.school_name,
        "checked_at": checked_at,
        "elapsed_ms": elapsed_ms,
        "access_method": profile.access_method(),
    }


def _success_result(
    profile: AcademicSystemProfile,
    *,
    username: str,
    display_name: str = "",
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    checked_at = _now_iso()
    return {
        "ok": True,
        "status": STATUS_VERIFIED,
        "message": "教务系统账号已通过登录校验。",
        "school_code": profile.school_code,
        "school_name": profile.school_name,
        "username": username,
        "display_name": display_name,
        "checked_at": checked_at,
        "elapsed_ms": elapsed_ms,
        "access_method": profile.access_method(),
    }


def _rsa_encrypt_password(password: str, modulus_b64: str, exponent_b64: str) -> str:
    try:
        modulus = int.from_bytes(base64.b64decode(str(modulus_b64)), "big")
        exponent = int.from_bytes(base64.b64decode(str(exponent_b64)), "big")
        public_key = rsa.PublicKey(modulus, exponent)
        encrypted = rsa.encrypt(password.encode("utf-8"), public_key)
        return base64.b64encode(encrypted).decode("ascii")
    except OverflowError as exc:
        raise ValueError("密码长度超过教务系统 RSA 加密上限，请确认密码是否粘贴异常。") from exc
    except Exception as exc:
        raise ValueError("教务系统公钥解析失败，暂时无法校验账号。") from exc


async def _probe_zfsoft_home(
    client: httpx.AsyncClient,
    profile: AcademicSystemProfile,
    *,
    username: str,
    started_at: float,
) -> dict[str, Any] | None:
    try:
        response = await client.get(profile.home_path, follow_redirects=False)
    except httpx.HTTPError:
        return None

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    location = response.headers.get("location", "")
    if response.status_code in {301, 302, 303, 307, 308}:
        if _is_login_location(location):
            return _failure_result(profile, STATUS_FAILED, "教务系统未建立有效登录会话，请确认账号密码。", elapsed_ms=elapsed_ms)
        if _location_requires_interaction(location):
            return _failure_result(profile, STATUS_CHALLENGE, "账号密码可能正确，但教务系统要求完成二次验证或改密后才能对接。", elapsed_ms=elapsed_ms)
        return _success_result(profile, username=username, elapsed_ms=elapsed_ms)

    if response.status_code == 200 and _looks_like_authenticated_page(response.text):
        return _success_result(profile, username=username, elapsed_ms=elapsed_ms)
    return None


async def _verify_zfsoft_v9_password(
    profile: AcademicSystemProfile,
    *,
    username: str,
    password: str,
) -> dict[str, Any]:
    started_at = time.monotonic()
    headers = {
        "User-Agent": "LanShare Academic Integration/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": profile.login_url,
    }
    timeout = httpx.Timeout(ACADEMIC_HTTP_TIMEOUT_SECONDS, connect=8.0)
    async with httpx.AsyncClient(
        base_url=profile.base_url,
        timeout=timeout,
        headers=headers,
        follow_redirects=False,
    ) as client:
        try:
            login_response = await client.get(profile.login_path)
            login_response.raise_for_status()
            login_html = login_response.text
            csrf_token = _extract_input_value(login_html, "csrftoken")
            if not csrf_token:
                return _failure_result(
                    profile,
                    STATUS_UNAVAILABLE,
                    "教务系统登录页未返回 CSRF 令牌，暂时无法自动校验。",
                    elapsed_ms=int((time.monotonic() - started_at) * 1000),
                )

            password_for_submit = password
            if _extract_input_value(login_html, "mmsfjm") != "0":
                public_key_response = await client.get(
                    profile.public_key_path,
                    params={"time": int(time.time() * 1000)},
                    headers={"Accept": "application/json,*/*;q=0.8"},
                )
                public_key_response.raise_for_status()
                public_key = public_key_response.json()
                password_for_submit = _rsa_encrypt_password(
                    password,
                    str(public_key.get("modulus") or ""),
                    str(public_key.get("exponent") or ""),
                )

            language = _extract_input_value(login_html, "language") or "zh_CN"
            post_response = await client.post(
                profile.login_path,
                params={"time": int(time.time() * 1000)},
                data={
                    "csrftoken": csrf_token,
                    "language": language,
                    "yhm": username,
                    "mm": password_for_submit,
                    "ydType": "",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": profile.base_url,
                    "Referer": profile.login_url,
                },
            )
        except ValueError as exc:
            return _failure_result(
                profile,
                STATUS_UNAVAILABLE,
                str(exc),
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            return _failure_result(
                profile,
                STATUS_UNAVAILABLE,
                f"无法连接教务系统或教务系统响应异常：{str(exc)[:180]}",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        location = post_response.headers.get("location", "")
        if post_response.status_code in {301, 302, 303, 307, 308}:
            if _location_requires_interaction(location):
                return _failure_result(
                    profile,
                    STATUS_CHALLENGE,
                    "账号密码可能正确，但教务系统要求完成二次验证或改密后才能对接。",
                    elapsed_ms=elapsed_ms,
                )
            if not _is_login_location(location):
                return _success_result(profile, username=username, elapsed_ms=elapsed_ms)

        tip = _extract_login_tip(post_response.text)
        if tip:
            return _failure_result(profile, STATUS_FAILED, tip, elapsed_ms=elapsed_ms)

        if "验证码" in post_response.text and _looks_like_login_page(post_response.text):
            return _failure_result(
                profile,
                STATUS_CHALLENGE,
                "教务系统要求输入验证码，请稍后在教务系统网页完成一次正常登录后再回来保存。",
                elapsed_ms=elapsed_ms,
            )

        home_result = await _probe_zfsoft_home(
            client,
            profile,
            username=username,
            started_at=started_at,
        )
        if home_result is not None:
            return home_result

        return _failure_result(
            profile,
            STATUS_UNAVAILABLE,
            "教务系统未返回明确登录结果，已取消保存以避免写入不可用凭据。",
            elapsed_ms=elapsed_ms,
        )


async def verify_academic_credential(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_academic_credential_payload(payload)
    profile = get_academic_system_profile(normalized["school_code"])
    if profile.adapter_key == "zfsoft_v9" and profile.auth_method == "password_rsa":
        return await _verify_zfsoft_v9_password(
            profile,
            username=normalized["username"],
            password=normalized["password"],
        )
    return _failure_result(profile, STATUS_UNAVAILABLE, "该学校适配器尚未实现账号密码校验。")


def _load_access_method(raw_value: Any, profile: AcademicSystemProfile) -> dict[str, Any]:
    if not raw_value:
        return profile.access_method()
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return profile.access_method()
    return parsed if isinstance(parsed, dict) else profile.access_method()


def serialize_academic_credential(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    profile = ACADEMIC_SYSTEM_PROFILES.get(str(row_dict.get("school_code") or "").strip().lower())
    fallback_profile = profile or AcademicSystemProfile(
        school_code=str(row_dict.get("school_code") or ""),
        school_name=str(row_dict.get("school_name") or "未知学校"),
        adapter_key=str(row_dict.get("adapter_key") or ""),
        auth_method=str(row_dict.get("auth_method") or ""),
        base_url=str(row_dict.get("base_url") or ""),
        login_path="",
        login_url=str(row_dict.get("login_url") or ""),
    )
    return {
        "id": int(row_dict["id"]),
        "teacher_id": int(row_dict["teacher_id"]),
        "school_code": str(row_dict.get("school_code") or ""),
        "school_name": str(row_dict.get("school_name") or ""),
        "adapter_key": str(row_dict.get("adapter_key") or ""),
        "auth_method": str(row_dict.get("auth_method") or ""),
        "base_url": str(row_dict.get("base_url") or ""),
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


def list_teacher_academic_credentials(conn, teacher_id: int | str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM teacher_academic_system_credentials
        WHERE teacher_id = ?
        ORDER BY enabled DESC, last_verified_at DESC, updated_at DESC, id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    return [serialize_academic_credential(row) for row in rows]


def get_teacher_academic_credential(conn, teacher_id: int | str, credential_id: int | str):
    row = conn.execute(
        """
        SELECT *
        FROM teacher_academic_system_credentials
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (int(credential_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        raise ValueError("教务系统凭据不存在。")
    return row


def save_verified_academic_credential(
    conn,
    teacher_id: int | str,
    payload: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_academic_credential_payload(payload)
    profile = get_academic_system_profile(normalized["school_code"])
    if verification.get("status") != STATUS_VERIFIED:
        raise ValueError("教务系统账号尚未通过校验，不能保存。")

    now = _now_iso()
    checked_at = str(verification.get("checked_at") or now)
    access_method_json = json.dumps(profile.access_method(), ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO teacher_academic_system_credentials (
            teacher_id, school_code, school_name, adapter_key, auth_method, base_url,
            login_url, username, password_encrypted, display_name, enabled,
            last_status, last_status_at, last_error, last_verified_at,
            access_method_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id, school_code, auth_method) DO UPDATE SET
            school_name = excluded.school_name,
            adapter_key = excluded.adapter_key,
            base_url = excluded.base_url,
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
            profile.school_code,
            profile.school_name,
            profile.adapter_key,
            profile.auth_method,
            profile.base_url,
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
        FROM teacher_academic_system_credentials
        WHERE teacher_id = ? AND school_code = ? AND auth_method = ?
        LIMIT 1
        """,
        (int(teacher_id), profile.school_code, profile.auth_method),
    ).fetchone()
    return serialize_academic_credential(row)


def build_saved_credential_verification_payload(row: Any) -> dict[str, Any]:
    row_dict = dict(row)
    password = decrypt_academic_secret(row_dict.get("password_encrypted"))
    if not password:
        raise ValueError("已保存的教务系统密码无法解密，请重新录入。")
    return {
        "school_code": str(row_dict.get("school_code") or ""),
        "username": str(row_dict.get("username") or ""),
        "password": password,
        "enabled": bool(row_dict.get("enabled")),
    }


def update_academic_credential_verification_status(
    conn,
    teacher_id: int | str,
    credential_id: int | str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    get_teacher_academic_credential(conn, teacher_id, credential_id)
    now = _now_iso()
    status = str(verification.get("status") or STATUS_UNCHECKED)
    checked_at = str(verification.get("checked_at") or now)
    is_verified = status == STATUS_VERIFIED
    conn.execute(
        """
        UPDATE teacher_academic_system_credentials
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
    return serialize_academic_credential(get_teacher_academic_credential(conn, teacher_id, credential_id))


def delete_teacher_academic_credential(conn, teacher_id: int | str, credential_id: int | str) -> int:
    get_teacher_academic_credential(conn, teacher_id, credential_id)
    cursor = conn.execute(
        "DELETE FROM teacher_academic_system_credentials WHERE id = ? AND teacher_id = ?",
        (int(credential_id), int(teacher_id)),
    )
    return int(cursor.rowcount or 0)


def load_teacher_academic_access_method(
    conn,
    teacher_id: int | str,
    *,
    school_code: str = "gxufl",
) -> dict[str, Any] | None:
    """Return the decrypted access payload for internal sync jobs.

    This function is intentionally not used by public API serializers; callers
    should keep the returned password in memory only for the duration of a sync.
    """
    profile = get_academic_system_profile(school_code)
    row = conn.execute(
        """
        SELECT *
        FROM teacher_academic_system_credentials
        WHERE teacher_id = ?
          AND school_code = ?
          AND enabled = 1
        ORDER BY last_verified_at DESC, updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id), profile.school_code),
    ).fetchone()
    if row is None:
        return None
    password = decrypt_academic_secret(row["password_encrypted"])
    if not password:
        return None
    credential = serialize_academic_credential(row)
    return {
        "credential_id": credential["id"],
        "school_code": credential["school_code"],
        "school_name": credential["school_name"],
        "username": credential["username"],
        "password": password,
        "access_method": credential["access_method"],
        "last_verified_at": credential["last_verified_at"],
    }
