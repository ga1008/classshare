from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import imaplib
import os
import smtplib
import socket
import ssl
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Optional

from ..config import (
    EMAIL_DEFAULT_DAILY_LIMIT,
    EMAIL_DEFAULT_PER_MINUTE_LIMIT,
    EMAIL_IMAP_TIMEOUT_SECONDS,
    EMAIL_SMTP_TIMEOUT_SECONDS,
    EMAIL_WORKER_BATCH_SIZE,
    EMAIL_WORKER_HEARTBEAT_TIMEOUT_SECONDS,
    EMAIL_WORKER_MAX_ATTEMPTS,
    EMAIL_WORKER_POLL_SECONDS,
    PUBLIC_SITE_BASE_URL,
    SECRET_KEY,
    SITE_DISPLAY_NAME,
)
from ..database import get_db_connection

NOTIFICATION_SEVERITY_NORMAL = "normal"
NOTIFICATION_SEVERITY_IMPORTANT = "important"
NOTIFICATION_SEVERITY_SYSTEM = "system"

SEVERITY_LABELS = {
    NOTIFICATION_SEVERITY_NORMAL: "普通通知",
    NOTIFICATION_SEVERITY_IMPORTANT: "重要通知",
    NOTIFICATION_SEVERITY_SYSTEM: "系统通知",
}

IMPORTANT_NOTIFICATION_CATEGORIES = {
    "assignment",
    "discussion_mention",
    "submission",
    "grading_result",
    "learning_progress",
}

SYSTEM_NOTIFICATION_CATEGORIES = {
    "ai_feedback",
    "app_feedback",
    "password_reset_request",
}

EMAIL_ELIGIBLE_CATEGORIES = {
    "assignment",
    "discussion_mention",
    "submission",
    "grading_result",
    "learning_progress",
    "app_feedback",
    "password_reset_request",
}

SECURITY_VALUES = {"ssl", "starttls", "none"}
EMAIL_STATUS_NOT_REQUIRED = "not_required"
EMAIL_STATUS_NOT_CONFIGURED = "not_configured"
EMAIL_STATUS_SKIPPED = "skipped"
EMAIL_STATUS_QUEUED = "queued"
EMAIL_STATUS_SENT = "sent"
EMAIL_STATUS_FAILED = "failed"
SECRET_TOKEN_PREFIX = "v1:"

EMAIL_PROVIDER_QQ = "qq"
EMAIL_PROVIDER_163 = "netease_163"
EMAIL_PROVIDER_126 = "netease_126"
EMAIL_PROVIDER_YEAH = "netease_yeah"
EMAIL_PROVIDER_SINA = "sina"
EMAIL_PROVIDER_SOHU = "sohu"
EMAIL_PROVIDER_ALIYUN = "aliyun"
EMAIL_PROVIDER_PRESETS = {
    EMAIL_PROVIDER_QQ: {
        "label": "QQ邮箱",
        "domains": ("qq.com",),
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "QQ邮箱授权码",
    },
    EMAIL_PROVIDER_163: {
        "label": "网易163邮箱",
        "domains": ("163.com",),
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.163.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "客户端授权密码",
    },
    EMAIL_PROVIDER_126: {
        "label": "网易126邮箱",
        "domains": ("126.com",),
        "smtp_host": "smtp.126.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.126.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "客户端授权密码",
    },
    EMAIL_PROVIDER_YEAH: {
        "label": "网易yeah.net邮箱",
        "domains": ("yeah.net",),
        "smtp_host": "smtp.yeah.net",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.yeah.net",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "客户端授权密码",
    },
    EMAIL_PROVIDER_SINA: {
        "label": "新浪邮箱",
        "domains": ("sina.com", "sina.cn"),
        "smtp_host": "smtp.sina.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.sina.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "邮箱授权码",
    },
    EMAIL_PROVIDER_SOHU: {
        "label": "搜狐邮箱",
        "domains": ("sohu.com",),
        "smtp_host": "smtp.sohu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.sohu.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "邮箱授权码",
    },
    EMAIL_PROVIDER_ALIYUN: {
        "label": "阿里邮箱",
        "domains": ("aliyun.com",),
        "smtp_host": "smtp.aliyun.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.aliyun.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "per_minute_limit": 20,
        "daily_limit": 200,
        "secret_label": "邮箱密码或客户端授权码",
    }
}

EMAIL_CATEGORY_ACTION_LABELS = {
    "assignment": "查看作业",
    "discussion_mention": "回到课堂讨论",
    "submission": "查看学生提交",
    "grading_result": "查看批改结果",
    "learning_progress": "查看学习进度",
    "app_feedback": "查看反馈",
    "password_reset_request": "处理申请",
}

