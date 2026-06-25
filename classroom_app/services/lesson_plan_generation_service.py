"""Generate a whole-semester 教案 from a class offering, session by session.

Flow (run as a background asyncio task so the list page can show a placeholder
card that polls progress):

1. Read the offering's ``class_offering_sessions`` (date/week/weekday/节次/章节/
   bound material) and auto-fill the cover.
2. For each session: gather the bound teaching-material text. If a session has
   no bound doc, ask the thinking model to infer its topic/outline from the
   neighbouring sessions first (requirement: "缺文档的课次先 AI 补全").
3. For each session: ask the thinking model to produce the structured 教案
   fields (objectives / key_points / difficulties / methods / means / process /
   side_notes), following the OBE + 两性一度 + 思政 spec.
4. Persist after each session so progress is observable; mark ready/failed.

The thinking model is reached via the same ``ai_client`` → ``/api/ai/chat``
gateway the rest of the app uses (``model_capability="thinking"``).
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from typing import Any

import httpx

from ..core import ai_client
from ..db.connection import get_db_connection
from . import lesson_plan_prompts as prompts
from . import lesson_plan_service as lp
from .academic_service import build_classroom_ai_context
from .course_planning_service import weekday_label
from .session_material_generation_service import _load_material_text
from .session_learning_materials_service import (
    AI_BLURB_GENERATE_LIMIT,
    build_material_entries,
    generate_material_blurb,
    set_blurb as set_session_learning_material_blurb,
)

# Per-call material budget (chars) so a long doc set never blows the context.
_MATERIAL_CHAR_BUDGET = 12000
_NEIGHBOR_CHAR_BUDGET = 1200
_AI_TIMEOUT = 240.0
_AI_RETRY_TIMEOUT = 150.0
_RETRY_USER_MESSAGE_BUDGET = 14000
_RETRY_MATERIAL_BUDGET = 5000
_FALLBACK_TEXT_BUDGET = 900


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _limit_text(value: Any, limit: int) -> str:
    return _safe_text(value)[:limit]


def _compact_file_texts(
    file_texts: list[dict[str, str]] | None,
    *,
    max_items: int = 2,
    content_limit: int = _RETRY_MATERIAL_BUDGET,
) -> list[dict[str, str]]:
    compacted: list[dict[str, str]] = []
    for item in (file_texts or [])[:max_items]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                **item,
                "content": str(item.get("content") or "")[:content_limit],
            }
        )
    return compacted


def _standard_retry_payload(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    """Shrink a thinking-model request so a slow session can continue on fast AI."""
    return {
        **payload,
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "task_label": f"{label}:standard-retry",
        "new_message": str(payload.get("new_message") or "")[:_RETRY_USER_MESSAGE_BUDGET],
        "file_texts": _compact_file_texts(payload.get("file_texts")),
    }


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------
def _loads_ai_json(text: Any) -> dict[str, Any] | None:
    """Best-effort: pull a JSON object out of a model reply."""
    if isinstance(text, dict):
        return text
    if text in (None, ""):
        return None
    raw = str(text).strip()
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start() :])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
            return {"sessions": parsed}
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, json.JSONDecodeError):
            return None
    return None


def _json_from_ai_chat_payload(data: Any) -> dict[str, Any] | None:
    """Handle both current AI gateway JSON mode and legacy text mode."""
    if not isinstance(data, dict):
        return None
    for key in ("response_json", "json", "data"):
        parsed = _loads_ai_json(data.get(key))
        if parsed:
            return parsed
    return _loads_ai_json(data.get("response_text"))


async def _repair_json_text(
    raw_text: Any,
    *,
    schema_hint: dict[str, Any],
    label: str,
) -> dict[str, Any] | None:
    """Use the fast JSON-capable model to normalize malformed-but-useful text."""
    raw = str(raw_text or "").strip()
    if not raw:
        return None
    payload = {
        "system_prompt": prompts.build_json_repair_system_prompt(schema_hint),
        "messages": [],
        "new_message": raw[:24000],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": f"{label}:json-repair",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=90.0)
        response.raise_for_status()
        return _json_from_ai_chat_payload(response.json())
    except Exception:
        return None


async def _chat_json(
    *,
    system_prompt: str,
    user_message: str,
    file_texts: list[dict[str, str]] | None = None,
    label: str,
    model_capability: str = "thinking",
    task_type: str = "deep_text_reasoning",
    schema_hint: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "file_texts": file_texts or [],
        "model_capability": model_capability,
        "task_type": task_type,
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": label,
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status < 500 or model_capability == "standard":
            raise
        retry_payload = _standard_retry_payload(payload, label=label)
        response = await ai_client.post("/api/ai/chat", json=retry_payload, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.TransportError):
        if model_capability == "standard":
            raise
        retry_payload = _standard_retry_payload(payload, label=label)
        response = await ai_client.post("/api/ai/chat", json=retry_payload, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    data = response.json()
    parsed = _json_from_ai_chat_payload(data)
    if parsed:
        return parsed
    return await _repair_json_text(data.get("response_text"), schema_hint=schema_hint or {}, label=label)


# ---------------------------------------------------------------------------
# Offering session reading (sync; runs in a worker thread)
# ---------------------------------------------------------------------------
def _read_offering_sessions(class_offering_id: int) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, order_index, title, session_date, weekday, week_index,
                   academic_section_text, section_count, slot_section_count,
                   learning_material_id
            FROM class_offering_sessions
            WHERE class_offering_id = ?
            ORDER BY order_index, session_date, id
            """,
            (int(class_offering_id),),
        ).fetchall()
        metas: list[dict[str, Any]] = []
        for row in rows:
            row = dict(row)
            sections = str(row.get("academic_section_text") or "").strip()
            week = int(row.get("week_index") or 0)
            weekday = int(row.get("weekday") or 0)
            parts = [str(row.get("session_date") or "").strip()]
            if week:
                parts.append(f"第{week}周")
            if weekday:
                parts.append(weekday_label(weekday))
            if sections:
                parts.append(f"第{sections}节")
            schedule_text = " ".join(p for p in parts if p)
            section_count = int(row.get("section_count") or row.get("slot_section_count") or 2)
            metas.append(
                {
                    "session_id": int(row.get("id") or 0),
                    "order_index": int(row.get("order_index") or 0),
                    "title": str(row.get("title") or "").strip(),
                    "schedule": {
                        "date": str(row.get("session_date") or "").strip(),
                        "week_index": week or None,
                        "weekday": weekday or None,
                        "sections": sections,
                        "text": schedule_text,
                    },
                    "schedule_text": schedule_text,
                    "section_minutes": max(40, section_count * 40),
                    "learning_material_id": int(row.get("learning_material_id") or 0),
                }
            )
        return metas


