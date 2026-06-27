"""Generate a 《课程考核计划表》 from a class offering via the thinking model.

Method two of the assessment-plan feature: the teacher picks a class offering and
the deep-thinking model integrates the课堂/教材/教务 context into a complete plan
(考核项目 + 分值，合计 100). Runs as a background asyncio task so the list page can
show a placeholder card that polls progress; on AI failure it falls back to the
local structured draft (``build_final_material_generation_seed``) so the closed
loop never leaves the teacher empty-handed.

Signatures: after generation the 命题教师 is auto-bound to the teacher's own latest
signature (if any); the 系主任 is intentionally left blank for offline signing.
"""

from __future__ import annotations

import json
import re
import traceback
from typing import Any

import httpx

from ..core import ai_client
from ..db.connection import get_db_connection
from . import assessment_plan_service as ap
from .material_final_document_service import build_final_material_generation_seed

_AI_TIMEOUT = 240.0
_AI_RETRY_TIMEOUT = 150.0
_PROCESS_ASSESSMENT_TERMS = (
    "平时",
    "考勤",
    "课堂表现",
    "课堂互动",
    "课堂参与",
    "课后",
    "书面作业",
    "编程作业",
    "作业",
    "阶段性",
    "过程性",
)


# ---------------------------------------------------------------------------
# Prompts (self-contained; aligned with the 期末材料 assessment_plan prompt)
# ---------------------------------------------------------------------------
def _system_prompt() -> str:
    return (
        "你是广西外国语学院课程考核计划表模板填写助手。你的任务不是自由撰写材料，而是只为固定模板补齐字段和考核项目。"
        "必须严格返回 JSON 对象，不要 Markdown 代码块。"
        "JSON 必须包含 fields 和 assessment_items 两个键。"
        "fields 必须包含 school、course_name、class_name、teacher_name、examiner_name、reviewer_name、"
        "academic_year、semester、date、assessment_type、assessment_mode、assessment_mode_label、assessment_method、total_score。"
        "assessment_type 只能是“考查”或“考试”。如果教务或课堂信息显示考查，assessment_mode 必须是 non_written、"
        "assessment_mode_label 必须是“非笔试考核”。assessment_method 写具体形式，例如“机试”“闭卷笔试”“项目实操”。"
        "reviewer_name 留空（系主任线下手写签名）。"
        "assessment_items 必须是数组，每项包含 assessment_form、content、score；分值合计必须严格等于 100，数量控制在 3-6 项。"
        "assessment_items 只描述期末考试/期末考核本身考什么、怎么考、多少分；严禁写入平时成绩、考勤、课堂表现、作业、阶段性实验、过程性成绩等整学期成绩构成。"
        "content 要结合课堂内容、绑定文档、使用教材和考试形式，具体、可考核、可评分。"
    )


def _user_prompt(fields: dict[str, Any], classroom_context: dict[str, Any], prompt: str) -> str:
    return "\n\n".join(
        [
            "请根据课堂信息生成《广西外国语学院课程考核计划表》的结构化填表数据。",
            "固定模板要求：基础信息包含课程名称、专业年级班级、考核类型(考查/考试)、命题教师、系主任审核签字、命题日期；"
            "考核信息列为考核形式、考核技能/内容、分值；分值合计必须为 100。",
            "注意：这是期末考试/期末考核的命题计划表，不是课程成绩构成表。考核信息只写考试形式、题目/技能大类和对应分值，通常 3-6 行；不要写平时分、考勤分、课堂表现、课后作业或阶段性过程项目。",
            "请优先沿用下面给出的课程名称、专业年级班级、命题教师、学年学期等字段，不要篡改。",
            f"已知模板字段 JSON：\n{json.dumps(fields, ensure_ascii=False, indent=2)}",
            f"课堂与教务上下文 JSON：\n{json.dumps(classroom_context, ensure_ascii=False, indent=2)}",
            f"教师补充要求：\n{prompt.strip() or '无'}",
        ]
    )


# ---------------------------------------------------------------------------
# JSON extraction (mirrors lesson_plan_generation_service)
# ---------------------------------------------------------------------------
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


