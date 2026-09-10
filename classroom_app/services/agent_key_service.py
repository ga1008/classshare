from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx
from fastapi import HTTPException

from ..config import AGENT_MODEL_DEFAULT
from ..db.connection import execute_insert_returning_id
from ..time_utils import local_iso
from .email_notification_service import decrypt_secret, encrypt_secret


KEY_STATUS_VALID = "valid"
KEY_STATUS_FAILED = "failed"
KEY_STATUS_UNCHECKED = "unchecked"
KEY_STATUS_UNAVAILABLE = "unavailable"

DEFAULT_PROVIDER = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = AGENT_MODEL_DEFAULT or "deepseek-v4-pro"
TEST_MODEL = "deepseek-flash"


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
    from .agent_model_gateway_service import approved_model_base_url

    candidate = str(value or default).strip().rstrip("/")
    try:
        approved_model_base_url(candidate)
    except HTTPException as exc:
        raise ValueError("API Base URL 未通过服务器允许列表。") from exc
    if candidate.endswith("/anthropic/v1"):
        raise ValueError("Agent 主模型请使用 OpenAI 兼容地址；搜索接口由服务器单独配置。")
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
        raise ValueError("当前 Agent 模型网关仅支持 DeepSeek Provider。")
    return provider


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _suffix(api_key: str) -> str:
    normalized = api_key.strip()
    if len(normalized) <= 8:
        return ""
    return normalized[-8:]


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
        WHERE deleted_at IS NULL
        ORDER BY is_active DESC, updated_at DESC, id DESC
        """
    ).fetchall()
    return [serialize_agent_api_key(row) for row in rows]


def load_agent_api_key_secret(conn, key_id: int) -> tuple[dict[str, Any], str]:
    row = conn.execute(
        "SELECT * FROM agent_runtime_api_keys WHERE id = ? AND deleted_at IS NULL LIMIT 1",
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
        WHERE provider = ? AND enabled = 1 AND is_active = 1 AND deleted_at IS NULL
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False) as client:
            response = await client.post(f"{normalized_base_url}/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return {
            "status": KEY_STATUS_UNAVAILABLE,
            "message": f"DeepSeek API 暂时不可达（{type(exc).__name__}）。",
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
    message = _safe_text(str(error_message or f"HTTP {response.status_code}").replace(normalized_key, "[redacted]"), limit=260)
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
            last_test_at = ?
        WHERE id = ?
        """,
        (
            result.get("status") or KEY_STATUS_UNCHECKED,
            _safe_text(result.get("message"), limit=500),
            usage_json,
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


def _lock_model_configuration(conn) -> None:
    """Use the same transaction mutex for every selected-key mutation."""
    cursor = conn.execute("UPDATE agent_model_configuration_lock SET revision = revision WHERE id = 1")
    if cursor.rowcount != 1:
        raise RuntimeError("Agent model configuration schema is not installed")


def _configuration_changed(conn) -> None:
    conn.execute("UPDATE agent_model_configuration_lock SET revision = revision + 1 WHERE id = 1")


def get_agent_model_configuration(conn) -> dict[str, Any]:
    """A read-only view: selecting a key is not proof of a serving runtime."""
    from .agent_model_gateway_service import configuration_generation

    row = conn.execute("""
        SELECT id, key_fingerprint, base_url, model, updated_at
        FROM agent_runtime_api_keys
        WHERE provider = ? AND enabled = 1 AND is_active = 1 AND deleted_at IS NULL
        LIMIT 1
    """, (DEFAULT_PROVIDER,)).fetchone()
    item = _row_to_dict(row)
    desired = configuration_generation(item) if item else None
    last = _row_to_dict(conn.execute("""
        SELECT config_generation, status, created_at, completed_at
        FROM agent_model_requests ORDER BY created_at DESC, id DESC LIMIT 1
    """).fetchone())
    observed = _row_to_dict(conn.execute("""
        SELECT config_generation, completed_at FROM agent_model_requests
        WHERE config_generation = ? AND status = 'completed'
        ORDER BY completed_at DESC, id DESC LIMIT 1
    """, (desired or "",)).fetchone())
    status = "missing_active_key" if not item else "observed" if observed else "pending_observation"
    message = {
        "missing_active_key": "尚未启用 Agent API Key。",
        "pending_observation": "已选择模型配置，等待该版本的实际请求成功回执。",
        "observed": "已观测到当前配置的模型请求成功回执。",
    }[status]
    if item:
        message += " 新请求读取当前 Key；切换前已启动的请求可能仍使用旧版本。"
    return {
        "status": status, "message": message, "configured": bool(item),
        "gateway_url": "/api/agent-model", "desired_generation": desired,
        "last_request_generation": last.get("config_generation"),
        "last_request_status": last.get("status"), "last_request_at": last.get("created_at"),
        "last_verified_generation": observed.get("config_generation"),
        "last_verified_at": observed.get("completed_at"),
        "key_id": item.get("id"), "model": item.get("model"), "base_url": item.get("base_url"),
    }


def prepare_agent_api_key(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize before a network probe. The returned secret stays server-side."""
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("请填写 DeepSeek API Key。")
    if len(api_key) > 4096:
        raise ValueError("API Key 过长。")
    return {
        "api_key": api_key,
        "key_label": _safe_text(payload.get("key_label") or payload.get("label") or "DeepSeek Agent Key", limit=80),
        "provider": _normalize_provider(payload.get("provider")),
        "base_url": _normalize_url(payload.get("base_url")),
        "model": _normalize_model(payload.get("model")),
        "make_active": bool(payload.get("make_active", True)),
        "test_on_save": bool(payload.get("test_on_save", True)),
    }


def _key_change_result(conn, *, key_id: int | None = None, **extra) -> dict[str, Any]:
    result = {**extra, "keys": list_agent_api_keys(conn), "runtime_config": get_agent_model_configuration(conn)}
    if key_id is not None:
        result["key"] = serialize_agent_api_key(conn.execute(
            "SELECT * FROM agent_runtime_api_keys WHERE id = ? AND deleted_at IS NULL", (key_id,)
        ).fetchone())
    return result


def create_agent_api_key(conn, payload: dict[str, Any], *, teacher_id: int,
                         test_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist an already-probed key in a short caller-owned transaction.

    The router probes the requested model with no DB connection open. Activation
    always needs a successful probe, even when 'test on save' was unchecked.
    """
    prepared = prepare_agent_api_key(payload)
    requires_probe = prepared["test_on_save"] or prepared["make_active"]
    if requires_probe and (not test_result or test_result.get("status") != KEY_STATUS_VALID):
        return _key_change_result(conn, saved=False,
            message=(test_result or {}).get("message") or "启用前须成功测试当前模型，尚未保存。",
            test_result=test_result)
    _lock_model_configuration(conn)
    fingerprint = _fingerprint(prepared["api_key"])
    if conn.execute("SELECT id FROM agent_runtime_api_keys WHERE key_fingerprint = ?", (fingerprint,)).fetchone():
        raise ValueError("这个 API Key 已经保存过。")
    now = local_iso(timespec="microseconds")
    key_id = execute_insert_returning_id(conn, """
        INSERT INTO agent_runtime_api_keys (
            provider, key_label, key_fingerprint, key_encrypted, key_suffix,
            base_url, model, enabled, is_active, created_by_teacher_id,
            last_test_status, last_test_message, last_test_usage_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, 'unchecked', '', '{}', ?, ?)
    """, (prepared["provider"], prepared["key_label"], fingerprint, encrypt_secret(prepared["api_key"]),
          _suffix(prepared["api_key"]), prepared["base_url"], prepared["model"], int(teacher_id), now, now))
    if test_result:
        _record_key_test(conn, key_id=key_id, result=test_result, checked_by_teacher_id=int(teacher_id))
    if prepared["make_active"]:
        set_active_agent_api_key(conn, key_id)
    return _key_change_result(conn, key_id=key_id, saved=True, test_result=test_result,
        message="Agent API Key 已保存并启用，新请求将使用该配置。" if prepared["make_active"] else "Agent API Key 已保存。")


def record_saved_agent_key_test(conn, key_id: int, *, teacher_id: int,
                                expected_configuration: str, result: dict[str, Any],
                                activate: bool = False) -> dict[str, Any]:
    """Do not apply a delayed probe to a changed/deleted key or revoked admin."""
    from .agent_model_gateway_service import configuration_generation

    _lock_model_configuration(conn)
    item, _ = load_agent_api_key_secret(conn, key_id)
    if configuration_generation(item) != expected_configuration:
        raise HTTPException(409, "测试期间 Key 配置已变更，请刷新后重试。")
    _record_key_test(conn, key_id=key_id, result=result, checked_by_teacher_id=teacher_id)
    activated = activate and result.get("status") == KEY_STATUS_VALID
    if activated:
        set_active_agent_api_key(conn, key_id)
    return _key_change_result(conn, key_id=key_id, activated=activated, test_result=result,
        message="Agent API Key 已启用，新请求将使用该配置。" if activated else result.get("message") or "测试完成。")


def set_active_agent_api_key(conn, key_id: int) -> dict[str, Any]:
    _lock_model_configuration(conn)
    item, _ = load_agent_api_key_secret(conn, key_id)
    _normalize_url(item.get("base_url"))
    if item.get("last_test_status") != KEY_STATUS_VALID:
        raise ValueError("请先成功测试这个 Key 的当前模型。")
    if not (item.get("enabled") and item.get("is_active")):
        now = local_iso(timespec="microseconds")
        conn.execute("UPDATE agent_runtime_api_keys SET is_active = 0 WHERE provider = ? AND is_active = 1", (item["provider"],))
        conn.execute("UPDATE agent_runtime_api_keys SET is_active = 1, enabled = 1, updated_at = ? WHERE id = ?", (now, int(key_id)))
        _configuration_changed(conn)
    return _key_change_result(conn, key_id=key_id, message="Agent API Key 已启用，新请求将使用该配置。")


def delete_agent_api_key(conn, key_id: int) -> dict[str, Any]:
    _lock_model_configuration(conn)
    row = conn.execute("SELECT id, is_active, key_fingerprint FROM agent_runtime_api_keys WHERE id = ? AND deleted_at IS NULL", (int(key_id),)).fetchone()
    if not row:
        raise ValueError("Agent API Key 不存在。")
    now = local_iso(timespec="microseconds")
    # Remove credential material; retain row/check/request references. A tombstone
    # fingerprint permits saving the same credential again with a fresh key id.
    conn.execute("""
        UPDATE agent_runtime_api_keys SET enabled = 0, is_active = 0, key_encrypted = '',
            key_fingerprint = ?, deleted_at = ?, updated_at = ? WHERE id = ?
    """, (f"deleted:{int(key_id)}:{row['key_fingerprint']}", now, now, int(key_id)))
    if row["is_active"]:
        _configuration_changed(conn)
    return _key_change_result(conn, message="Agent API Key 已删除，历史检查和请求记录已保留。")


_USAGE_COLUMNS = """
    COUNT(*) AS turns,
    COUNT(DISTINCT task_id) AS tasks,
    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_requests,
    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_requests,
    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_requests,
    SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) AS canceled_requests,
    COUNT(input_tokens) AS input_reported_requests,
    COUNT(output_tokens) AS output_reported_requests,
    CASE WHEN COUNT(*) > 0 AND COUNT(*) = COUNT(input_tokens) THEN SUM(input_tokens) ELSE NULL END AS input_tokens,
    CASE WHEN COUNT(*) > 0 AND COUNT(*) = COUNT(output_tokens) THEN SUM(output_tokens) ELSE NULL END AS output_tokens,
    SUM(input_tokens) AS reported_input_tokens,
    SUM(output_tokens) AS reported_output_tokens
"""


def _usage_result(row) -> dict[str, Any]:
    item = _row_to_dict(row)
    for name in ("turns", "tasks", "completed_requests", "failed_requests", "running_requests", "canceled_requests",
                 "input_reported_requests", "output_reported_requests"):
        item[name] = int(item.get(name) or 0)
    for name in ("input_tokens", "output_tokens", "reported_input_tokens", "reported_output_tokens"):
        item[name] = int(item[name]) if item.get(name) is not None else None
    # No invented price table or zero for counters the upstream did not report.
    item.update(cost_usd=None, cached_tokens=None, reasoning_tokens=None)
    return item


def get_agent_model_usage(conn) -> dict[str, Any]:
    totals = _usage_result(conn.execute(f"SELECT {_USAGE_COLUMNS} FROM agent_model_requests").fetchone())
    groups = {}
    for name, expression, limit in (("day", "SUBSTR(created_at, 1, 10)", 60), ("model", "model", 100)):
        rows = conn.execute(f"SELECT {expression} AS key, {_USAGE_COLUMNS} FROM agent_model_requests GROUP BY {expression} ORDER BY key DESC LIMIT {limit}").fetchall()
        groups[name] = {"totals": totals, "buckets": [_usage_result(row) for row in reversed(rows)], "bucket_limit": limit}
    return {"status": "success", "message": "已读取模型网关请求账本；未报告的用量显示为未知。",
            "source": "agent_model_requests", "groups": groups, "fetched_at": local_iso()}


def build_agent_key_dashboard(conn) -> dict[str, Any]:
    configuration = get_agent_model_configuration(conn)
    usage = get_agent_model_usage(conn)
    return {
        "keys": list_agent_api_keys(conn), "runtime_config": configuration,
        "runtime": {"url": configuration["gateway_url"], "configured": configuration["configured"],
                    "usage_snapshot": usage, "usage_fetched_at": usage["fetched_at"]},
        "defaults": {"provider": DEFAULT_PROVIDER, "base_url": DEFAULT_BASE_URL,
                     "model": DEFAULT_MODEL, "test_model": DEFAULT_MODEL},
    }


async def fetch_agent_runtime_usage(conn, *, teacher_id: int | None = None) -> dict[str, Any]:
    """Compatibility for the existing refresh API; read-only with no network I/O."""
    return get_agent_model_usage(conn)