def _format_generation_schedule_text(row: dict[str, Any]) -> str:
    sections = _safe_text(row.get("academic_section_text"))
    week = _safe_int(row.get("week_index"))
    weekday = _safe_int(row.get("weekday"))
    parts = [_safe_text(row.get("session_date"))]
    if week:
        parts.append(f"week {week}")
    if weekday:
        parts.append(weekday_label(weekday))
    if sections:
        parts.append(f"sections {sections}")
    return " ".join(p for p in parts if p)


def _material_summary(entries: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for entry in entries:
        name = _safe_text(entry.get("name") or entry.get("material_path"))
        blurb = _safe_text(entry.get("ai_blurb"))
        if name and blurb:
            parts.append(f"{name}: {blurb}")
        elif name:
            parts.append(name)
    return " | ".join(parts)[:800]


def read_generation_sessions(class_offering_id: int, teacher_id: int) -> list[dict[str, Any]]:
    """Read classroom sessions as editable generation-plan cards."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, order_index, title, session_date, weekday, week_index,
                   academic_section_text, section_count, slot_section_count,
                   learning_material_id, content, schedule_source,
                   academic_weeks_text, academic_time_text, academic_location,
                   schedule_status, schedule_note
            FROM class_offering_sessions
            WHERE class_offering_id = ?
            ORDER BY order_index, session_date, id
            """,
            (int(class_offering_id),),
        ).fetchall()
        metas: list[dict[str, Any]] = []
        for row in rows:
            row = dict(row)
            schedule_text = _format_generation_schedule_text(row)
            section_count = _safe_int(row.get("section_count") or row.get("slot_section_count"), 2)
            primary_id = _safe_int(row.get("learning_material_id"))
            try:
                material_entries = build_material_entries(
                    conn, int(class_offering_id), int(row["id"]), teacher_id=int(teacher_id)
                )
            except Exception:
                material_entries = []
            material_ids = [
                _safe_int(entry.get("material_id") or entry.get("id"))
                for entry in material_entries
                if _safe_int(entry.get("material_id") or entry.get("id")) > 0
            ]
            if primary_id and primary_id not in material_ids:
                material_ids.insert(0, primary_id)
            title = _safe_text(row.get("title"))
            metas.append(
                {
                    "session_id": int(row.get("id") or 0),
                    "source_session_id": int(row.get("id") or 0),
                    "source_type": "classroom",
                    "order_index": int(row.get("order_index") or 0),
                    "title": title,
                    "chapter": title,
                    "content": _limit_text(row.get("content"), 1200),
                    "schedule": {
                        "date": _safe_text(row.get("session_date")),
                        "week_index": _safe_int(row.get("week_index")) or None,
                        "weekday": _safe_int(row.get("weekday")) or None,
                        "sections": _safe_text(row.get("academic_section_text")),
                        "text": schedule_text,
                    },
                    "schedule_text": schedule_text,
                    "section_minutes": max(40, section_count * 40),
                    "learning_material_id": primary_id,
                    "source_material_ids": [str(mid) for mid in material_ids],
                    "materials": material_entries,
                    "material_summary": _material_summary(material_entries),
                    "prompt_hint": "",
                    "manual_outline": "",
                }
            )
        conn.commit()
        return metas


