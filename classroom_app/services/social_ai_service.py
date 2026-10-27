"""AI plumbing for the social second round (social doc §12.2, §12.3).

Every call goes through ``ai_gateway_post`` at priority P2 with a stable
``task_label`` (``social:traits`` / ``social:recs:<bucket>`` /
``social:ai_search:<step>``) and ``business_context.source_feature='social'``;
no database connection is held while the model runs.

``ai_caller`` is injectable (tests never reach a paid model); contract:
``await ai_caller(endpoint, json_payload, **gateway_kwargs) -> dict`` returning
the AI service JSON (``{"status": "success", "response_text"|"response_json": ...}``).
Small ``(counter_key, day)`` counters in ``social_daily_counters`` implement the
daily recommendation AI cap and the per-user AI-search quota.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable, Optional

from .social_common import as_int, ensure_schema, utc_date
from .wanderer_prompt_service import extract_json_object

AI_CHAT_ENDPOINT = "/api/ai/chat"
AI_TIMEOUT_SECONDS = 120.0

AiCaller = Callable[..., Awaitable[dict[str, Any]]]


async def gateway_caller(endpoint: str, json_payload: dict[str, Any], **gateway_kwargs: Any) -> dict[str, Any]:
    from ..core import ai_client
    from .ai_gateway_service import ai_gateway_post

    response = await ai_gateway_post(ai_client, endpoint, json_payload=json_payload, **gateway_kwargs)
    response.raise_for_status()
    return response.json()


# Tests replace this (or pass ``ai_caller``) so no paid model is reached.
default_caller: AiCaller = gateway_caller


def _response_text(data: Any) -> str:
    if not isinstance(data, dict) or data.get("status") != "success":
        raise RuntimeError("AI 服务未返回成功结果")
    text = str(data.get("response_text") or "")
    if text:
        return text
    structured = data.get("response_json")
    if isinstance(structured, (dict, list)):
        return json.dumps(structured, ensure_ascii=False)
    return str(structured or "")


async def call_json(
    *,
    system_prompt: str,
    user_message: str,
    task_type: str,
    task_label: str,
    gateway_task_type: str,
    operation: str = "chat",
    ai_caller: Optional[AiCaller] = None,
    timeout: float = AI_TIMEOUT_SECONDS,
) -> Optional[dict[str, Any]]:
    """One JSON-mode call; the parsed object, or None when the call failed or was unparseable."""
    logical_call_id = f"{task_label}:{uuid.uuid4().hex[:12]}"
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "task_type": task_type,
        "response_format": "json",
        "business_context": {"operation": operation, "source_feature": "social", "logical_call_id": logical_call_id},
        "task_priority": "background",
        "task_label": task_label,
        "web_search_enabled": False,
    }
    caller = ai_caller or default_caller
    try:
        data = await caller(AI_CHAT_ENDPOINT, payload, timeout=timeout, task_type=gateway_task_type, priority="P2",
                            source_ref=logical_call_id)
        return extract_json_object(_response_text(data))
    except Exception as exc:  # noqa: BLE001 - every caller degrades without AI
        print(f"[SOCIAL] AI call {task_label} failed: {type(exc).__name__}")
        return None


# ---------------------------------------------------------------------------
# Daily counters
# ---------------------------------------------------------------------------

def counter_value(conn: Any, key: str, day: Optional[str] = None) -> int:
    ensure_schema(conn)
    row = conn.execute("SELECT value FROM social_daily_counters WHERE counter_key = ? AND counter_day = ?",
                       (key, day or utc_date())).fetchone()
    return as_int(row["value"]) if row else 0


def increment_counter(conn: Any, key: str, delta: int = 1, day: Optional[str] = None) -> int:
    ensure_schema(conn)
    today = day or utc_date()
    conn.execute(
        "INSERT INTO social_daily_counters (counter_key, counter_day, value) VALUES (?, ?, ?) "
        "ON CONFLICT (counter_key, counter_day) DO UPDATE SET value = social_daily_counters.value + excluded.value",
        (key, today, int(delta)),
    )
    return counter_value(conn, key, today)


def reserve(conn: Any, key: str, cap: int) -> bool:
    """Take one unit of ``key`` for today when below ``cap``; an over-cap race is undone. Caller commits."""
    if counter_value(conn, key) >= cap:
        return False
    if increment_counter(conn, key) <= cap:
        return True
    increment_counter(conn, key, -1)
    return False