async def _chat_json(system_prompt: str, user_message: str) -> dict[str, Any] | None:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "file_texts": [],
        "model_capability": "thinking",
        "task_type": "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "assessment-plan:generate",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
        response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
        retry = {**payload, "model_capability": "standard", "task_type": "fast_text_response",
                 "task_label": "assessment-plan:generate:standard-retry"}
        response = await ai_client.post("/api/ai/chat", json=retry, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    return _json_from_payload(response.json())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_status(plan_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        ap.set_generation_status(conn, plan_id, **kwargs)
        conn.commit()


def find_teacher_own_signature_id(conn: Any, teacher_id: int) -> int | None:
    """The teacher's own latest active signature, used to auto-bind 命题教师."""
    try:
        row = conn.execute(
            """
            SELECT id FROM electronic_signatures
            WHERE owner_role = 'teacher' AND owner_id = ?
              AND subject_role = 'teacher'
              AND status = 'active' AND deleted_at IS NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(teacher_id),),
        ).fetchone()
        return int(dict(row)["id"]) if row else None
    except Exception:
        return None


def _classroom_context(conn: Any, class_offering_id: int) -> dict[str, Any]:
    try:
        from .academic_service import build_classroom_ai_context

        return build_classroom_ai_context(conn, int(class_offering_id)) or {}
    except Exception:
        return {}


def _merge_fields(offering_fields: dict[str, Any], ai_fields: dict[str, Any]) -> dict[str, Any]:
    """Offering-derived identity fields win; AI fills assessment specifics + gaps."""
    merged = dict(ai_fields or {})
    for key, value in (offering_fields or {}).items():
        if str(value or "").strip():
            merged[key] = value
    return merged


def _score_total(items: list[Any]) -> float:
    total = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("score") or "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", raw)
        if match:
            total += float(match.group(0))
    return total


def _looks_like_process_assessment(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    text = " ".join(
        str(item.get(key) or "")
        for key in ("assessment_form", "form", "content", "assessment_content")
    )
    return any(term in text for term in _PROCESS_ASSESSMENT_TERMS)


def _seed_assessment_items(fields: dict[str, Any], classroom_context: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    seed = build_final_material_generation_seed(
        document_type="assessment_plan",
        classroom_context={**(classroom_context or {}), **(fields or {})},
        prompt=prompt,
    )
    export_payload = seed.get("export_payload") if isinstance(seed.get("export_payload"), dict) else {}
    structured = export_payload.get("structured") if isinstance(export_payload.get("structured"), dict) else {}
    items = structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else []
    return [dict(item) for item in items if isinstance(item, dict)]


def _final_exam_items_or_seed(
    items: Any,
    *,
    fields: dict[str, Any],
    classroom_context: dict[str, Any],
    prompt: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    raw_items = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    filtered = [item for item in raw_items if not _looks_like_process_assessment(item)]
    if len(filtered) != len(raw_items):
        warnings.append("已移除 AI 误写入的平时/过程性成绩项，仅保留期末考试计划项。")
    if 3 <= len(filtered) <= 6 and abs(_score_total(filtered) - 100.0) < 1e-6:
        return filtered
    if filtered:
        warnings.append("AI 生成的期末考核项数量或分值不符合要求，已改用本地期末考试项模板。")
    return _seed_assessment_items(fields, classroom_context, prompt)


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------
async def run_generation_job(
    plan_id: str, class_offering_id: int, teacher_id: int, prompt: str = ""
) -> None:
    try:
        _set_status(
            plan_id,
            status="generating",
            ai_gen_status="running",
            ai_gen_error="",
            progress={"done": 0, "total": 1, "current_label": "正在整理课堂信息…"},
        )
        with get_db_connection() as conn:
            teacher_row = conn.execute(
                "SELECT id, name, email AS username FROM teachers WHERE id = ? LIMIT 1",
                (int(teacher_id),),
            ).fetchone()
            teacher = dict(teacher_row) if teacher_row else {"id": teacher_id, "name": "", "username": ""}
            offering_fields = ap.build_fields_from_offering(conn, int(class_offering_id), teacher=teacher)
            classroom_context = _classroom_context(conn, int(class_offering_id))
            own_signature_id = find_teacher_own_signature_id(conn, int(teacher_id))

        warnings: list[str] = []
        try:
            _set_status(plan_id, progress={"done": 0, "total": 1, "current_label": "深度思考生成中…"})
            raw = await _chat_json(_system_prompt(), _user_prompt(offering_fields, classroom_context, prompt))
            if not raw:
                raise ValueError("AI 未返回有效 JSON")
            ai_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else (
                raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            )
            ai_items = raw.get("assessment_items")
            if not isinstance(ai_items, list):
                structured = raw.get("structured") if isinstance(raw.get("structured"), dict) else {}
                ai_items = structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else []
            fields = _merge_fields(offering_fields, ai_fields)
            items = ai_items
        except Exception as exc:  # noqa: BLE001 — fall back to a local complete draft.
            seed = build_final_material_generation_seed(
                document_type="assessment_plan",
                classroom_context={**classroom_context, **offering_fields},
                prompt=prompt,
            )
            export_payload = seed.get("export_payload") or {}
            structured = export_payload.get("structured") if isinstance(export_payload.get("structured"), dict) else {}
            fields = _merge_fields(offering_fields, export_payload.get("fields") or seed.get("metadata") or {})
            items = structured.get("assessment_items") or []
            warnings.append(f"AI 生成不可用，已使用本地草稿模板（{type(exc).__name__}: {str(exc)[:160]}）。请教师复核分值与考核项。")

        items = _final_exam_items_or_seed(
            items,
            fields=fields,
            classroom_context=classroom_context,
            prompt=prompt,
            warnings=warnings,
        )
        normalized = ap.normalize_plan_payload(fields, items)
        if not normalized["score_balanced"]:
            warnings.append(f"考核项分值合计为 {normalized['score_total']}，未达到 100，请在编辑器中调整。")

        with get_db_connection() as conn:
            ap.update_content(
                conn,
                plan_id,
                fields=normalized["fields"],
                items=normalized["items"],
                notes=normalized["notes"],
                status="ready",
            )
            course_name = normalized["fields"].get("course_name") or "课程考核计划表"
            ap.update_attributes(conn, plan_id, title=f"{course_name}（按课堂生成）")
            if own_signature_id:
                ap.set_signature(conn, plan_id, role="examiner", signature_id=own_signature_id)
            ap.set_generation_status(
                conn,
                plan_id,
                ai_gen_status="completed" if not warnings else "completed_with_fallback",
                ai_gen_error="；".join(warnings)[:800],
                progress={"done": 1, "total": 1, "current_label": "完成", "warnings": warnings[-3:]},
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(
            plan_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"生成失败：{type(exc).__name__}: {str(exc)[:400]}",
        )
