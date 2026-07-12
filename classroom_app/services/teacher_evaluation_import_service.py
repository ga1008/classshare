"""Parse an uploaded 《教师评学表》 into a structured evaluation record.

Reuses :func:`material_ai_import_service.extract_material_content` to pull text +
page images out of each upload, then asks the AI gateway to map them onto the
评学表 shape (template fields + the 10 评价指标 scores + 综合评价 + 学习情况分析). Unlike
the 考核计划表 importer there are no signatures to harvest — the form has none.

Runs as a background task: a placeholder card shows ``parsing`` until the row is
filled in (with a structured ``import_preview`` so every extracted detail is visible
to the teacher) or flipped to ``failed``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from pathlib import Path
from typing import Any

from ..core import ai_client
from ..db.connection import get_db_connection
from . import teacher_evaluation_service as te
from .material_ai_import_service import MAX_VISION_IMAGES, extract_material_content

_TEXT_BUDGET_PER_FILE = 16000
_AI_TIMEOUT = 600.0


# ---------------------------------------------------------------------------
# AI parse
# ---------------------------------------------------------------------------
def _import_system_prompt(extra_prompt: str) -> str:
    base = (
        "你是广西外国语学院教师评学表解析助手。请把提供的文档（正文与/或页面图片）解析成结构化 JSON，"
        "不要 Markdown 代码块。JSON 必须包含 fields、scores 和 analysis 三个键。"
        "fields 必须尽量包含 course_name(课程名称)、class_name(授课班级)、college(所在二级学院)、"
        "teacher_name(任课教师)、teacher_title(教师职称)、evaluate_date(评价时间)、academic_year(学年，如2025-2026)、"
        "semester(学期，第一学期/第二学期)。"
        "scores 是长度为 10 的数组，依次对应表格中第 1 到第 10 项指标的“评价得分”单元格数值（每项满分 10）；"
        "如果某格为空，用 null；不要臆造分数，忠实还原原表。"
        "analysis 是“对学生学习情况的分析和今后教学改革建议”栏的纯文本内容，如为空则返回空字符串。"
        "综合评价（优秀/良好/一般/较差）无需返回，系统会根据总分自动计算。"
    )
    if str(extra_prompt or "").strip():
        base += f"\n教师补充说明：{extra_prompt.strip()}"
    return base


def _loads_ai_json(text: Any) -> dict[str, Any] | None:
    if isinstance(text, dict):
        return text
    if text in (None, ""):
        return None
    raw = str(text).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{]", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start():])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_from_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    for key in ("response_json", "json", "data"):
        parsed = _loads_ai_json(data.get(key))
        if parsed:
            return parsed
    return _loads_ai_json(data.get("response_text"))


def _extract_files(files: list[dict[str, str]]) -> dict[str, Any]:
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
        "system_prompt": _import_system_prompt(extra_prompt),
        "messages": [],
        "new_message": "请解析下面提供的教师评学表（正文与/或页面图片），输出规定结构的 JSON。",
        "file_texts": file_texts,
        "base64_urls": images,
        "model_capability": capability,
        "task_type": "document_multimodal_understanding" if images else "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "teacher-evaluation:import-parse",
    }
    response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
    response.raise_for_status()
    return _json_from_payload(response.json())


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------
def _set_status(evaluation_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        te.set_generation_status(conn, evaluation_id, **kwargs)
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


def _payload_is_empty(fields: dict[str, Any], scores: list[Any], analysis: str) -> bool:
    has_field = any(str(v).strip() for v in (fields or {}).values())
    has_score = any(str(s).strip() for s in (scores or []) if s is not None)
    return not has_field and not has_score and not str(analysis or "").strip()


async def run_import_job(
    evaluation_id: str,
    files: list[dict[str, str]],
    extra_prompt: str,
    teacher_id: int,
    *,
    cleanup_files: bool = True,
) -> None:
    """Background coroutine: extract → AI parse → persist."""
    try:
        _set_status(
            evaluation_id,
            status="parsing",
            ai_gen_status="running",
            ai_gen_error="",
            progress={"done": 0, "total": 1, "current_label": "正在抽取文档内容…"},
        )
        extracted = await asyncio.to_thread(_extract_files, files)
        if not extracted["file_texts"] and not extracted["images"]:
            _set_status(
                evaluation_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="未能从上传文件中提取到任何可解析的文本或图片，请确认文件内容或更换格式。",
            )
            return
        _set_status(evaluation_id, progress={"done": 0, "total": 1, "current_label": "AI 正在解析评学表…"})
        raw = await _parse_with_ai(extracted, extra_prompt)
        if not raw:
            _set_status(
                evaluation_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="AI 未返回有效的解析结果（JSON 解析失败），可重试或更换文件。",
            )
            return

        ai_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else (
            raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        )
        raw_scores = raw.get("scores") if isinstance(raw.get("scores"), list) else []
        analysis = str(raw.get("analysis") or "")
        if _payload_is_empty(ai_fields, raw_scores, analysis):
            _set_status(
                evaluation_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="AI 解析结果为空（未识别到字段、分数或评语），请补充提示后重试。",
            )
            return

        items = [{"score": ("" if score is None else score)} for score in raw_scores]
        normalized = te.normalize_evaluation_payload(ai_fields, items, analysis)
        course_name = normalized["fields"].get("course_name") or ""

        warnings = list(extracted.get("warnings") or [])
        missing = te.missing_fields(normalized)
        if missing:
            warnings.append("导入后仍需补全：" + "、".join(missing))
        import_preview = {
            "fields": normalized["fields"],
            "items": normalized["items"],
            "analysis": normalized["analysis"],
            "score_total": normalized["score_total"],
            "rating": normalized["rating"],
            "warnings": warnings[:10],
            "source_files": [item.get("name") for item in files],
        }

        with get_db_connection() as conn:
            te.apply_generated_payload(
                conn,
                evaluation_id,
                fields=normalized["fields"],
                items=normalized["items"],
                analysis=normalized["analysis"],
                import_preview=import_preview,
                title=f"{course_name}（导入）" if course_name else "教师评学表（导入）",
                ai_gen_status="completed_with_fallback" if warnings else "completed",
                ai_gen_error="；".join(warnings)[:800] if warnings else "",
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(
            evaluation_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"解析失败：{type(exc).__name__}: {str(exc)[:400]}",
        )
    finally:
        if cleanup_files:
            _cleanup(files)
