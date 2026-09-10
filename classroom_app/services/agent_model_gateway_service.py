"""Bounded, task-authorized DeepSeek gateway; real API keys never enter a runner.

These functions take part in the caller's short transactions and never commit.
Network I/O belongs to the router and must not retain a database connection.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..time_utils import local_iso
from .agent_key_service import DEFAULT_BASE_URL, get_active_agent_api_key

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_TOKENS = 16384
MAX_REQUESTS_PER_TASK = 120
MAX_INFLIGHT_PER_TASK = 2
MAX_REQUEST_SECONDS = 300
ENDPOINT_SCOPES = {"chat/completions": "model:chat", "messages": "model:search"}


@dataclass(frozen=True)
class ModelRequest:
    id: str
    url: str
    secret: str
    model: str
    generation: str
    expires_at: int
    payload: dict[str, Any]

    def __repr__(self):
        return f"ModelRequest(id={self.id!r}, model={self.model!r}, generation={self.generation!r})"


def approved_model_base_url(value: str) -> str:
    """Admin configuration is still bounded by a deployment-owned allowlist."""
    candidate = str(value or DEFAULT_BASE_URL).rstrip("/")
    allowed = {url.strip().rstrip("/") for url in os.getenv(
        "AGENT_MODEL_ALLOWED_BASE_URLS", "https://api.deepseek.com,https://api.deepseek.com/v1,https://api.deepseek.com/anthropic/v1"
    ).split(",") if url.strip()}
    parsed = urlsplit(candidate)
    if (candidate not in allowed or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(503, "模型上游地址未通过部署配置授权。")
    return candidate


def configuration_generation(item: dict[str, Any]) -> str:
    fields = [item.get(k) for k in ("id", "key_fingerprint", "base_url", "model", "updated_at")]
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()[:24]


def validate_model_payload(payload: Any, *, model: str, endpoint: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(422, "模型请求必须是 JSON 对象。")
    if endpoint not in ENDPOINT_SCOPES:
        raise HTTPException(404, "模型接口不存在。")
    # Forward DeepSeek extensions verbatim (thinking, reasoning, tool deltas),
    # but never let request parameters choose the upstream or credentials.
    blocked = {"api_key", "apiKey", "base_url", "baseURL", "url", "headers"} & payload.keys()
    if blocked:
        raise HTTPException(422, "模型请求包含保留配置字段。")
    if "max_completion_tokens" in payload or payload.get("n", 1) != 1:
        raise HTTPException(422, "请使用单份结果与 max_tokens 输出预算。")
    if payload.get("model") != model:
        raise HTTPException(403, "该模型未获本任务授权。")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 2000:
        raise HTTPException(422, "模型消息数量无效。")
    if any(not isinstance(message, dict) for message in messages):
        raise HTTPException(422, "模型消息格式无效。")
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise HTTPException(422, "stream 必须是布尔值。")
    result = dict(payload)
    maximum = result.get("max_tokens", MAX_OUTPUT_TOKENS)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= MAX_OUTPUT_TOKENS:
        raise HTTPException(422, f"单次输出上限为 {MAX_OUTPUT_TOKENS} tokens。")
    result["max_tokens"] = maximum
    if endpoint == "chat/completions" and stream:
        options = result.get("stream_options") or {}
        if not isinstance(options, dict):
            raise HTTPException(422, "stream_options 格式无效。")
        result["stream_options"] = {**options, "include_usage": True}
    return result


def reserve_model_request(conn, token: str, *, endpoint: str, payload: Any) -> ModelRequest:
    from .agent_delegation_service import verify_task_delegation
    from .agent_request_budget_service import reserve_agent_request_budget

    scope = ENDPOINT_SCOPES.get(endpoint)
    if not scope:
        raise HTTPException(404, "模型接口不存在。")
    verified = verify_task_delegation(conn, token, purpose="model", required_scope=scope, lock_task=True)
    active = get_active_agent_api_key(conn)
    if not active:
        raise HTTPException(503, "尚未配置可用的 Agent 模型密钥。")
    item, secret = active
    model = str(item.get("model") or "")
    if endpoint == "messages":
        # Search has its own protocol and explicit configured model. Never infer
        # a paid model from a client-supplied name.
        model = os.getenv("AGENT_MODEL_SEARCH_MODEL", "deepseek-flash").strip()
    body = validate_model_payload(payload, model=model, endpoint=endpoint)
    base_url = approved_model_base_url(item.get("base_url") or DEFAULT_BASE_URL)
    if endpoint == "messages":
        base_url = approved_model_base_url(os.getenv("AGENT_MODEL_SEARCH_BASE_URL", "https://api.deepseek.com/anthropic/v1"))
    task_id = int(verified.task["id"])
    now = int(time.time())
    # A crashed gateway must not consume an in-flight slot forever. The relay
    # enforces the same deadline, so these expired requests cannot still emit.
    conn.execute("UPDATE agent_model_requests SET status = 'failed', completed_at = ? "
                 "WHERE task_id = ? AND status = 'running' AND expires_at <= ?", (local_iso(), task_id, now))
    counts = conn.execute("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0) AS running
        FROM agent_model_requests WHERE task_id = ?
    """, (task_id,)).fetchone()
    if int(counts["total"]) >= MAX_REQUESTS_PER_TASK:
        raise HTTPException(429, "本任务已达到模型请求预算。")
    if int(counts["running"]) >= MAX_INFLIGHT_PER_TASK:
        raise HTTPException(429, "本任务模型并发已满，请稍后重试。")
    request_id = str(uuid.uuid4())
    reserve_agent_request_budget(conn, grant=verified, channel="model", request_id=request_id)
    generation = configuration_generation(item)
    conn.execute("""
        INSERT INTO agent_model_requests
        (id, task_id, attempt_id, fencing_token, actor_role, actor_id, key_id,
         config_generation, endpoint, model, status, output_token_limit, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
    """, (request_id, task_id, verified.attempt["id"], verified.attempt["fencing_token"],
          verified.actor.role, verified.actor.id, item["id"], generation, endpoint, model,
          body["max_tokens"], now + MAX_REQUEST_SECONDS, local_iso()))
    conn.execute("UPDATE agent_runtime_api_keys SET last_used_at = ? WHERE id = ?", (local_iso(), item["id"]))
    return ModelRequest(request_id, f"{base_url}/{endpoint}", secret, model, generation, now + MAX_REQUEST_SECONDS, body)


