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

from ..core import ai_client
from ..db.connection import get_db_connection
from . import lesson_plan_prompts as prompts
from . import lesson_plan_service as lp
from .course_planning_service import weekday_label
from .session_material_generation_service import _load_material_text

# Per-call material budget (chars) so a long doc set never blows the context.
_MATERIAL_CHAR_BUDGET = 12000
_NEIGHBOR_CHAR_BUDGET = 1200
_AI_TIMEOUT = 240.0


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------
def _loads_ai_json(text: Any) -> dict[str, Any] | None:
    """Best-effort: pull the first JSON object out of a model reply."""
    if not text:
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
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, json.JSONDecodeError):
            return None
    return None


async def _chat_json(
    *,
    system_prompt: str,
    user_message: str,
    file_texts: list[dict[str, str]] | None = None,
    label: str,
) -> dict[str, Any] | None:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "file_texts": file_texts or [],
        "model_capability": "thinking",
        "task_type": "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": label,
    }
    response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    return _loads_ai_json(data.get("response_text"))


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


def _gather_session_material_text(class_offering_id: int, meta: dict[str, Any], teacher_id: int) -> str:
    """Concatenate the text of every material bound to this session."""
    texts: list[str] = []
    seen: set[int] = set()
    with get_db_connection() as conn:
        primary = int(meta.get("learning_material_id") or 0)
        material_ids: list[int] = []
        if primary:
            material_ids.append(primary)
        try:
            from .session_learning_materials_service import build_material_entries

            entries = build_material_entries(
                conn, class_offering_id, meta["session_id"], teacher_id=teacher_id
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


async def run_generation_job(plan_id: str, class_offering_id: int, teacher_id: int) -> None:
    """Background coroutine: generate the whole plan, persisting per session."""
    try:
        _set_status(plan_id, status="generating", ai_gen_status="running", ai_gen_error="")
        cover = await asyncio.to_thread(_build_cover, class_offering_id, teacher_id)
        metas = await asyncio.to_thread(_read_offering_sessions, class_offering_id)
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
        for index, meta in enumerate(metas, start=1):
            _set_status(
                plan_id,
                progress={
                    "done": index - 1,
                    "total": total,
                    "current_label": meta.get("title") or f"第{index}次课",
                },
            )
            material_text = await asyncio.to_thread(
                _gather_session_material_text, class_offering_id, meta, teacher_id
            )
            chapter = meta.get("title") or ""
            ai_filled = False
            neighbor = _neighbor_context(metas, index - 1)
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
            )
            sessions.append(session_obj)
            _save_progress(plan_id, cover, sessions, {"done": index, "total": total, "current_label": chapter})

        _set_status(
            plan_id,
            status="ready",
            ai_gen_status="completed",
            ai_gen_error="",
            progress={"done": total, "total": total, "current_label": "完成"},
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
        "SELECT id, name, username FROM teachers WHERE id = ? LIMIT 1", (int(teacher_id),)
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
    result = await _chat_json(
        system_prompt=prompts.build_generation_system_prompt(),
        user_message=user_message,
        file_texts=file_texts,
        label="lesson-plan:generate-session",
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
            [str(meta.get("learning_material_id"))] if meta.get("learning_material_id") else []
        ),
        "ai_filled": ai_filled,
    }