EMAIL_CATEGORY_COPY = {
    "assignment": "老师发布了新的学习任务，建议尽早查看要求和截止安排。",
    "discussion_mention": "课堂讨论中有人提到了你，点开即可回到相关课堂继续查看。",
    "submission": "学生已有新的作业提交，点开即可查看详情或继续批改。",
    "grading_result": "你的作业已有新的批改结果，可以查看得分、反馈和后续建议。",
    "learning_progress": "学习进度有新的变化，可以查看当前阶段和下一步安排。",
    "app_feedback": "有新的平台反馈需要处理，点开即可进入后台查看。",
    "password_reset_request": "有学生提交了找回密码申请，请及时核对并处理。",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: Any, *, limit: int = 255) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _normalize_email(value: Any, *, required: bool = False) -> str:
    normalized = _normalize_text(value, limit=180).lower()
    if required and not normalized:
        raise ValueError("邮箱地址不能为空。")
    if normalized and ("@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]):
        raise ValueError("请输入有效的邮箱地址。")
    return normalized


def _email_domain(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized.rsplit("@", 1)[-1] if "@" in normalized else ""


def _normalize_security(value: Any, default: str = "ssl") -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in SECURITY_VALUES else default


def _provider_for_value(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "custom":
        return ""
    if normalized in EMAIL_PROVIDER_PRESETS:
        return normalized
    host_or_domain = normalized.rsplit("@", 1)[-1] if "@" in normalized else normalized
    for provider, preset in EMAIL_PROVIDER_PRESETS.items():
        domains = {str(domain).lower() for domain in preset.get("domains", ())}
        hosts = {
            str(preset.get("smtp_host") or "").lower(),
            str(preset.get("imap_host") or "").lower(),
        }
        if host_or_domain in domains or normalized in hosts:
            return provider
    return ""


def _infer_email_provider(payload: dict[str, Any]) -> str:
    provider = str(payload.get("provider") or "").strip().lower()
    if provider == "custom":
        return ""
    if provider in EMAIL_PROVIDER_PRESETS:
        return provider
    for key in ("from_email", "smtp_username", "imap_username", "smtp_host", "imap_host"):
        inferred = _provider_for_value(payload.get(key))
        if inferred:
            return inferred
    return ""


def notification_severity_for_category(category: Any) -> str:
    normalized = str(category or "").strip().lower()
    if normalized in IMPORTANT_NOTIFICATION_CATEGORIES:
        return NOTIFICATION_SEVERITY_IMPORTANT
    if normalized in SYSTEM_NOTIFICATION_CATEGORIES:
        return NOTIFICATION_SEVERITY_SYSTEM
    return NOTIFICATION_SEVERITY_NORMAL


def notification_email_required(category: Any, severity: Any = "") -> bool:
    normalized_category = str(category or "").strip().lower()
    normalized_severity = str(severity or notification_severity_for_category(normalized_category)).strip().lower()
    if normalized_category not in EMAIL_ELIGIBLE_CATEGORIES:
        return False
    return normalized_severity in {NOTIFICATION_SEVERITY_IMPORTANT, NOTIFICATION_SEVERITY_SYSTEM}


def _secret_key_bytes() -> bytes:
    return hashlib.sha256(str(SECRET_KEY or "lanshare-email-secret").encode("utf-8")).digest()


def _secret_keystream(nonce: bytes, length: int) -> bytes:
    key = _secret_key_bytes()
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:length])


def encrypt_secret(value: Any) -> str:
    raw_value = str(value or "")
    if not raw_value:
        return ""
    nonce = os.urandom(16)
    plaintext = raw_value.encode("utf-8")
    stream = _secret_keystream(nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    signature = hmac.new(_secret_key_bytes(), nonce + ciphertext, hashlib.sha256).digest()[:16]
    return SECRET_TOKEN_PREFIX + base64.urlsafe_b64encode(nonce + signature + ciphertext).decode("ascii")


def decrypt_secret(value: Any) -> str:
    token = str(value or "")
    if not token:
        return ""
    if not token.startswith(SECRET_TOKEN_PREFIX):
        return ""
    try:
        payload = base64.urlsafe_b64decode(token[len(SECRET_TOKEN_PREFIX):].encode("ascii"))
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


def _config_stats(conn, config_id: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM email_outbox
        WHERE config_id = ?
        GROUP BY status
        """,
        (config_id,),
    ).fetchall()
    counts = {str(row["status"] or ""): int(row["count"] or 0) for row in rows}
    latest = conn.execute(
        """
        SELECT status, sent_at, last_error, updated_at
        FROM email_outbox
        WHERE config_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (config_id,),
    ).fetchone()
    return {
        "queued": counts.get("queued", 0),
        "sending": counts.get("sending", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "latest_status": str(latest["status"] or "") if latest else "",
        "latest_error": str(latest["last_error"] or "") if latest else "",
        "latest_at": str(latest["sent_at"] or latest["updated_at"] or "") if latest else "",
    }


def _serialize_email_config(conn, row) -> dict[str, Any]:
    config_id = int(row["id"])
    stats = _config_stats(conn, config_id)
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    provider = str(row["provider"] or "").strip().lower() if "provider" in row_keys else ""
    if provider not in EMAIL_PROVIDER_PRESETS and provider != "custom":
        provider = _infer_email_provider(dict(row))
    provider_preset = EMAIL_PROVIDER_PRESETS.get(provider, {})
    return {
        "id": config_id,
        "teacher_id": int(row["teacher_id"]),
        "provider": provider,
        "provider_label": str(provider_preset.get("label") or ""),
        "label": str(row["label"] or ""),
        "smtp_host": str(row["smtp_host"] or ""),
        "smtp_port": int(row["smtp_port"] or 0),
        "smtp_security": str(row["smtp_security"] or "ssl"),
        "smtp_username": str(row["smtp_username"] or ""),
        "has_smtp_password": bool(row["smtp_password_encrypted"]),
        "from_email": str(row["from_email"] or ""),
        "from_name": str(row["from_name"] or ""),
        "imap_host": str(row["imap_host"] or ""),
        "imap_port": int(row["imap_port"] or 0),
        "imap_security": str(row["imap_security"] or "ssl"),
        "imap_username": str(row["imap_username"] or ""),
        "has_imap_password": bool(row["imap_password_encrypted"]),
        "enabled": bool(row["enabled"]),
        "is_default": bool(row["is_default"]),
        "per_minute_limit": int(row["per_minute_limit"] or EMAIL_DEFAULT_PER_MINUTE_LIMIT),
        "daily_limit": int(row["daily_limit"] or EMAIL_DEFAULT_DAILY_LIMIT),
        "last_status": str(row["last_status"] or "unchecked"),
        "last_status_at": str(row["last_status_at"] or ""),
        "last_error": str(row["last_error"] or ""),
        "sent_success_count": int(row["sent_success_count"] or 0),
        "sent_failure_count": int(row["sent_failure_count"] or 0),
        "last_sent_at": str(row["last_sent_at"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "stats": stats,
    }


def list_teacher_email_configs(conn, teacher_id: int | str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM teacher_email_configs
        WHERE teacher_id = ?
        ORDER BY is_default DESC, enabled DESC, updated_at DESC, id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    return [_serialize_email_config(conn, row) for row in rows]


def get_teacher_email_config(conn, teacher_id: int | str, config_id: int | str):
    row = conn.execute(
        """
        SELECT *
        FROM teacher_email_configs
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (int(config_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        raise ValueError("邮箱配置不存在。")
    return row


def _normalize_config_payload(payload: dict[str, Any], *, existing=None) -> dict[str, Any]:
    raw_provider = str(payload.get("provider") or "").strip().lower()
    provider = _infer_email_provider(payload)
    preset = EMAIL_PROVIDER_PRESETS.get(provider, {})
    from_email = _normalize_email(payload.get("from_email"), required=True)
    provider_domains = {str(domain).lower() for domain in preset.get("domains", ())}
    if provider and provider_domains and _email_domain(from_email) not in provider_domains:
        provider_label = str(preset.get("label") or "所选服务商")
        raise ValueError(f"发信邮箱不是{provider_label}账号，请切换正确服务商或选择自定义邮箱。")
    smtp_username = _normalize_text(payload.get("smtp_username") or (from_email if provider else ""), limit=180)
    imap_username = _normalize_text(payload.get("imap_username") or (from_email if provider else ""), limit=180)
    smtp_security = _normalize_security(payload.get("smtp_security") or preset.get("smtp_security"), "ssl")
    imap_security = _normalize_security(payload.get("imap_security") or preset.get("imap_security"), "ssl")
    smtp_port = _safe_int(payload.get("smtp_port"), 465 if smtp_security == "ssl" else 587)
    imap_port = _safe_int(payload.get("imap_port"), 993 if imap_security == "ssl" else 143)
    if provider and not payload.get("smtp_port"):
        smtp_port = int(preset.get("smtp_port") or smtp_port)
    if provider and not payload.get("imap_port"):
        imap_port = int(preset.get("imap_port") or imap_port)
    per_minute_limit = max(1, min(_safe_int(payload.get("per_minute_limit"), EMAIL_DEFAULT_PER_MINUTE_LIMIT), 120))
    daily_limit = max(per_minute_limit, min(_safe_int(payload.get("daily_limit"), EMAIL_DEFAULT_DAILY_LIMIT), 5000))
    if provider and not payload.get("per_minute_limit"):
        per_minute_limit = max(1, min(int(preset.get("per_minute_limit") or per_minute_limit), 120))
    if provider and not payload.get("daily_limit"):
        daily_limit = max(per_minute_limit, min(int(preset.get("daily_limit") or daily_limit), 5000))

    smtp_password = str(payload.get("smtp_password") or "")
    imap_password = str(payload.get("imap_password") or "")
    keep_existing_smtp = existing is not None and not smtp_password
    keep_existing_imap = existing is not None and not imap_password

    data = {
        "provider": provider or ("custom" if raw_provider == "custom" else ""),
        "label": _normalize_text(payload.get("label") or preset.get("label") or "默认邮箱", limit=80) or "默认邮箱",
        "smtp_host": _normalize_text(payload.get("smtp_host") or preset.get("smtp_host"), limit=180),
        "smtp_port": smtp_port,
        "smtp_security": smtp_security,
        "smtp_username": smtp_username,
        "smtp_password_encrypted": str(existing["smtp_password_encrypted"] or "") if keep_existing_smtp else encrypt_secret(smtp_password),
        "from_email": from_email,
        "from_name": _normalize_text(payload.get("from_name"), limit=80),
        "imap_host": _normalize_text(payload.get("imap_host") or preset.get("imap_host"), limit=180),
        "imap_port": imap_port,
        "imap_security": imap_security,
        "imap_username": imap_username,
        "imap_password_encrypted": str(existing["imap_password_encrypted"] or "") if keep_existing_imap else encrypt_secret(imap_password),
        "enabled": 1 if payload.get("enabled", True) else 0,
        "is_default": 1 if payload.get("is_default", True) else 0,
        "per_minute_limit": per_minute_limit,
        "daily_limit": daily_limit,
    }
    if provider and not data["smtp_password_encrypted"]:
        secret_label = str(preset.get("secret_label") or "邮箱授权码")
        provider_label = str(preset.get("label") or "当前邮箱")
        raise ValueError(f"{provider_label}需要填写{secret_label}，请先在邮箱设置中开启 SMTP/IMAP 服务。")
    return data


def create_teacher_email_config(conn, teacher_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
    data = _normalize_config_payload(payload)
    if not data["smtp_host"]:
        raise ValueError("SMTP 服务器不能为空。")
    teacher_id_int = int(teacher_id)
    has_existing = conn.execute(
        "SELECT 1 FROM teacher_email_configs WHERE teacher_id = ? LIMIT 1",
        (teacher_id_int,),
    ).fetchone()
    if data["is_default"] or not has_existing:
        conn.execute("UPDATE teacher_email_configs SET is_default = 0 WHERE teacher_id = ?", (teacher_id_int,))
        data["is_default"] = 1

    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO teacher_email_configs (
            teacher_id, label, provider, smtp_host, smtp_port, smtp_security, smtp_username,
            smtp_password_encrypted, from_email, from_name, imap_host, imap_port,
            imap_security, imap_username, imap_password_encrypted, enabled, is_default,
            per_minute_limit, daily_limit, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            teacher_id_int,
            data["label"],
            data["provider"],
            data["smtp_host"],
            data["smtp_port"],
            data["smtp_security"],
            data["smtp_username"],
            data["smtp_password_encrypted"],
            data["from_email"],
            data["from_name"],
            data["imap_host"],
            data["imap_port"],
            data["imap_security"],
            data["imap_username"],
            data["imap_password_encrypted"],
            data["enabled"],
            data["is_default"],
            data["per_minute_limit"],
            data["daily_limit"],
            now,
            now,
        ),
    )
    return _serialize_email_config(conn, get_teacher_email_config(conn, teacher_id_int, int(cursor.lastrowid)))


def update_teacher_email_config(conn, teacher_id: int | str, config_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
    existing = get_teacher_email_config(conn, teacher_id, config_id)
    data = _normalize_config_payload(payload, existing=existing)
    if not data["smtp_host"]:
        raise ValueError("SMTP 服务器不能为空。")
    teacher_id_int = int(teacher_id)
    config_id_int = int(config_id)
    if data["is_default"]:
        conn.execute(
            "UPDATE teacher_email_configs SET is_default = 0 WHERE teacher_id = ? AND id != ?",
            (teacher_id_int, config_id_int),
        )
    now = _now_iso()
    conn.execute(
        """
        UPDATE teacher_email_configs
        SET label = ?, provider = ?, smtp_host = ?, smtp_port = ?, smtp_security = ?,
            smtp_username = ?, smtp_password_encrypted = ?, from_email = ?,
            from_name = ?, imap_host = ?, imap_port = ?, imap_security = ?,
            imap_username = ?, imap_password_encrypted = ?, enabled = ?,
            is_default = ?, per_minute_limit = ?, daily_limit = ?, updated_at = ?
        WHERE id = ? AND teacher_id = ?
        """,
        (
            data["label"],
            data["provider"],
            data["smtp_host"],
            data["smtp_port"],
            data["smtp_security"],
            data["smtp_username"],
            data["smtp_password_encrypted"],
            data["from_email"],
            data["from_name"],
            data["imap_host"],
            data["imap_port"],
            data["imap_security"],
            data["imap_username"],
            data["imap_password_encrypted"],
            data["enabled"],
            data["is_default"],
            data["per_minute_limit"],
            data["daily_limit"],
            now,
            config_id_int,
            teacher_id_int,
        ),
    )
    return _serialize_email_config(conn, get_teacher_email_config(conn, teacher_id_int, config_id_int))


def delete_teacher_email_config(conn, teacher_id: int | str, config_id: int | str) -> int:
    get_teacher_email_config(conn, teacher_id, config_id)
    cursor = conn.execute(
        "DELETE FROM teacher_email_configs WHERE id = ? AND teacher_id = ?",
        (int(config_id), int(teacher_id)),
    )
    remaining = conn.execute(
        "SELECT id FROM teacher_email_configs WHERE teacher_id = ? ORDER BY enabled DESC, updated_at DESC, id DESC LIMIT 1",
        (int(teacher_id),),
    ).fetchone()
    if remaining:
        conn.execute(
            "UPDATE teacher_email_configs SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE teacher_id = ?",
            (int(remaining["id"]), int(teacher_id)),
        )
    return int(cursor.rowcount or 0)


def _smtp_connect(config) -> smtplib.SMTP:
    host = str(config["smtp_host"] or "").strip()
    port = int(config["smtp_port"] or 0)
    security = str(config["smtp_security"] or "ssl")
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=EMAIL_SMTP_TIMEOUT_SECONDS)
    client = smtplib.SMTP(host, port, timeout=EMAIL_SMTP_TIMEOUT_SECONDS)
    if security == "starttls":
        client.starttls(context=ssl.create_default_context())
    return client


def _smtp_login_if_needed(client: smtplib.SMTP, config) -> None:
    username = str(config["smtp_username"] or "").strip()
    password = decrypt_secret(config["smtp_password_encrypted"])
    if username:
        client.login(username, password)


def _provider_from_config(config) -> str:
    for key in ("from_email", "smtp_host", "imap_host", "smtp_username", "imap_username"):
        inferred = _provider_for_value(config[key])
        if inferred:
            return inferred
    return ""


def _friendly_email_error(config, exc: Exception, *, mode: str) -> str:
    raw = str(exc)[:500]
    provider = _provider_from_config(config)
    if provider:
        lowered = raw.lower()
        is_auth_error = (
            isinstance(exc, smtplib.SMTPAuthenticationError)
            or "auth" in lowered
            or "login" in lowered
            or "password" in lowered
            or "535" in lowered
        )
        if is_auth_error:
            preset = EMAIL_PROVIDER_PRESETS.get(provider, {})
            provider_label = str(preset.get("label") or "邮箱")
            secret_label = str(preset.get("secret_label") or "邮箱授权码")
            action_label = "发信" if mode == "smtp" else "收信"
            return f"{provider_label}{action_label}验证失败：请确认已开启 SMTP/IMAP 服务，并在密码栏填写{secret_label}。"
    return raw


def _test_smtp_config(config) -> None:
    client = _smtp_connect(config)
    try:
        _smtp_login_if_needed(client, config)
        client.noop()
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def _test_imap_config(config) -> None:
    host = str(config["imap_host"] or "").strip()
    if not host:
        raise ValueError("IMAP 服务器未配置。")
    port = int(config["imap_port"] or 0)
    security = str(config["imap_security"] or "ssl")
    username = str(config["imap_username"] or config["smtp_username"] or "").strip()
    password = decrypt_secret(config["imap_password_encrypted"]) or decrypt_secret(config["smtp_password_encrypted"])
    if not username:
        raise ValueError("IMAP 用户名未配置。")
    if security == "ssl":
        client = imaplib.IMAP4_SSL(host, port, timeout=EMAIL_IMAP_TIMEOUT_SECONDS)
    else:
        client = imaplib.IMAP4(host, port, timeout=EMAIL_IMAP_TIMEOUT_SECONDS)
        if security == "starttls":
            client.starttls(ssl_context=ssl.create_default_context())
    try:
        client.login(username, password)
        client.select("INBOX", readonly=True)
    finally:
        try:
            client.logout()
        except Exception:
            try:
                client.shutdown()
            except Exception:
                pass


def test_teacher_email_config(conn, teacher_id: int | str, config_id: int | str, mode: str = "smtp") -> dict[str, Any]:
    config = get_teacher_email_config(conn, teacher_id, config_id)
    normalized_mode = str(mode or "smtp").strip().lower()
    started_at = time.monotonic()
    try:
        if normalized_mode == "imap":
            _test_imap_config(config)
        else:
            _test_smtp_config(config)
        status = "ok"
        error = ""
    except (OSError, smtplib.SMTPException, imaplib.IMAP4.error, socket.timeout, ValueError) as exc:
        status = "failed"
        error = _friendly_email_error(config, exc, mode="imap" if normalized_mode == "imap" else "smtp")
    now = _now_iso()
    conn.execute(
        """
        UPDATE teacher_email_configs
        SET last_status = ?, last_status_at = ?, last_error = ?, updated_at = ?
        WHERE id = ? AND teacher_id = ?
        """,
        (status, now, error, now, int(config_id), int(teacher_id)),
    )
    return {
        "mode": "imap" if normalized_mode == "imap" else "smtp",
        "status": status,
        "message": "连接正常" if status == "ok" else error,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
        "config": _serialize_email_config(conn, get_teacher_email_config(conn, teacher_id, config_id)),
    }


def _mark_notification_email_status(conn, notification_id: int, status: str, *, job_id: Optional[int] = None) -> None:
    fields = ["email_status = ?"]
    params: list[Any] = [status]
    if job_id is not None:
        fields.append("email_job_id = ?")
        params.append(int(job_id))
    if status == EMAIL_STATUS_QUEUED:
        fields.append("email_queued_at = ?")
        params.append(_now_iso())
    if status == EMAIL_STATUS_SENT:
        fields.append("email_sent_at = ?")
        params.append(_now_iso())
    params.append(int(notification_id))
    conn.execute(
        f"UPDATE message_center_notifications SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )


def _load_recipient_email(conn, *, role: str, user_pk: int) -> Optional[dict[str, str]]:
    table_name = "teachers" if role == "teacher" else "students"
    row = conn.execute(
        f"SELECT id, name, email FROM {table_name} WHERE id = ? LIMIT 1",
        (int(user_pk),),
    ).fetchone()
    if not row:
        return None
    email = _normalize_email(row["email"])
    if not email:
        return None
    return {"email": email, "name": str(row["name"] or "")}


def _resolve_sender_teacher_id(conn, payload: dict[str, Any]) -> Optional[int]:
    recipient_role = str(payload.get("recipient_role") or "").strip().lower()
    if recipient_role == "teacher":
        return _safe_int(payload.get("recipient_user_pk")) or None

    actor_role = str(payload.get("actor_role") or "").strip().lower()
    actor_user_pk = _safe_int(payload.get("actor_user_pk"))
    if actor_role == "teacher" and actor_user_pk:
        return actor_user_pk

    class_offering_id = _safe_int(payload.get("class_offering_id"))
    if class_offering_id:
        row = conn.execute(
            "SELECT teacher_id FROM class_offerings WHERE id = ? LIMIT 1",
            (class_offering_id,),
        ).fetchone()
        if row:
            return _safe_int(row["teacher_id"]) or None
    return None


def _load_default_email_config(conn, teacher_id: int):
    return conn.execute(
        """
        SELECT *
        FROM teacher_email_configs
        WHERE teacher_id = ?
          AND enabled = 1
        ORDER BY is_default DESC, updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()


def _absolute_link(link_url: Any) -> str:
    raw = str(link_url or "").strip()
    if not raw:
        raw = "/profile?section=notifications"
    if raw.startswith(("http://", "https://")):
        return raw
    base = str(PUBLIC_SITE_BASE_URL or "").rstrip("/")
    return f"{base}{raw if raw.startswith('/') else '/' + raw}" if base else raw


def _email_action_url(notification_id: int | str) -> str:
    return _absolute_link(f"/message-center/notifications/{int(notification_id)}/open")


def _email_action_label(category: str) -> str:
    return EMAIL_CATEGORY_ACTION_LABELS.get(str(category or "").strip().lower(), "查看详情")


def _polished_email_copy(payload: dict[str, Any]) -> str:
    category = str(payload.get("category") or "").strip().lower()
    preview = _normalize_text(payload.get("body_preview"), limit=180)
    base_copy = EMAIL_CATEGORY_COPY.get(category, "你有一条新的重要通知，点开即可查看对应内容。")
    if preview:
        return f"{base_copy} 摘要：{preview}"
    return base_copy


def _build_email_content(payload: dict[str, Any], recipient_name: str, *, notification_id: int) -> tuple[str, str]:
    title = _normalize_text(payload.get("title") or "新的通知", limit=160)
    summary = _polished_email_copy(payload)
    category = str(payload.get("category") or "").strip().lower()
    severity = str(payload.get("severity") or notification_severity_for_category(category))
    severity_label = SEVERITY_LABELS.get(severity, severity)
    action_url = _email_action_url(notification_id)
    action_label = _email_action_label(category)
    greeting_name = recipient_name or "同学/老师"
    lines = [
        f"{greeting_name}，你好：",
        "",
        f"你在 {SITE_DISPLAY_NAME} 收到一条{severity_label}。",
        "",
        title,
        "",
        summary,
        "",
        f"{action_label}：{action_url}",
    ]
    lines.extend([
        "",
        "这封邮件由通知中心自动发送。普通通知仅保留站内提醒，重要与系统类通知才会进入邮件队列。",
    ])
    text_body = "\n".join(lines)
    safe_site = html.escape(str(SITE_DISPLAY_NAME or "Lanshare"))
    safe_title = html.escape(title)
    safe_summary = html.escape(summary)
    safe_severity = html.escape(severity_label)
    safe_greeting = html.escape(greeting_name)
    safe_action_label = html.escape(action_label)
    safe_action_url = html.escape(action_url, quote=True)
    html_body = f"""<!doctype html>
<html lang="zh-CN">
<body style="margin:0;padding:0;background:#edf2fb;color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:32px 18px;">
    <div style="border:1px solid #dbe3ef;border-radius:18px;background:#ffffff;padding:28px;box-shadow:0 18px 40px -28px rgba(15,23,42,0.42);">
      <div style="font-size:13px;font-weight:800;color:#4f46e5;margin-bottom:14px;letter-spacing:0.02em;">{safe_site}</div>
      <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:#fff7ed;color:#b45309;font-size:12px;font-weight:800;border:1px solid #fed7aa;">{safe_severity}</div>
      <h1 style="margin:16px 0 10px;font-size:22px;line-height:1.35;color:#0f172a;">{safe_title}</h1>
      <p style="margin:0 0 16px;font-size:15px;line-height:1.8;color:#475569;">{safe_greeting}，你好。</p>
      <p style="margin:0 0 22px;font-size:15px;line-height:1.8;color:#475569;">{safe_summary}</p>
      <a href="{safe_action_url}" style="display:inline-block;padding:12px 18px;border-radius:10px;background:#4f46e5;color:#ffffff;text-decoration:none;font-size:14px;font-weight:800;">{safe_action_label}</a>
      <p style="margin:22px 0 0;font-size:12px;line-height:1.7;color:#64748b;">若按钮无法打开，请复制此链接到浏览器：<br><span style="word-break:break-all;">{safe_action_url}</span></p>
    </div>
    <p style="margin:14px 2px 0;font-size:12px;line-height:1.7;color:#64748b;">普通通知仅保留站内提醒，重要与系统类通知才会发送邮件。</p>
  </div>
</body>
</html>"""
    return text_body, html_body


def queue_notification_email_if_applicable(conn, *, notification_id: int, payload: dict[str, Any]) -> bool:
    category = str(payload.get("category") or "").strip().lower()
    severity = str(payload.get("severity") or notification_severity_for_category(category)).strip().lower()
    if not notification_email_required(category, severity):
        _mark_notification_email_status(conn, int(notification_id), EMAIL_STATUS_NOT_REQUIRED)
        return False

    recipient_role = str(payload.get("recipient_role") or "").strip().lower()
    recipient_user_pk = _safe_int(payload.get("recipient_user_pk"))
    if recipient_role not in {"student", "teacher"} or not recipient_user_pk:
        _mark_notification_email_status(conn, int(notification_id), EMAIL_STATUS_SKIPPED)
        return False

    recipient = _load_recipient_email(conn, role=recipient_role, user_pk=recipient_user_pk)
    if not recipient:
        _mark_notification_email_status(conn, int(notification_id), EMAIL_STATUS_SKIPPED)
        return False

    teacher_id = _resolve_sender_teacher_id(conn, payload)
    if not teacher_id:
        _mark_notification_email_status(conn, int(notification_id), EMAIL_STATUS_NOT_CONFIGURED)
        return False
    config = _load_default_email_config(conn, teacher_id)
    if not config:
        _mark_notification_email_status(conn, int(notification_id), EMAIL_STATUS_NOT_CONFIGURED)
        return False

    title = _normalize_text(payload.get("title") or "新的通知", limit=160)
    subject = f"【{SITE_DISPLAY_NAME}】{title}"
    body_text, body_html = _build_email_content(payload, recipient["name"], notification_id=int(notification_id))
    dedupe_key = f"notification:{int(notification_id)}:{recipient['email']}"
    now = _now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO email_outbox (
            config_id, teacher_id, notification_id, dedupe_key,
            recipient_identity, recipient_role, recipient_user_pk, recipient_email,
            subject, body_text, body_html, category, severity, status, next_attempt_at,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
        """,
        (
            int(config["id"]),
            int(teacher_id),
            int(notification_id),
            dedupe_key,
            str(payload.get("recipient_identity") or f"{recipient_role}:{recipient_user_pk}"),
            recipient_role,
            recipient_user_pk,
            recipient["email"],
            subject,
            body_text,
            body_html,
            category,
            severity,
            now,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM email_outbox WHERE dedupe_key = ? LIMIT 1", (dedupe_key,)).fetchone()
    if row:
        _mark_notification_email_status(conn, int(notification_id), EMAIL_STATUS_QUEUED, job_id=int(row["id"]))
        return True
    return False


def _claim_due_jobs(limit: int = EMAIL_WORKER_BATCH_SIZE) -> list[dict[str, Any]]:
    now = _now_iso()
    stale_cutoff = (datetime.now() - timedelta(minutes=15)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM email_outbox
            WHERE (
                status = 'queued'
                AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ) OR (
                status = 'sending'
                AND (locked_at IS NULL OR locked_at <= ?)
            )
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (now, stale_cutoff, max(1, min(int(limit), 100))),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            cursor = conn.execute(
                """
                UPDATE email_outbox
                SET status = 'sending', locked_at = ?, updated_at = ?
                WHERE id = ?
                  AND (
                    (
                        status = 'queued'
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    ) OR (
                        status = 'sending'
                        AND (locked_at IS NULL OR locked_at <= ?)
                    )
                  )
                """,
                (now, now, int(row["id"]), now, stale_cutoff),
            )
            if cursor.rowcount:
                claimed.append(dict(row))
        conn.commit()
    return claimed


def _rate_limit_delay_seconds(conn, config) -> int:
    config_id = int(config["id"])
    per_minute = max(1, int(config["per_minute_limit"] or EMAIL_DEFAULT_PER_MINUTE_LIMIT))
    daily_limit = max(per_minute, int(config["daily_limit"] or EMAIL_DEFAULT_DAILY_LIMIT))
    now_dt = datetime.now()
    minute_start = (now_dt - timedelta(seconds=60)).isoformat(timespec="seconds")
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    minute_count = int(conn.execute(
        "SELECT COUNT(*) FROM email_outbox WHERE config_id = ? AND status = 'sent' AND sent_at >= ?",
        (config_id, minute_start),
    ).fetchone()[0] or 0)
    if minute_count >= per_minute:
        return 60
    day_count = int(conn.execute(
        "SELECT COUNT(*) FROM email_outbox WHERE config_id = ? AND status = 'sent' AND sent_at >= ?",
        (config_id, day_start),
    ).fetchone()[0] or 0)
    if day_count >= daily_limit:
        return 60 * 60
    return 0


def _send_outbox_message(config, job: dict[str, Any]) -> None:
    sender = str(config["from_email"] or "").strip()
    sender_name = str(config["from_name"] or "").strip()
    message = EmailMessage()
    message["Subject"] = str(job["subject"] or "")
    message["From"] = f"{sender_name} <{sender}>" if sender_name else sender
    message["To"] = str(job["recipient_email"] or "")
    message.set_content(str(job["body_text"] or ""), charset="utf-8")
    body_html = str(job.get("body_html") or "").strip()
    if body_html:
        message.add_alternative(body_html, subtype="html", charset="utf-8")

    client = _smtp_connect(config)
    try:
        _smtp_login_if_needed(client, config)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def process_email_job(job: dict[str, Any]) -> str:
    now = _now_iso()
    with get_db_connection() as conn:
        config = None
        if job.get("config_id"):
            config = conn.execute(
                "SELECT * FROM teacher_email_configs WHERE id = ? AND enabled = 1 LIMIT 1",
                (int(job["config_id"]),),
            ).fetchone()
        if not config:
            next_time = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds")
            conn.execute(
                """
                UPDATE email_outbox
                SET status = 'queued', next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_time, "邮箱配置不可用，稍后重试。", now, int(job["id"])),
            )
            conn.commit()
            return "deferred"

        delay_seconds = _rate_limit_delay_seconds(conn, config)
        if delay_seconds > 0:
            next_time = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE email_outbox SET status = 'queued', next_attempt_at = ?, updated_at = ? WHERE id = ?",
                (next_time, now, int(job["id"])),
            )
            conn.commit()
            return "rate_limited"

    try:
        _send_outbox_message(config, job)
    except Exception as exc:
        error = _friendly_email_error(config, exc, mode="smtp")
        attempt_count = int(job.get("attempt_count") or 0) + 1
        final_failure = attempt_count >= EMAIL_WORKER_MAX_ATTEMPTS
        next_time = None if final_failure else (
            datetime.now() + timedelta(seconds=min(3600, 60 * (2 ** max(attempt_count - 1, 0))))
        ).isoformat(timespec="seconds")
        status = EMAIL_STATUS_FAILED if final_failure else EMAIL_STATUS_QUEUED
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE email_outbox
                SET status = ?, attempt_count = ?, next_attempt_at = ?,
                    locked_at = NULL, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, attempt_count, next_time, error, _now_iso(), int(job["id"])),
            )
            conn.execute(
                """
                UPDATE teacher_email_configs
                SET sent_failure_count = sent_failure_count + 1,
                    last_status = 'failed',
                    last_status_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (_now_iso(), error, _now_iso(), int(config["id"])),
            )
            if final_failure and job.get("notification_id"):
                _mark_notification_email_status(conn, int(job["notification_id"]), EMAIL_STATUS_FAILED, job_id=int(job["id"]))
            conn.commit()
        return "failed" if final_failure else "retry"

    sent_at = _now_iso()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE email_outbox
            SET status = 'sent', sent_at = ?, locked_at = NULL, last_error = '', updated_at = ?
            WHERE id = ?
            """,
            (sent_at, sent_at, int(job["id"])),
        )
        conn.execute(
            """
            UPDATE teacher_email_configs
            SET sent_success_count = sent_success_count + 1,
                last_status = 'ok',
                last_status_at = ?,
                last_error = '',
                last_sent_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (sent_at, sent_at, sent_at, int(config["id"])),
        )
        if job.get("notification_id"):
            _mark_notification_email_status(conn, int(job["notification_id"]), EMAIL_STATUS_SENT, job_id=int(job["id"]))
        conn.commit()
    return "sent"


def process_due_email_jobs_once(limit: int = EMAIL_WORKER_BATCH_SIZE) -> dict[str, int]:
    jobs = _claim_due_jobs(limit)
    result = {"claimed": len(jobs), "sent": 0, "retry": 0, "failed": 0, "deferred": 0, "rate_limited": 0}
    for job in jobs:
        status = process_email_job(job)
        result[status] = result.get(status, 0) + 1
    return result


def update_email_worker_heartbeat(worker_id: str, *, status: str, last_error: str = "") -> None:
    with get_db_connection() as conn:
        queue_depth = int(conn.execute(
            "SELECT COUNT(*) FROM email_outbox WHERE status IN ('queued', 'sending')"
        ).fetchone()[0] or 0)
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO email_worker_heartbeats (worker_id, status, queue_depth, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                status = excluded.status,
                queue_depth = excluded.queue_depth,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (worker_id, status, queue_depth, last_error[:500], now),
        )
        conn.commit()


def email_worker_health_snapshot() -> dict[str, Any]:
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM email_worker_heartbeats
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        queue_depth = int(conn.execute(
            "SELECT COUNT(*) FROM email_outbox WHERE status IN ('queued', 'sending')"
        ).fetchone()[0] or 0)
    if not row:
        return {"ok": False, "queue_depth": queue_depth, "status": "missing", "updated_at": "", "last_error": ""}
    updated_at = datetime.fromisoformat(str(row["updated_at"]))
    ok = (datetime.now() - updated_at).total_seconds() <= EMAIL_WORKER_HEARTBEAT_TIMEOUT_SECONDS
    return {
        "ok": ok,
        "queue_depth": queue_depth,
        "status": str(row["status"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "last_error": str(row["last_error"] or ""),
    }


def run_email_worker_forever(worker_id: str = "mailer") -> None:
    print(f"[EMAIL] worker {worker_id} started")
    update_email_worker_heartbeat(worker_id, status="running")
    while True:
        try:
            result = process_due_email_jobs_once()
            status = "running"
            if result.get("claimed"):
                print(f"[EMAIL] processed batch: {result}")
            update_email_worker_heartbeat(worker_id, status=status)
        except Exception as exc:
            error = str(exc)
            print(f"[EMAIL] worker loop failed: {error}")
            try:
                update_email_worker_heartbeat(worker_id, status="error", last_error=error)
            except Exception:
                pass
        time.sleep(EMAIL_WORKER_POLL_SECONDS)
