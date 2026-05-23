from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import (
    AGENT_TASK_DEEPSEEK_HOME,
    AGENT_TASK_RUNTIME_CONFIG_PATH,
    AGENT_TASK_RUNTIME_MODEL,
    AGENT_TASK_RUNTIME_TOKEN,
    AGENT_TASK_RUNTIME_URL,
)
from ..time_utils import local_iso
from .email_notification_service import decrypt_secret, encrypt_secret


KEY_STATUS_VALID = "valid"
KEY_STATUS_FAILED = "failed"
KEY_STATUS_UNCHECKED = "unchecked"
KEY_STATUS_UNAVAILABLE = "unavailable"

DEFAULT_PROVIDER = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = AGENT_TASK_RUNTIME_MODEL or "deepseek-v4-pro"
TEST_MODEL = "deepseek-v4-flash"


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw_value: Any, fallback: Any = None) -> Any:
    if raw_value in (None, ""):
        return fallback
    try:
        return json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _safe_text(value: Any, *, limit: int = 200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:limit]


def _normalize_url(value: Any, *, default: str = DEFAULT_BASE_URL) -> str:
    candidate = str(value or default).strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("API Base URL 格式不正确。")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("DeepSeek API Base URL 必须使用 HTTPS。")
    return candidate


def _normalize_model(value: Any, *, default: str = DEFAULT_MODEL) -> str:
    model = str(value or default).strip()
    if not model:
        raise ValueError("请填写 Agent 使用的模型。")
    if len(model) > 120:
        raise ValueError("模型名称过长。")
    return model


def _normalize_provider(value: Any) -> str:
    provider = str(value or DEFAULT_PROVIDER).strip().lower()
    if provider not in {"deepseek"}:
        raise ValueError("当前 Agent 运行时仅支持 DeepSeek Provider。")
    return provider


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _suffix(api_key: str) -> str:
    normalized = api_key.strip()
    if len(normalized) <= 8:
        return normalized
    return normalized[-8:]


def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def serialize_agent_api_key(row: Any) -> dict[str, Any]:
    item = _row_to_dict(row)
    if not item:
        return {}
    return {
        "id": int(item["id"]),
        "provider": item.get("provider") or DEFAULT_PROVIDER,
        "key_label": item.get("key_label") or "",
        "key_suffix": item.get("key_suffix") or "",
        "base_url": item.get("base_url") or DEFAULT_BASE_URL,
        "model": item.get("model") or DEFAULT_MODEL,
        "enabled": bool(item.get("enabled")),
        "is_active": bool(item.get("is_active")),
        "last_test_status": item.get("last_test_status") or KEY_STATUS_UNCHECKED,
        "last_test_message": item.get("last_test_message") or "",
        "last_test_usage": _json_loads(item.get("last_test_usage_json"), {}) or {},
        "last_test_at": item.get("last_test_at") or "",
        "last_used_at": item.get("last_used_at") or "",
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
    }


def list_agent_api_keys(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM agent_runtime_api_keys
        ORDER BY is_active DESC, updated_at DESC, id DESC
        """
    ).fetchall()
    return [serialize_agent_api_key(row) for row in rows]


def load_agent_api_key_secret(conn, key_id: int) -> tuple[dict[str, Any], str]:
    row = conn.execute(
        "SELECT * FROM agent_runtime_api_keys WHERE id = ? LIMIT 1",
        (int(key_id),),
    ).fetchone()
    if not row:
        raise ValueError("Agent API Key 不存在。")
    item = _row_to_dict(row)
    secret = decrypt_secret(item.get("key_encrypted"))
    if not secret:
        raise ValueError("Agent API Key 无法解密，请重新保存。")
    return item, secret


def get_active_agent_api_key(conn) -> tuple[dict[str, Any], str] | None:
    row = conn.execute(
        """
        SELECT *
        FROM agent_runtime_api_keys
        WHERE provider = ? AND enabled = 1 AND is_active = 1
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (DEFAULT_PROVIDER,),
    ).fetchone()
    if not row:
        return None
    item = _row_to_dict(row)
    secret = decrypt_secret(item.get("key_encrypted"))
    if not secret:
        return None
    return item, secret