def _build_classroom_context(class_offering_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        return build_classroom_ai_context(conn, int(class_offering_id)) or {}


def _preview_context_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": snapshot.get("id"),
        "course_name": snapshot.get("course_name") or "",
        "class_name": snapshot.get("class_name") or "",
        "teacher_name": snapshot.get("teacher_name") or "",
        "semester_name": snapshot.get("semester_name") or snapshot.get("semester") or "",
        "textbook_title": snapshot.get("textbook_title") or "",
        "classroom_summary": snapshot.get("classroom_summary") or "",
        "textbook_summary": snapshot.get("textbook_summary") or "",
        "recent_material_names": snapshot.get("recent_material_names") or [],
        "recent_assignment_titles": snapshot.get("recent_assignment_titles") or [],
    }


def serialize_generation_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(sessions, start=1):
        materials = []
        for entry in item.get("materials") or []:
            materials.append(
                {
                    "id": _safe_int(entry.get("material_id") or entry.get("id")),
                    "name": _safe_text(entry.get("name")),
                    "material_path": _safe_text(entry.get("material_path")),
                    "ai_blurb": _safe_text(entry.get("ai_blurb")),
                    "ai_blurb_status": _safe_text(entry.get("ai_blurb_status") or "idle"),
                }
            )
        result.append(
            {
                "client_id": _safe_text(item.get("client_id")) or f"session-{index}",
                "index": index,
                "source_type": _safe_text(item.get("source_type") or "classroom"),
                "source_session_id": _safe_int(item.get("source_session_id") or item.get("session_id")),
                "title": _safe_text(item.get("title")),
                "chapter": _safe_text(item.get("chapter") or item.get("title")),
                "schedule": item.get("schedule") or {},
                "schedule_text": _safe_text(item.get("schedule_text") or (item.get("schedule") or {}).get("text")),
                "section_minutes": _safe_int(item.get("section_minutes"), 80) or 80,
                "material_summary": _safe_text(item.get("material_summary")),
                "materials": materials,
                "source_material_ids": [str(mid) for mid in item.get("source_material_ids") or [] if str(mid).strip()],
                "prompt_hint": _safe_text(item.get("prompt_hint")),
                "manual_outline": _safe_text(item.get("manual_outline") or item.get("material_outline")),
            }
        )
    return result


