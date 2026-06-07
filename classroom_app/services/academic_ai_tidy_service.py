"""Optional fast-AI tidy-up for messy academic exam/invigilation text.

Most ZF (正方教务) rows parse deterministically into a date + time window. When
a row's time text is irregular and the deterministic parser fails, the fast text
model can extract a clean ``exam_date`` / ``starts_at`` / ``ends_at``.

This is a best-effort enhancement, gated by ``INVIGILATION_AI_TIDY_ENABLED`` and
bounded so it never slows a normal sync: it only runs for rows whose time failed
to parse, is capped per sync, and silently degrades when the AI service is down.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..core import ai_client


def ai_tidy_enabled() -> bool:
    return str(os.getenv("INVIGILATION_AI_TIDY_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}


AI_TIDY_MAX_ITEMS = max(1, int(os.getenv("INVIGILATION_AI_TIDY_MAX_ITEMS", "12")))
AI_TIDY_TIMEOUT_SECONDS = max(3.0, float(os.getenv("INVIGILATION_AI_TIDY_TIMEOUT_SECONDS", "15")))

_SYSTEM_PROMPT = (
    "你是教务考试时间解析助手。用户会给出一段中文考试时间描述，"
    "请抽取出考试日期与起止时间，严格返回 JSON：\n"
    '{"exam_date": "YYYY-MM-DD 或空字符串", "start_time": "HH:MM 或空字符串", "end_time": "HH:MM 或空字符串"}\n'
    "无法确定的字段返回空字符串，不要编造，不要输出额外文字。"
)


def _normalize_time(value: Any) -> str:
    match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return ""


def _normalize_date(value: Any) -> str:
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", str(value or ""))
    if not match:
        return ""
    try:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    except ValueError:
        return ""


async def tidy_exam_time_with_fast_ai(text: str) -> dict[str, str] | None:
    """Return ``{exam_date, starts_at, ends_at}`` parsed by the fast model, or None."""
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return None
    payload = {
        "system_prompt": _SYSTEM_PROMPT,
        "messages": [],
        "new_message": cleaned,
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "background",
        "task_label": "invigilation_time_tidy",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=AI_TIDY_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    parsed = data.get("response_json") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        return None

    exam_date = _normalize_date(parsed.get("exam_date"))
    start_time = _normalize_time(parsed.get("start_time"))
    end_time = _normalize_time(parsed.get("end_time"))
    if not exam_date:
        return None

    starts_at = ""
    ends_at = ""
    if start_time:
        try:
            start_dt = datetime.fromisoformat(f"{exam_date}T{start_time}")
            starts_at = start_dt.isoformat(timespec="minutes")
            if end_time:
                end_dt = datetime.fromisoformat(f"{exam_date}T{end_time}")
                if end_dt < start_dt:
                    end_dt += timedelta(days=1)
                ends_at = end_dt.isoformat(timespec="minutes")
        except ValueError:
            starts_at = ""
            ends_at = ""
    return {"exam_date": exam_date, "starts_at": starts_at, "ends_at": ends_at}
