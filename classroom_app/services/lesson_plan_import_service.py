"""Parse uploaded files into a structured 教案 via thinking + multimodal AI.

Reuses :func:`material_ai_import_service.extract_material_content` (which already
handles doc/docx/pdf/png/jpg with LibreOffice/antiword/PDF-render fallbacks) to
pull text + page images out of each upload, then asks the AI gateway to map
them onto the lesson-plan JSON shape (``lesson_plan_prompts.IMPORT_OUTPUT_SCHEMA``).
Runs as a background task: a placeholder card shows ``parsing`` until the row is
filled in or flipped to ``failed`` (with a retry/delete affordance).
"""

from __future__ import annotations

import asyncio
import os
import traceback
from pathlib import Path
from typing import Any

from ..core import ai_client
from ..db.connection import get_db_connection
from . import lesson_plan_prompts as prompts
from . import lesson_plan_service as lp
from .lesson_plan_generation_service import _loads_ai_json
from .material_ai_import_service import MAX_VISION_IMAGES, extract_material_content

_TEXT_BUDGET_PER_FILE = 16000
_AI_TIMEOUT = 300.0


def _extract_files(files: list[dict[str, str]]) -> dict[str, Any]:
    """Synchronously extract text + images from every uploaded file."""
    file_texts: list[dict[str, str]] = []
    images: list[str] = []
    warnings: list[str] = []
    for item in files:
        path = Path(item["path"])
        name = item.get("name") or path.name
        try:
            extraction = extract_material_content(path, name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name}：本地抽取失败（{exc}）")
            continue
        text = (extraction.text or "").strip()
        if text:
            file_texts.append({"name": name, "content": text[:_TEXT_BUDGET_PER_FILE]})
        for image in extraction.images or []:
            data_url = image.get("data_url") if isinstance(image, dict) else None
            if data_url and len(images) < MAX_VISION_IMAGES:
                images.append(data_url)
        warnings.extend(extraction.warnings or [])
    return {"file_texts": file_texts, "images": images[:MAX_VISION_IMAGES], "warnings": warnings}


async def _parse_with_ai(extracted: dict[str, Any], extra_prompt: str) -> dict[str, Any] | None:
    images = extracted["images"]
    file_texts = extracted["file_texts"]
    capability = "vision" if images else "thinking"
    payload = {
        "system_prompt": prompts.build_import_system_prompt(extra_prompt),
        "messages": [],
        "new_message": "请解析下面提供的教案文档（正文与/或页面图片），输出规定结构的 JSON。",
        "file_texts": file_texts,
        "base64_urls": images,
        "model_capability": capability,
        "task_type": "deep_multimodal_reasoning" if images else "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "lesson-plan:import-parse",
    }
    response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    return _loads_ai_json(data.get("response_text"))


def _set_status(plan_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        lp.set_generation_status(conn, plan_id, **kwargs)
        conn.commit()


def _cleanup(files: list[dict[str, str]]) -> None:
    parents: set[str] = set()
    for item in files:
        path = item.get("path", "")
        try:
            os.remove(path)
        except OSError:
            pass
        parent = os.path.dirname(path)
        if parent:
            parents.add(parent)
    for parent in parents:
        try:
            os.rmdir(parent)
        except OSError:
            pass


def _payload_is_empty(payload: dict[str, Any]) -> bool:
    cover = payload.get("cover") or {}
    sessions = payload.get("sessions") or []
    has_cover = any(str(v).strip() for v in cover.values())
    return not sessions and not has_cover


async def run_import_job(
    plan_id: str, files: list[dict[str, str]], extra_prompt: str, teacher_id: int
) -> None:
    """Background coroutine: extract → AI parse → persist → ready/failed."""
    try:
        _set_status(plan_id, status="parsing", ai_gen_status="running", ai_gen_error="")
        extracted = await asyncio.to_thread(_extract_files, files)
        if not extracted["file_texts"] and not extracted["images"]:
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="未能从上传文件中提取到任何可解析的文本或图片，请确认文件内容或更换格式。",
            )
            return
        raw = await _parse_with_ai(extracted, extra_prompt)
        if not raw:
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="AI 未返回有效的解析结果（JSON 解析失败），可重试或更换文件。",
            )
            return
        payload = lp.normalize_lesson_plan_payload(raw)
        if _payload_is_empty(payload):
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="AI 解析结果为空（未识别到封面或任何课次），请补充提示后重试。",
            )
            return
        cover = payload["cover"]
        title = cover.get("course_name") or "导入教案"
        with get_db_connection() as conn:
            lp.update_content(conn, plan_id, cover=cover, sessions=payload["sessions"], status="ready")
            lp.update_attributes(conn, plan_id, title=f"{title}（导入）")
            lp.set_generation_status(
                conn,
                plan_id,
                ai_gen_status="completed",
                ai_gen_error="",
                progress={
                    "done": len(payload["sessions"]),
                    "total": len(payload["sessions"]),
                    "current_label": "完成",
                },
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(
            plan_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"解析失败：{exc}"[:800],
        )
    finally:
        _cleanup(files)