async def _ensure_material_blurbs(class_offering_id: int, teacher_id: int, sessions: list[dict[str, Any]]) -> bool:
    missing: list[dict[str, Any]] = []
    for session in sessions:
        for entry in session.get("materials") or []:
            if len(missing) >= AI_BLURB_GENERATE_LIMIT:
                break
            if _safe_text(entry.get("ai_blurb")):
                continue
            if _safe_text(entry.get("ai_blurb_status") or "idle") != "idle":
                continue
            if _safe_int(entry.get("row_id")):
                missing.append(entry)
        if len(missing) >= AI_BLURB_GENERATE_LIMIT:
            break
    if not missing:
        return False
    generated: list[tuple[int, str]] = []
    for entry in missing:
        blurb = await generate_material_blurb(
            name=_safe_text(entry.get("name")),
            type_label=_safe_text(entry.get("preview_type") or entry.get("node_type")),
            material_path=_safe_text(entry.get("material_path")),
        )
        generated.append((_safe_int(entry.get("row_id")), blurb))
    with get_db_connection() as conn:
        for row_id, blurb in generated:
            if row_id:
                set_session_learning_material_blurb(
                    conn,
                    row_id,
                    blurb,
                    status="ready" if blurb else "failed",
                )
        conn.commit()
    return True


async def build_generation_plan_preview(class_offering_id: int, teacher_id: int) -> dict[str, Any]:
    cover = await asyncio.to_thread(_build_cover, class_offering_id, teacher_id)
    classroom_context = await asyncio.to_thread(_build_classroom_context, class_offering_id)
    sessions = await asyncio.to_thread(read_generation_sessions, class_offering_id, teacher_id)
    if await _ensure_material_blurbs(class_offering_id, teacher_id, sessions):
        sessions = await asyncio.to_thread(read_generation_sessions, class_offering_id, teacher_id)
    return {
        "cover": cover,
        "classroom": _preview_context_from_snapshot(classroom_context),
        "sessions": serialize_generation_sessions(sessions),
        "session_count": len(sessions),
    }


def normalize_generation_session_plan(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    normalized: list[dict[str, Any]] = []
    for item in items[:60]:
        if not isinstance(item, dict):
            continue
        schedule = item.get("schedule") if isinstance(item.get("schedule"), dict) else {}
        schedule_text = _safe_text(item.get("schedule_text") or schedule.get("text"))
        if schedule_text:
            schedule = {**schedule, "text": schedule_text}
        source_ids = item.get("source_material_ids")
        if not isinstance(source_ids, list):
            source_ids = []
        source_session_id = _safe_int(item.get("source_session_id") or item.get("session_id"))
        chapter = _safe_text(item.get("chapter") or item.get("title"))
        prompt_hint = _limit_text(item.get("prompt_hint"), 500)
        manual_outline = _limit_text(item.get("manual_outline") or item.get("material_outline"), 3000)
        if not (source_session_id or chapter or prompt_hint or manual_outline):
            continue
        normalized.append(
            {
                "client_id": _safe_text(item.get("client_id")) or f"manual-{len(normalized) + 1}",
                "index": len(normalized) + 1,
                "session_id": source_session_id,
                "source_session_id": source_session_id,
                "source_type": "classroom" if source_session_id else "manual",
                "order_index": len(normalized) + 1,
                "title": chapter,
                "chapter": chapter,
                "schedule": schedule,
                "schedule_text": schedule_text,
                "section_minutes": max(40, min(240, _safe_int(item.get("section_minutes"), 80) or 80)),
                "learning_material_id": _safe_int(item.get("learning_material_id")),
                "source_material_ids": [str(mid) for mid in source_ids if str(mid).strip()],
                "materials": item.get("materials") if isinstance(item.get("materials"), list) else [],
                "material_summary": _limit_text(item.get("material_summary"), 1000),
                "prompt_hint": prompt_hint,
                "manual_outline": manual_outline,
                "content": _limit_text(item.get("content"), 1200),
            }
        )
    return normalized


async def draft_manual_session(
    *,
    class_offering_id: int,
    teacher_id: int,
    prompt: str,
    previous_context: str = "",
    next_context: str = "",
) -> dict[str, Any]:
    cover = await asyncio.to_thread(_build_cover, class_offering_id, teacher_id)
    result = await _chat_json(
        system_prompt=prompts.build_session_draft_system_prompt(),
        user_message=prompts.build_session_draft_user_message(
            cover=cover,
            prompt=prompt,
            previous_context=previous_context,
            next_context=next_context,
        ),
        label="lesson-plan:session-draft",
        model_capability="standard",
        task_type="fast_text_response",
        schema_hint={"chapter": "", "material_outline": "", "prompt_hint": ""},
    )
    result = result or {}
    chapter = _limit_text(result.get("chapter") or prompt, 80)
    outline = _limit_text(result.get("material_outline") or result.get("outline") or prompt, 3000)
    hint = _limit_text(result.get("prompt_hint") or prompt, 500)
    return serialize_generation_sessions(
        [
            {
                "client_id": f"manual-{abs(hash((chapter, outline))) % 1000000}",
                "source_type": "manual",
                "chapter": chapter,
                "title": chapter,
                "schedule": {},
                "schedule_text": "",
                "section_minutes": 80,
                "material_summary": outline[:800],
                "manual_outline": outline,
                "prompt_hint": hint,
                "source_material_ids": [],
                "materials": [],
            }
        ]
    )[0]


def _gather_session_material_text(class_offering_id: int, meta: dict[str, Any], teacher_id: int) -> str:
    """Concatenate the text of every material bound to this session."""
    source_session_id = _safe_int(meta.get("source_session_id") or meta.get("session_id"))
    if source_session_id <= 0:
        manual_text = "\n\n".join(
            part
            for part in (
                _safe_text(meta.get("manual_outline")),
                _safe_text(meta.get("material_summary")),
                _safe_text(meta.get("prompt_hint")),
            )
            if part
        )
        return manual_text[:_MATERIAL_CHAR_BUDGET]
    texts: list[str] = []
    seen: set[int] = set()
    with get_db_connection() as conn:
        primary = int(meta.get("learning_material_id") or 0)
        material_ids: list[int] = []
        if primary:
            material_ids.append(primary)
        for raw_id in meta.get("source_material_ids") or []:
            material_id = _safe_int(raw_id)
            if material_id:
                material_ids.append(material_id)
        try:
            entries = build_material_entries(
                conn, class_offering_id, source_session_id, teacher_id=teacher_id
            )
            for entry in entries:
                material_ids.append(int(entry.get("material_id") or 0))
        except Exception:
            pass
        for material_id in material_ids:
            if material_id <= 0 or material_id in seen:
                continue
            seen.add(material_id)
            try:
                content = _load_material_text(conn, material_id)
            except Exception:
                content = ""
            if content.strip():
                texts.append(content.strip())
    combined = "\n\n---\n\n".join(texts)
    return combined[:_MATERIAL_CHAR_BUDGET]


def _offering_homework_hint(class_offering_id: int) -> dict[int, str]:
    """Best-effort: collect assignment titles for the offering (作业参考)."""
    titles: list[str] = []
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT title FROM assignments WHERE class_offering_id = ? ORDER BY id LIMIT 60",
                (int(class_offering_id),),
            ).fetchall()
            titles = [str(dict(r).get("title") or "").strip() for r in rows]
    except Exception:
        titles = []
    titles = [t for t in titles if t]
    return {0: "；".join(titles[:12])} if titles else {}