async def test_agent_api_key_value(
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = TEST_MODEL,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    normalized_key = str(api_key or "").strip()
    if not normalized_key:
        raise ValueError("请填写 DeepSeek API Key。")
    normalized_base_url = _normalize_url(base_url)
    normalized_model = _normalize_model(model, default=TEST_MODEL)

    payload: dict[str, Any] = {
        "model": normalized_model,
        "messages": [
            {"role": "system", "content": "You are a connectivity check. Reply OK."},
            {"role": "user", "content": "ping"},
        ],
        "max_tokens": 4,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    headers = {
        "Authorization": f"Bearer {normalized_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True) as client:
            response = await client.post(f"{normalized_base_url}/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return {
            "status": KEY_STATUS_UNAVAILABLE,
            "message": f"DeepSeek API 暂时不可达：{exc}",
            "response_ms": int((time.perf_counter() - started_at) * 1000),
            "usage": {},
        }

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        data = response.json()
    except ValueError:
        data = {}

    usage = data.get("usage") if isinstance(data, dict) else {}
    if response.status_code == 200:
        return {
            "status": KEY_STATUS_VALID,
            "message": "DeepSeek API Key 可用。",
            "response_ms": elapsed_ms,
            "usage": usage if isinstance(usage, dict) else {},
        }

    error_payload = data.get("error") if isinstance(data, dict) else {}
    if isinstance(error_payload, dict):
        error_message = error_payload.get("message") or error_payload.get("type")
    else:
        error_message = error_payload
    message = _safe_text(error_message or response.text or f"HTTP {response.status_code}", limit=260)
    return {
        "status": KEY_STATUS_FAILED,
        "message": f"DeepSeek API Key 测试失败：{message}",
        "response_ms": elapsed_ms,
        "usage": usage if isinstance(usage, dict) else {},
    }


def _record_key_test(
    conn,
    *,
    key_id: int,
    result: dict[str, Any],
    checked_by_teacher_id: int | None = None,
) -> None:
    now = local_iso()
    usage_json = _json_dumps(result.get("usage") or {})
    conn.execute(
        """
        UPDATE agent_runtime_api_keys
        SET last_test_status = ?,
            last_test_message = ?,
            last_test_usage_json = ?,
            last_test_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            result.get("status") or KEY_STATUS_UNCHECKED,
            _safe_text(result.get("message"), limit=500),
            usage_json,
            now,
            now,
            int(key_id),
        ),
    )
    conn.execute(
        """
        INSERT INTO agent_runtime_key_checks (
            key_id, status, message, response_ms, usage_json, checked_by_teacher_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(key_id),
            result.get("status") or KEY_STATUS_UNCHECKED,
            _safe_text(result.get("message"), limit=500),
            int(result.get("response_ms") or 0),
            usage_json,
            checked_by_teacher_id,
            now,
        ),
    )


def _write_runtime_config(item: dict[str, Any], secret: str) -> dict[str, Any]:
    AGENT_TASK_DEEPSEEK_HOME.mkdir(parents=True, exist_ok=True)
    base_url = item.get("base_url") or DEFAULT_BASE_URL
    model = item.get("model") or DEFAULT_MODEL
    content = "\n".join(
        [
            "# Managed by LanShare Agent Key Center.",
            "# Restart the DeepSeek-TUI runtime container after changing the active key.",
            'provider = "deepseek"',
            f"api_key = {_toml_string(secret)}",
            f"base_url = {_toml_string(base_url)}",
            f"default_text_model = {_toml_string(model)}",
            f"reasoning_effort = {_toml_string('max')}",
            "show_thinking = true",
            f'cost_currency = "usd"',
            "allow_shell = false",
            "",
            "[providers.deepseek]",
            f"api_key = {_toml_string(secret)}",
            f"base_url = {_toml_string(base_url)}",
            f"model = {_toml_string(model)}",
            "",
        ]
    )
    AGENT_TASK_RUNTIME_CONFIG_PATH.write_text(content, encoding="utf-8")
    return {
        "path": str(AGENT_TASK_RUNTIME_CONFIG_PATH),
        "exists": AGENT_TASK_RUNTIME_CONFIG_PATH.exists(),
        "updated_at": local_iso(),
    }


def sync_active_agent_runtime_config(conn) -> dict[str, Any]:
    active = get_active_agent_api_key(conn)
    if not active:
        return {
            "status": "missing_active_key",
            "message": "尚未启用 Agent API Key。",
            "config_path": str(AGENT_TASK_RUNTIME_CONFIG_PATH),
            "exists": AGENT_TASK_RUNTIME_CONFIG_PATH.exists(),
        }
    item, secret = active
    try:
        write_result = _write_runtime_config(item, secret)
    except OSError as exc:
        return {
            "status": "failed",
            "message": f"运行时配置写入失败：{exc}",
            "config_path": str(AGENT_TASK_RUNTIME_CONFIG_PATH),
        }
    return {
        "status": "synced",
        "message": "运行时配置已写入，重启 deepseek-runtime 后生效。",
        "config_path": write_result["path"],
        "updated_at": write_result["updated_at"],
    }


async def create_agent_api_key(conn, payload: dict[str, Any], *, teacher_id: int) -> dict[str, Any]:
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("请填写 DeepSeek API Key。")
    label = _safe_text(payload.get("key_label") or payload.get("label") or "DeepSeek Agent Key", limit=80)
    provider = _normalize_provider(payload.get("provider"))
    base_url = _normalize_url(payload.get("base_url"))
    model = _normalize_model(payload.get("model"))
    test_model = _normalize_model(payload.get("test_model"), default=TEST_MODEL)
    make_active = bool(payload.get("make_active", True))
    test_on_save = bool(payload.get("test_on_save", True))

    test_result: dict[str, Any] | None = None
    if test_on_save:
        test_result = await test_agent_api_key_value(api_key=api_key, base_url=base_url, model=test_model)
        if test_result.get("status") != KEY_STATUS_VALID:
            return {
                "saved": False,
                "message": test_result.get("message") or "DeepSeek API Key 测试失败，未保存。",
                "test_result": test_result,
                "keys": list_agent_api_keys(conn),
                "runtime_config": sync_active_agent_runtime_config(conn),
            }

    now = local_iso()
    fingerprint = _fingerprint(api_key)
    try:
        cursor = conn.execute(
            """
            INSERT INTO agent_runtime_api_keys (
                provider, key_label, key_fingerprint, key_encrypted, key_suffix,
                base_url, model, enabled, is_active, created_by_teacher_id,
                last_test_status, last_test_message, last_test_usage_json, last_test_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                label,
                fingerprint,
                encrypt_secret(api_key),
                _suffix(api_key),
                base_url,
                model,
                int(teacher_id),
                test_result.get("status") if test_result else KEY_STATUS_UNCHECKED,
                _safe_text(test_result.get("message"), limit=500) if test_result else "",
                _json_dumps(test_result.get("usage") if test_result else {}),
                now if test_result else None,
                now,
                now,
            ),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValueError("这个 API Key 已经保存过。") from exc
        raise

    key_id = int(cursor.lastrowid)
    if test_result:
        conn.execute(
            """
            INSERT INTO agent_runtime_key_checks (
                key_id, status, message, response_ms, usage_json, checked_by_teacher_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_id,
                test_result.get("status") or KEY_STATUS_UNCHECKED,
                _safe_text(test_result.get("message"), limit=500),
                int(test_result.get("response_ms") or 0),
                _json_dumps(test_result.get("usage") or {}),
                int(teacher_id),
                now,
            ),
        )
    if make_active:
        set_active_agent_api_key(conn, key_id)
    runtime_config = sync_active_agent_runtime_config(conn) if make_active else sync_active_agent_runtime_config(conn)
    return {
        "saved": True,
        "message": "Agent API Key 已保存。" if not make_active else "Agent API Key 已保存并设为启用。",
        "key": serialize_agent_api_key(
            conn.execute("SELECT * FROM agent_runtime_api_keys WHERE id = ?", (key_id,)).fetchone()
        ),
        "test_result": test_result,
        "keys": list_agent_api_keys(conn),
        "runtime_config": runtime_config,
    }


async def test_saved_agent_api_key(conn, key_id: int, *, teacher_id: int) -> dict[str, Any]:
    item, secret = load_agent_api_key_secret(conn, key_id)
    result = await test_agent_api_key_value(
        api_key=secret,
        base_url=item.get("base_url") or DEFAULT_BASE_URL,
        model=TEST_MODEL,
    )
    _record_key_test(conn, key_id=int(key_id), result=result, checked_by_teacher_id=int(teacher_id))
    return {
        "message": result.get("message") or "测试完成。",
        "test_result": result,
        "key": serialize_agent_api_key(
            conn.execute("SELECT * FROM agent_runtime_api_keys WHERE id = ?", (int(key_id),)).fetchone()
        ),
        "keys": list_agent_api_keys(conn),
        "runtime_config": sync_active_agent_runtime_config(conn),
    }


def set_active_agent_api_key(conn, key_id: int) -> dict[str, Any]:
    item, secret = load_agent_api_key_secret(conn, key_id)
    now = local_iso()
    conn.execute("UPDATE agent_runtime_api_keys SET is_active = 0, updated_at = ?", (now,))
    conn.execute(
        """
        UPDATE agent_runtime_api_keys
        SET is_active = 1, enabled = 1, updated_at = ?
        WHERE id = ?
        """,
        (now, int(key_id)),
    )
    runtime_config = _write_runtime_config({**item, "is_active": 1, "enabled": 1}, secret)
    row = conn.execute("SELECT * FROM agent_runtime_api_keys WHERE id = ?", (int(key_id),)).fetchone()
    return {
        "message": "Agent API Key 已启用，重启 deepseek-runtime 后运行时会使用该 key。",
        "key": serialize_agent_api_key(row),
        "keys": list_agent_api_keys(conn),
        "runtime_config": {
            "status": "synced",
            "message": "运行时配置已写入，重启 deepseek-runtime 后生效。",
            "config_path": runtime_config["path"],
            "updated_at": runtime_config["updated_at"],
        },
    }


def delete_agent_api_key(conn, key_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_runtime_api_keys WHERE id = ? LIMIT 1", (int(key_id),)).fetchone()
    if not row:
        raise ValueError("Agent API Key 不存在。")
    was_active = bool(row["is_active"])
    conn.execute("DELETE FROM agent_runtime_api_keys WHERE id = ?", (int(key_id),))
    if was_active and AGENT_TASK_RUNTIME_CONFIG_PATH.exists():
        try:
            AGENT_TASK_RUNTIME_CONFIG_PATH.unlink()
        except OSError:
            pass
    runtime_config = sync_active_agent_runtime_config(conn)
    return {
        "message": "Agent API Key 已删除。",
        "keys": list_agent_api_keys(conn),
        "runtime_config": runtime_config,
    }


def build_agent_key_dashboard(conn) -> dict[str, Any]:
    latest_usage = conn.execute(
        """
        SELECT *
        FROM agent_runtime_usage_snapshots
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "keys": list_agent_api_keys(conn),
        "runtime_config": sync_active_agent_runtime_config(conn),
        "runtime": {
            "url": AGENT_TASK_RUNTIME_URL,
            "configured": bool(AGENT_TASK_RUNTIME_URL),
            "usage_snapshot": _json_loads(latest_usage["usage_json"], {}) if latest_usage else {},
            "usage_fetched_at": latest_usage["created_at"] if latest_usage else "",
        },
        "defaults": {
            "provider": DEFAULT_PROVIDER,
            "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL,
            "test_model": TEST_MODEL,
        },
    }


async def fetch_agent_runtime_usage(conn, *, teacher_id: int | None = None) -> dict[str, Any]:
    if not AGENT_TASK_RUNTIME_URL:
        return {
            "status": "not_configured",
            "message": "未配置 AGENT_TASK_RUNTIME_URL，无法读取 DeepSeek-TUI 运行用量。",
            "runtime_url": "",
            "groups": {},
        }

    headers = {"Authorization": f"Bearer {AGENT_TASK_RUNTIME_TOKEN}"} if AGENT_TASK_RUNTIME_TOKEN else {}
    groups: dict[str, Any] = {}
    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            base_url=AGENT_TASK_RUNTIME_URL,
            headers=headers,
            timeout=httpx.Timeout(12.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            for group_by in ("day", "model", "provider", "thread"):
                response = await client.get("/v1/usage", params={"group_by": group_by})
                response.raise_for_status()
                data = response.json()
                groups[group_by] = data if isinstance(data, dict) else {}
    except httpx.HTTPError as exc:
        return {
            "status": KEY_STATUS_UNAVAILABLE,
            "message": f"DeepSeek-TUI 运行时用量暂时不可读：{exc}",
            "runtime_url": AGENT_TASK_RUNTIME_URL,
            "groups": groups,
        }
    except ValueError as exc:
        return {
            "status": KEY_STATUS_UNAVAILABLE,
            "message": f"DeepSeek-TUI 用量响应格式不正确：{exc}",
            "runtime_url": AGENT_TASK_RUNTIME_URL,
            "groups": groups,
        }

    snapshot = {
        "status": "success",
        "message": "DeepSeek-TUI 运行时用量已刷新。",
        "runtime_url": AGENT_TASK_RUNTIME_URL,
        "response_ms": int((time.perf_counter() - started_at) * 1000),
        "groups": groups,
        "fetched_at": local_iso(),
    }
    conn.execute(
        """
        INSERT INTO agent_runtime_usage_snapshots (source, runtime_url, usage_json, fetched_by_teacher_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "deepseek-tui",
            AGENT_TASK_RUNTIME_URL,
            _json_dumps(snapshot),
            teacher_id,
            snapshot["fetched_at"],
        ),
    )
    return snapshot