def finish_model_request(conn, request_id: str, *, status: str, upstream_status: int | None,
                         usage: dict[str, Any] | None = None) -> None:
    from .agent_request_budget_service import finish_agent_request_budget
    if status not in {"completed", "failed", "canceled"}:
        raise ValueError("Invalid model receipt status")
    data = usage if isinstance(usage, dict) else {}

    def count(*names):
        for name in names:
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12:
                return value
        return None

    # Only known scalar counters are persisted, never provider-supplied strings.
    counters = {"prompt_tokens", "input_tokens", "completion_tokens", "output_tokens", "total_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"}
    safe = {k: v for k, v in data.items() if k in counters and isinstance(v, (int, float))
            and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 10**12}
    conn.execute("""
        UPDATE agent_model_requests SET status = ?, upstream_status = ?,
            input_tokens = ?, output_tokens = ?, usage_json = ?, usage_source = ?, completed_at = ?
        WHERE id = ? AND status = 'running'
    """, (status, upstream_status, count("prompt_tokens", "input_tokens"),
          count("completion_tokens", "output_tokens"), json.dumps(safe), "upstream" if safe else None,
          local_iso(), request_id))
    finish_agent_request_budget(conn, request_id, status=status)


class UsageCollector:
    """Bounded incremental SSE observer. Bytes forwarded to DSH stay untouched."""
    def __init__(self):
        self.buffer = b""
        self.usage: dict[str, Any] = {}

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.startswith(b"data:") or len(line) > MAX_RESPONSE_BYTES:
                continue
            try:
                data = json.loads(line[5:].strip())
            except (ValueError, UnicodeError):
                continue
            if not isinstance(data, dict):
                continue
            usage = data.get("usage")
            if not usage and isinstance(data.get("message"), dict):
                usage = data["message"].get("usage")
            if isinstance(usage, dict):
                self.usage.update(usage)
        if len(self.buffer) > MAX_RESPONSE_BYTES:
            raise ValueError("Model stream frame exceeds limit")