def _fallback_excerpt(*values: Any, limit: int = _FALLBACK_TEXT_BUDGET) -> str:
    parts: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", _safe_text(value))
        if text:
            parts.append(text)
    return "；".join(parts)[:limit]


def _fallback_topic(meta: dict[str, Any], chapter: str, material_text: str) -> str:
    for value in (
        chapter,
        meta.get("chapter"),
        meta.get("title"),
        meta.get("material_summary"),
        meta.get("manual_outline"),
        material_text,
    ):
        text = _fallback_excerpt(value, limit=80)
        if text:
            return text
    return "本次课核心内容"


def _fallback_process(
    *,
    topic: str,
    section_minutes: int,
    material_hint: str,
    homework_hint: str,
    neighbor: str,
) -> str:
    intro_minutes = 10 if section_minutes >= 70 else 5
    summary_minutes = 10 if section_minutes >= 70 else 5
    practice_minutes = max(20, section_minutes - intro_minutes - summary_minutes)
    context_line = f"前后课衔接：{neighbor}" if neighbor else "前后课衔接：承接上一课知识基础，并为后续实践任务做铺垫。"
    material_line = material_hint or f"围绕“{topic}”梳理概念、步骤、实践任务与常见问题。"
    homework_line = homework_hint or f"完成与“{topic}”相关的基础练习，并记录操作过程、关键结果和问题反思。"
    return "\n".join(
        [
            f"一、教学导入（约{intro_minutes}分钟）",
            f"- 回顾相关知识与课堂任务背景，引出“{topic}”。",
            f"- {context_line}",
            "- 明确本次课的学习产出：能说清关键概念，能完成核心操作，能解释常见错误原因。",
            "",
            f"二、讲授新课与实践训练（约{practice_minutes}分钟）",
            "",
            "| 教学环节 | 教学活动（教师引导） | 学生活动（主体） | 设计意图（OBE & 两性一度） |",
            "| --- | --- | --- | --- |",
            f"| 问题导入 | 结合课程案例提出与“{topic}”相关的真实问题，说明任务目标和评价标准。 | 阅读任务要求，提出已有经验和疑问。 | 以问题驱动学习，帮助学生建立成果导向意识。 |",
            f"| 核心讲解 | 围绕“{topic}”讲解关键概念、流程、命令或代码结构，并结合材料提示：{material_line} | 跟随示范记录关键步骤，标注易错点。 | 强化知识结构，降低实践任务的认知负荷。 |",
            "| 课堂实践 | 组织学生分步完成任务，巡视并针对典型错误进行集中讲评。 | 独立或结对完成实践，提交阶段性结果。 | 通过动手实践形成可观察学习成果，提升解决问题能力。 |",
            "| 拓展提升 | 引导学生比较不同方案的适用场景，讨论安全性、可维护性或工程规范。 | 归纳方案差异，尝试优化自己的实现。 | 增强挑战度和创新性，培养工程思维。 |",
            "",
            f"三、教学小结（约{summary_minutes}分钟）",
            f"- 总结“{topic}”的核心知识、实践步骤和常见问题处理方法。",
            "- 引导学生从技术规范、协作意识和职业责任角度反思本次实践。",
            "",
            "四、作业布置",
            f"- 基础任务：{homework_line}",
            f"- Pro 任务：在基础任务上增加一个扩展场景，说明设计思路、关键步骤和验证结果。",
        ]
    )


