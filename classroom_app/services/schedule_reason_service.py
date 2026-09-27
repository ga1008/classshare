"""调课原因自动填写 (short reason suggestion for a timetable adjustment).

Fast-text AI (``fast_text_response``) writes one short sentence from the
adjustment context; when the AI gateway is unavailable a rule-based fallback
produces a sensible reason so the flow never blocks. Nothing here writes to
教务 — the teacher can still edit the text before saving.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from ..config import AI_ASSISTANT_URL

logger = logging.getLogger(__name__)

REASON_MAX_CHARS = 40
AI_TIMEOUT_SECONDS = 12.0
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

SYSTEM_PROMPT = (
    "你是高校教务系统的调课申请助手。根据给出的课程、原上课时间、拟调整时间和校历信息，"
    "写一句简短、得体、可直接提交给教务处的调课原因（不超过 30 个汉字），只输出这一句，不要标点以外的任何前后缀。"
    "如果原上课日是法定节假日，原因应说明因放假需要顺延/调整；如果拟调整日是调休上课日，说明按调休安排安排上课；"
    "如果只是更换教室，说明教室调整原因；其他情况写“因教学安排调整”一类的中性表述。"
)


def _weekday(iso: str) -> str:
    try:
        return WEEKDAY_LABELS[date.fromisoformat(iso).weekday()]
    except (TypeError, ValueError):
        return ""


def _slot_text(slot: dict[str, Any]) -> str:
    if not slot:
        return "未知"
    sections = slot.get("sections") or []
    section = f"第{sections[0]}-{sections[-1]}节" if len(sections) > 1 else (f"第{sections[0]}节" if sections else "")
    date_text = str(slot.get("date") or "")
    week = f"第{slot.get('week')}周" if slot.get("week") else ""
    return " ".join(part for part in (week, date_text, _weekday(date_text), section, str(slot.get("room") or "")) if part)


def fallback_reason(context: dict[str, Any]) -> str:
    """Deterministic reason from the calendar facts (used when AI is unavailable)."""
    original, proposed = context.get("original") or {}, context.get("proposed") or {}
    original_day, proposed_day = context.get("original_day") or {}, context.get("proposed_day") or {}
    same_time = (original.get("date"), list(original.get("sections") or [])) == (proposed.get("date"), list(proposed.get("sections") or []))
    if same_time:
        return "因教学需要更换上课教室"
    if original_day.get("kind") == "holiday":
        return f"原上课日为{original_day.get('label') or '法定节假日'}放假，课程顺延调整"
    if proposed_day.get("kind") == "workday":
        return f"按{proposed_day.get('label') or '调休上课'}安排，课程调整至该日"
    if original_day.get("kind") == "workday":
        return "原上课日为调休上课日，按实际课表调整"
    return "因教学安排调整，课程调换上课时间"


def _strip_reason(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("\n", " ").split())
    for quote in ("“", "”", '"', "「", "」"):
        cleaned = cleaned.replace(quote, "")
    cleaned = cleaned.strip(" 。.！!")
    return cleaned[:REASON_MAX_CHARS]


async def suggest_reason(context: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"reason", "source"}``; ``source`` is ``ai`` or ``fallback``."""
    fallback = fallback_reason(context)
    original, proposed = context.get("original") or {}, context.get("proposed") or {}
    facts = [
        f"课程：{context.get('course_name') or ''} {context.get('class_label') or ''}",
        f"原安排：{_slot_text(original)}",
        f"拟调整：{_slot_text(proposed)}",
    ]
    for label, day in (("原上课日校历", context.get("original_day")), ("拟调整日校历", context.get("proposed_day"))):
        if day:
            facts.append(f"{label}：{day.get('kind')} {day.get('label') or ''}".strip())
    if context.get("note"):
        facts.append(f"教师备注：{str(context['note'])[:80]}")
    try:
        async with httpx.AsyncClient(base_url=AI_ASSISTANT_URL, timeout=AI_TIMEOUT_SECONDS) as client:
            response = await client.post("/api/ai/chat", json={
                "system_prompt": SYSTEM_PROMPT, "messages": [], "new_message": "\n".join(facts),
                "model_capability": "fast", "task_type": "fast_text_response", "task_priority": "interactive",
                "task_label": "schedule_editor_reason",
            })
            response.raise_for_status()
            payload = response.json()
        text = payload.get("response_text") if isinstance(payload, dict) else ""
        if not text and isinstance(payload, dict):
            text = payload.get("response") or payload.get("content") or ""
        reason = _strip_reason(str(text or ""))
        if 4 <= len(reason) <= REASON_MAX_CHARS:
            return {"reason": reason, "source": "ai"}
    except Exception as exc:  # AI is optional here; never block the editor
        logger.info("schedule reason suggestion fell back: %s", exc)
    return {"reason": fallback, "source": "fallback"}
