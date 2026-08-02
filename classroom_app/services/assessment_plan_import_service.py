"""Parse an uploaded 《课程考核计划表》 into a structured plan + harvest signatures.

Reuses :func:`material_ai_import_service.extract_material_content` to pull text +
page images out of each upload, then asks the AI gateway to map them onto the
assessment-plan shape (fields + 考核项目 + 注释). In addition — and this is the part
the classroom 期末材料 flow never did — it extracts every **embedded signature image**
from the docx and adds it to the signature library with SHA-256 dedup, binding the
命题教师 / 系主任 signatures back onto the plan.

Runs as a background task: a placeholder card shows ``parsing`` until the row is
filled in (with a structured ``import_preview`` so every extracted detail is
visible to the teacher) or flipped to ``failed``.
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
from . import assessment_plan_service as ap
from . import signature_service
from . import signature_workflow_service
from .material_ai_import_service import MAX_VISION_IMAGES, extract_material_content

_TEXT_BUDGET_PER_FILE = 16000
_AI_TIMEOUT = 600.0

# Drawing-ML / VML namespaces for locating embedded images inside docx cells.
_NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_BLIP_EMBED = f"{_NS_R}embed"
_VML_ID = f"{_NS_R}id"


# ---------------------------------------------------------------------------
# AI parse
# ---------------------------------------------------------------------------
def _import_system_prompt(extra_prompt: str) -> str:
    base = (
        "你是广西外国语学院课程考核计划表解析助手。请把提供的文档（正文与/或页面图片）解析成结构化 JSON，"
        "不要 Markdown 代码块。JSON 必须包含 fields 和 assessment_items。"
        "fields 必须尽量包含 school、course_name、class_name、examiner_name、reviewer_name、academic_year、semester、"
        "date、assessment_type(考查/考试)、assessment_mode(non_written/written)、assessment_mode_label(非笔试考核/笔试考核)、"
        "assessment_method、total_score。"
        "course_name 来自“课程名称”单元格；class_name 必须来自“专业年级班级”单元格，不得用课程名称、教学班号或文件名替代；"
        "examiner_name 来自“命题教师”文字姓名，reviewer_name 来自“系（教研室）主任审核签字”处的文字姓名，手写签名图片不要当作文字路径写入字段。"
        "assessment_items 是数组，每项 assessment_form、content、score，必须忠实还原原文的考核形式、考核技能/内容与分值，"
        "不得臆造或改动分值。如果原文分值合计不是 100，也要如实返回原始分值。"
        "assessment_items 只从“考核形式 / 考核技能/内容 / 分值”三列表读取，保持原有行数、顺序和大类描述，不要补入平时成绩、考勤、课堂表现等模板外内容。"
        "命题教师签名、系主任签名是手写图片，无法识别为文字时 examiner_name/reviewer_name 可留空，由系统从签名图片单独入库。"
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
        "new_message": "请解析下面提供的课程考核计划表（正文与/或页面图片），输出规定结构的 JSON。",
        "file_texts": file_texts,
        "base64_urls": images,
        "model_capability": capability,
        "task_type": "document_multimodal_understanding" if images else "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "assessment-plan:import-parse",
    }
    response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
    response.raise_for_status()
    return _json_from_payload(response.json())


# ---------------------------------------------------------------------------
# Signature-image extraction from docx
# ---------------------------------------------------------------------------
def _role_for_label(label: str) -> str:
    text = str(label or "")
    if any(k in text for k in ("命题教师", "命题人", "出题", "命题")):
        return "examiner"
    if any(k in text for k in ("主任", "审核", "系（教研室）", "教研室")):
        return "reviewer"
    return "unknown"


def _image_ext_from_content_type(content_type: str) -> str:
    ct = str(content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    return ".png"


def extract_docx_signature_images(path: Path) -> list[dict[str, Any]]:
    """Return embedded signature images from a docx, mapped to 命题/系主任 roles.

    Each entry: ``{"role", "data", "ext", "label"}``. Robust to bad files — returns
    ``[]`` rather than raising.
    """
    results: list[dict[str, Any]] = []
    try:
        from docx import Document
    except Exception:
        return results
    if path.suffix.lower() != ".docx":
        return results
    try:
        document = Document(str(path))
    except Exception:
        return results

    related = getattr(document.part, "related_parts", {})

    def blob_for_rid(rid: str) -> tuple[bytes, str] | None:
        part = related.get(rid)
        if part is None:
            return None
        try:
            return part.blob, _image_ext_from_content_type(getattr(part, "content_type", ""))
        except Exception:
            return None

    seen_rids: set[str] = set()
    for table in document.tables:
        for row in table.rows:
            cells = list(row.cells)
            for index, cell in enumerate(cells):
                # rId references inside this cell (drawingML blip + VML imagedata)
                rids: list[str] = []
                for element in cell._tc.iter():
                    tag = element.tag.rsplit("}", 1)[-1]
                    if tag == "blip" and element.get(_BLIP_EMBED):
                        rids.append(element.get(_BLIP_EMBED))
                    elif tag == "imagedata" and element.get(_VML_ID):
                        rids.append(element.get(_VML_ID))
                if not rids:
                    continue
                # The role is named by this cell's label, or the preceding label cell.
                label = cell.text or (cells[index - 1].text if index > 0 else "")
                role = _role_for_label(label)
                if role == "unknown" and index > 0:
                    role = _role_for_label(cells[index - 1].text)
                for rid in rids:
                    if not rid or rid in seen_rids:
                        continue
                    seen_rids.add(rid)
                    blob = blob_for_rid(rid)
                    if not blob:
                        continue
                    data, ext = blob
                    if data:
                        results.append({"role": role, "data": data, "ext": ext, "label": (label or "").strip()})
    return results


async def _harvest_signatures(
    conn: Any,
    user: dict[str, Any],
    files: list[dict[str, str]],
    *,
    course_name: str,
    examiner_name: str,
    reviewer_name: str,
) -> dict[str, Any]:
    """Add every embedded signature image to the library (dedup) and bind roles."""
    summary: list[dict[str, Any]] = []
    examiner_signature_id: int | None = None
    reviewer_signature_id: int | None = None
    for item in files:
        path = Path(item.get("path") or "")
        if not path.is_file():
            continue
        try:
            images = extract_docx_signature_images(path)
        except Exception:
            images = []
        for image in images:
            role = image.get("role") or "unknown"
            if role == "examiner":
                subject = examiner_name or "命题教师"
                label = f"{course_name} 命题教师签名" if course_name else "命题教师签名"
            elif role == "reviewer":
                subject = reviewer_name or "系（教研室）主任"
                label = f"{course_name} 系主任审核签名" if course_name else "系主任审核签名"
            else:
                subject = "导入签名"
                label = f"{course_name} 导入签名" if course_name else "导入签名"
            try:
                created = await signature_service.create_signature_from_bytes(
                    conn,
                    user,
                    image["data"],
                    ext=image.get("ext") or ".png",
                    name=label,
                    subject_name=subject,
                    subject_role="teacher",
                    description=f"自考核计划表导入（{image.get('label') or role}）",
                    original_filename=f"{role}_signature{image.get('ext') or '.png'}",
                )
            except Exception as exc:  # noqa: BLE001 — one bad image must not abort import.
                summary.append({"role": role, "error": str(exc)[:160]})
                continue
            signature_id = int(created.get("id") or 0) or None
            summary.append(
                {
                    "role": role,
                    "id": signature_id,
                    "subject_name": created.get("subject_name") or subject,
                    "deduped": bool(created.get("deduped")),
                    "image_url": created.get("image_url"),
                }
            )
            if role == "examiner" and signature_id and not examiner_signature_id:
                examiner_signature_id = signature_id
            elif role == "reviewer" and signature_id and not reviewer_signature_id:
                reviewer_signature_id = signature_id
    return {
        "signatures": summary,
        "examiner_signature_id": examiner_signature_id,
        "reviewer_signature_id": reviewer_signature_id,
    }


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------
def _set_status(plan_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        ap.set_generation_status(conn, plan_id, **kwargs)
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


def _payload_is_empty(fields: dict[str, Any], items: list[Any]) -> bool:
    has_field = any(str(v).strip() for v in (fields or {}).values())
    return not items and not has_field


async def run_import_job(
    plan_id: str,
    files: list[dict[str, str]],
    extra_prompt: str,
    teacher_id: int,
    *,
    cleanup_files: bool = True,
) -> None:
    """Background coroutine: extract → AI parse → harvest signatures → persist."""
    user = {"id": int(teacher_id), "role": "teacher"}
    try:
        _set_status(
            plan_id,
            status="parsing",
            ai_gen_status="running",
            ai_gen_error="",
            progress={"done": 0, "total": 1, "current_label": "正在抽取文档内容…"},
        )
        extracted = await asyncio.to_thread(_extract_files, files)
        if not extracted["file_texts"] and not extracted["images"]:
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="未能从上传文件中提取到任何可解析的文本或图片，请确认文件内容或更换格式。",
            )
            return
        _set_status(plan_id, progress={"done": 0, "total": 1, "current_label": "AI 正在解析考核计划表…"})
        raw = await _parse_with_ai(extracted, extra_prompt)
        if not raw:
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="AI 未返回有效的解析结果（JSON 解析失败），可重试或更换文件。",
            )
            return

        ai_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else (
            raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        )
        ai_items = raw.get("assessment_items")
        if not isinstance(ai_items, list):
            structured = raw.get("structured") if isinstance(raw.get("structured"), dict) else {}
            ai_items = structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else []
        if _payload_is_empty(ai_fields, ai_items):
            _set_status(
                plan_id,
                status="failed",
                ai_gen_status="failed",
                ai_gen_error="AI 解析结果为空（未识别到字段或考核项目），请补充提示后重试。",
            )
            return

        normalized = ap.normalize_plan_payload(ai_fields, ai_items)
        course_name = normalized["fields"].get("course_name") or ""

        # Harvest embedded signature images into the library (dedup) and bind roles.
        _set_status(plan_id, progress={"done": 0, "total": 1, "current_label": "正在归集签名图片…"})
        harvest = {"signatures": [], "examiner_signature_id": None, "reviewer_signature_id": None}
        try:
            with get_db_connection() as conn:
                harvest = await _harvest_signatures(
                    conn,
                    user,
                    files,
                    course_name=course_name,
                    examiner_name=normalized["fields"].get("examiner_name") or "",
                    reviewer_name=normalized["fields"].get("reviewer_name") or "",
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — signatures are best-effort.
            print(f"[ASSESSMENT_PLAN] signature harvest failed plan_id={plan_id}: {exc}")

        warnings = list(extracted.get("warnings") or [])
        if not normalized["score_balanced"]:
            warnings.append(f"原文考核项分值合计为 {normalized['score_total']}，未达到 100，请核对原始分值。")
        import_preview = {
            "fields": normalized["fields"],
            "items": normalized["items"],
            "notes": normalized["notes"],
            "score_total": normalized["score_total"],
            "score_balanced": normalized["score_balanced"],
            "signatures": harvest["signatures"],
            "warnings": warnings[:10],
            "source_files": [item.get("name") for item in files],
        }

        with get_db_connection() as conn:
            signature_bindings = (
                (
                    harvest["examiner_signature_id"],
                    "assessment_plan.examiner_signature",
                    "命题教师签名",
                ),
                (
                    harvest["reviewer_signature_id"],
                    "assessment_plan.reviewer_signature",
                    "审核教师签名",
                ),
            )
            for signature_id, function_point_key, role_label in signature_bindings:
                if not signature_id:
                    continue
                signature_workflow_service.authorize_and_consume_signature_use(
                    conn,
                    user,
                    int(signature_id),
                    function_point_key=function_point_key,
                    context_type="assessment_plan",
                    context_id=str(plan_id),
                    context_label=f"{course_name or '课程考核计划表'} · {role_label}",
                    metadata={"source": "document_import", "role": role_label},
                )
            ap.apply_imported_payload(
                conn,
                plan_id,
                fields=normalized["fields"],
                items=normalized["items"],
                notes=normalized["notes"],
                examiner_signature_id=harvest["examiner_signature_id"],
                reviewer_signature_id=harvest["reviewer_signature_id"],
                import_preview=import_preview,
                title=f"{course_name}（导入）" if course_name else "课程考核计划表（导入）",
            )
            if warnings:
                ap.set_generation_status(
                    conn,
                    plan_id,
                    ai_gen_status="completed_with_fallback",
                    ai_gen_error="；".join(warnings)[:800],
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(
            plan_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"解析失败：{type(exc).__name__}: {str(exc)[:400]}",
        )
    finally:
        if cleanup_files:
            _cleanup(files)