def _fallback_session_from_context(
    *,
    cover: dict[str, Any],
    meta: dict[str, Any],
    chapter: str,
    index: int,
    total: int,
    material_text: str,
    homework_hint: str,
    neighbor: str,
    ai_filled: bool,
    error: Exception | None = None,
) -> dict[str, Any]:
    topic = _fallback_topic(meta, chapter, material_text)
    section_minutes = max(40, min(240, _safe_int(meta.get("section_minutes"), 80) or 80))
    material_hint = _fallback_excerpt(
        meta.get("material_summary"),
        meta.get("manual_outline"),
        meta.get("prompt_hint"),
        material_text,
        limit=_FALLBACK_TEXT_BUDGET,
    )
    course_name = _safe_text(cover.get("course_name")) or "本课程"
    return {
        "index": index,
        "schedule": meta.get("schedule"),
        "chapter": chapter or topic,
        "objectives": "\n".join(
            [
                f"知识目标：理解{topic}的核心概念、基本流程和适用场景。",
                f"能力目标：能够结合{course_name}的课堂任务完成与{topic}相关的实践操作，并能定位常见问题。",
                "素养目标：形成规范操作、主动验证、持续改进和负责任使用技术的职业意识。",
            ]
        ),
        "key_points": f"{topic}的关键概念与操作流程；课堂实践任务的完成标准；常见错误的识别与修正。",
        "difficulties": f"{topic}在真实任务中的综合应用；错误现象与原因之间的对应分析；实践结果的验证与表达。",
        "methods": "讲授法、案例法、PBL项目驱动法、任务驱动法、演示法、课堂实践指导",
        "means": "PPT、教学文档、课堂演示、代码或命令示例、在线课堂平台、AI辅助答疑",
        "process": _fallback_process(
            topic=topic,
            section_minutes=section_minutes,
            material_hint=material_hint,
            homework_hint=homework_hint,
            neighbor=neighbor,
        ),
        "side_notes": "课前检查课件、教学文档、演示环境和网络环境。\n课堂中重点观察学生实践进度，对共性问题及时集中讲评。\n课后根据学生提交情况补充案例或微调下一次课的衔接内容。",
        "post_notes": "",
        "source_material_ids": (
            [str(mid) for mid in (meta.get("source_material_ids") or []) if str(mid).strip()]
            or ([str(meta.get("learning_material_id"))] if meta.get("learning_material_id") else [])
        ),
        "ai_filled": ai_filled,
        "ai_fallback": True,
        "ai_fallback_reason": _safe_text(error)[:200] if error else "",
    }


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------
def _set_status(plan_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        lp.set_generation_status(conn, plan_id, **kwargs)
        conn.commit()


def _save_progress(
    plan_id: str, cover: dict[str, Any], sessions: list[dict[str, Any]], progress: dict[str, Any]
) -> None:
    with get_db_connection() as conn:
        lp.update_content(conn, plan_id, cover=cover, sessions=sessions)
        lp.set_generation_status(conn, plan_id, progress=progress)
        conn.commit()


async def run_generation_job(
    plan_id: str,
    class_offering_id: int,
    teacher_id: int,
    session_plan: list[dict[str, Any]] | None = None,
) -> None:
    """Background coroutine: generate the whole plan, persisting per session."""
    try:
        _set_status(plan_id, status="generating", ai_gen_status="running", ai_gen_error="")
        cover = await asyncio.to_thread(_build_cover, class_offering_id, teacher_id)
        classroom_context = await asyncio.to_thread(_build_classroom_context, class_offering_id)
        metas = normalize_generation_session_plan(session_plan) if session_plan else []
        if not metas:
            metas = await asyncio.to_thread(read_generation_sessions, class_offering_id, teacher_id)
        if not metas:
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="该课堂还没有排好课次（class_offering_sessions 为空），请先在课堂安排课次后再生成。",
            )
            return
        homework = await asyncio.to_thread(_offering_homework_hint, class_offering_id)
        total = len(metas)
        sessions: list[dict[str, Any]] = []
        warnings: list[str] = []
        for index, meta in enumerate(metas, start=1):
            _set_status(
                plan_id,
                progress={
                    "done": index - 1,
                    "total": total,
                    "current_label": meta.get("title") or f"第{index}次课",
                },
            )
            chapter = meta.get("title") or ""
            ai_filled = False
            neighbor = _neighbor_context(metas, index - 1)
            material_text = ""
            try:
                material_text = await asyncio.to_thread(
                    _gather_session_material_text, class_offering_id, meta, teacher_id
                )
                if not material_text.strip():
                    filled = await _fill_missing(cover, index, total, neighbor)
                    if filled:
                        chapter = chapter or str(filled.get("chapter") or "")
                        material_text = str(filled.get("outline") or "")
                        ai_filled = True
                session_obj = await _generate_one_session(
                    cover=cover,
                    meta=meta,
                    chapter=chapter,
                    index=index,
                    total=total,
                    material_text=material_text,
                    homework_hint=homework.get(0, ""),
                    neighbor=neighbor,
                    ai_filled=ai_filled,
                    classroom_context=classroom_context,
                )
            except Exception as exc:  # noqa: BLE001 - isolate one failed AI call from the semester job.
                print(
                    "[LESSON_PLAN] session generation fallback "
                    f"plan_id={plan_id} session_index={index}: {type(exc).__name__}: {exc}"
                )
                chapter = chapter or _fallback_topic(meta, "", material_text)
                warnings.append(f"第 {index} 次课 AI 生成超时或失败，已自动使用结构化兜底内容。")
                session_obj = _fallback_session_from_context(
                    cover=cover,
                    meta=meta,
                    chapter=chapter,
                    index=index,
                    total=total,
                    material_text=material_text,
                    homework_hint=homework.get(0, ""),
                    neighbor=neighbor,
                    ai_filled=ai_filled,
                    error=exc,
                )
            sessions.append(session_obj)
            _save_progress(
                plan_id,
                cover,
                sessions,
                {
                    "done": index,
                    "total": total,
                    "current_label": chapter,
                    "warnings": warnings[-5:],
                },
            )

        warning_text = "；".join(warnings[:8])
        _set_status(
            plan_id,
            status="ready",
            ai_gen_status="completed_with_fallback" if warnings else "completed",
            ai_gen_error=warning_text[:800] if warning_text else "",
            progress={"done": total, "total": total, "current_label": "完成", "warnings": warnings[-5:]},
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure on the card
        traceback.print_exc()
        _set_status(
            plan_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"生成失败：{exc}"[:800],
        )


def _build_cover(class_offering_id: int, teacher_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        teacher = _teacher_row(conn, teacher_id)
        return lp.build_cover_from_offering(conn, class_offering_id, teacher=teacher)


def _teacher_row(conn, teacher_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, name, email AS username FROM teachers WHERE id = ? LIMIT 1", (int(teacher_id),)
    ).fetchone()
    if row:
        return dict(row)
    return {"id": int(teacher_id), "name": "", "username": ""}


def _neighbor_context(metas: list[dict[str, Any]], zero_index: int) -> str:
    parts: list[str] = []
    if zero_index - 1 >= 0:
        prev = metas[zero_index - 1]
        parts.append(f"上一次课：{prev.get('title') or '（无标题）'}")
    if zero_index + 1 < len(metas):
        nxt = metas[zero_index + 1]
        parts.append(f"下一次课：{nxt.get('title') or '（无标题）'}")
    return "；".join(parts)[:_NEIGHBOR_CHAR_BUDGET]


async def _fill_missing(cover: dict[str, Any], index: int, total: int, neighbor: str) -> dict[str, Any] | None:
    try:
        return await _chat_json(
            system_prompt=prompts.build_missing_doc_system_prompt(),
            user_message=prompts.build_missing_doc_user_message(
                cover=cover, session_index=index, total_sessions=total, neighbor_context=neighbor
            ),
            label="lesson-plan:fill-missing",
            schema_hint={"chapter": "", "outline": ""},
        )
    except Exception:
        return None


async def _generate_one_session(
    *,
    cover: dict[str, Any],
    meta: dict[str, Any],
    chapter: str,
    index: int,
    total: int,
    material_text: str,
    homework_hint: str,
    neighbor: str,
    ai_filled: bool,
    classroom_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    file_texts = (
        [{"name": "本次课教学材料.md", "content": material_text}] if material_text.strip() else []
    )
    user_message = prompts.build_generation_user_message(
        cover=cover,
        session_index=index,
        total_sessions=total,
        chapter=chapter,
        schedule_text=meta.get("schedule_text", ""),
        section_minutes=int(meta.get("section_minutes") or 80),
        homework_hint=homework_hint,
        neighbor_context=neighbor,
    )
    context_parts: list[str] = []
    classroom_context = classroom_context or {}
    if classroom_context.get("classroom_summary"):
        context_parts.append(str(classroom_context.get("classroom_summary")))
    if classroom_context.get("textbook_summary"):
        context_parts.append(str(classroom_context.get("textbook_summary")))
    recent_materials = classroom_context.get("recent_material_names") or []
    if recent_materials:
        context_parts.append("Recent classroom materials: " + " | ".join(map(str, recent_materials[:12])))
    recent_assignments = classroom_context.get("recent_assignment_titles") or []
    if recent_assignments:
        context_parts.append("Recent assignments: " + " | ".join(map(str, recent_assignments[:12])))
    if meta.get("material_summary"):
        context_parts.append("Current session bound-material summary: " + str(meta.get("material_summary")))
    if meta.get("prompt_hint"):
        context_parts.append("Teacher hint for this session: " + str(meta.get("prompt_hint")))
    if context_parts:
        user_message += "\n\n--- Classroom and textbook context ---\n" + "\n\n".join(context_parts)[:6000]
    result = await _chat_json(
        system_prompt=prompts.build_generation_system_prompt(),
        user_message=user_message,
        file_texts=file_texts,
        label="lesson-plan:generate-session",
        schema_hint=prompts.SESSION_OUTPUT_SCHEMA,
    )
    result = result or {}
    return {
        "index": index,
        "schedule": meta.get("schedule"),
        "chapter": chapter or str(result.get("chapter") or ""),
        "objectives": str(result.get("objectives") or ""),
        "key_points": str(result.get("key_points") or ""),
        "difficulties": str(result.get("difficulties") or ""),
        "methods": str(result.get("methods") or "讲授法、案例法、PBL项目驱动法、手把手实践指导"),
        "means": str(result.get("means") or "PPT、思维导图、虚拟机、Xshell"),
        "process": str(result.get("process") or ""),
        "side_notes": str(result.get("side_notes") or ""),
        "post_notes": "",
        "source_material_ids": (
            [str(mid) for mid in (meta.get("source_material_ids") or []) if str(mid).strip()]
            or ([str(meta.get("learning_material_id"))] if meta.get("learning_material_id") else [])
        ),
        "ai_filled": ai_filled,
    }
