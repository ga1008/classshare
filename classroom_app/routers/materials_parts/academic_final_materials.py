"""Teacher workflow for paired JWXT grade-register and exam-analysis materials."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from ..ui_parts.common import _build_manage_template_context
from ...dependencies import get_client_ip
from ...services import signature_service
from ...services.academic_final_material_service import (
    ACADEMIC_EXAM_ANALYSIS_LABEL,
    ACADEMIC_EXAM_ANALYSIS_TYPE,
    ACADEMIC_FINAL_MATERIAL_TYPES,
    ACADEMIC_GRADE_REGISTER_LABEL,
    ACADEMIC_GRADE_REGISTER_TYPE,
    academic_final_material_record_urls,
    batch_semester_parts,
    build_content_markdown,
    build_exam_analysis_export_payload,
    build_grade_register_export_payload,
    build_parse_result_dict,
    load_fresh_cached_batch,
    list_teacher_final_material_batches,
    list_teacher_final_material_candidates,
    reclaim_stale_academic_final_material_batches,
    resolve_default_semester_selection,
    serialize_batch,
    sync_paired_reports_from_academic_system,
    upsert_batch_state,
    validate_paired_reports,
)
from ...services.material_ai_import_service import MaterialParseResult


router = APIRouter()


class AcademicFinalMaterialSyncRequest(BaseModel):
    class_offering_id: int
    exam_course_key: str = ""
    force: bool = False


class AcademicFinalMaterialUpdateRequest(BaseModel):
    document_type: str
    proposition_form: str | None = None
    exam_form: str | None = None
    separate_teaching_exam: str | None = None
    course_nature: str | None = None
    marking_form: str | None = None
    analysis_text: str | None = None
    teacher_signature_id: int | None = None
    department_signature_id: int | None = None
    dean_signature_id: int | None = None
    teacher_signature_ids: list[int] | None = None
    department_signature_ids: list[int] | None = None
    dean_signature_ids: list[int] | None = None


class AcademicFinalMaterialRegenerateRequest(BaseModel):
    prompt: str = Field(default="", max_length=2000)


ANALYSIS_EDIT_FIELDS = {
    "proposition_form",
    "exam_form",
    "separate_teaching_exam",
    "course_nature",
    "marking_form",
}
ANALYSIS_CHOICE_SETS = {
    "proposition_form": {"", "试题库", "试卷库", "教师组题"},
    "exam_form": {"", "开卷", "闭卷"},
    "separate_teaching_exam": {"", "是", "否"},
    "course_nature": {"", "选修", "必修"},
    "marking_form": {"", "本人阅卷", "同行阅卷", "集体阅卷", "机器阅卷", "其他"},
}
ACADEMIC_FINAL_MATERIAL_JOB_TIMEOUT_SECONDS = 10 * 60
AI_METADATA_VALIDATION_KEYS = {
    "paired_course_name",
    "paired_teacher_name",
    "paired_class_name",
    "context_course_name",
    "context_teacher_name",
}
_academic_final_material_tasks: dict[tuple[int, int], asyncio.Task] = {}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_analysis_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:1200]


def _record_export_payload(record: Any) -> dict[str, Any]:
    return _json_object(record["export_payload_json"]) if record else {}


def _record_for_teacher(conn: Any, record_id: int | None, teacher_id: int):
    if not record_id:
        return None
    return conn.execute(
        """
        SELECT *
        FROM material_ai_import_records
        WHERE id = ? AND teacher_id = ? AND parse_status = 'completed'
        LIMIT 1
        """,
        (int(record_id), int(teacher_id)),
    ).fetchone()


def _batch_for_teacher(conn: Any, batch_id: str, teacher_id: int):
    row = conn.execute(
        """
        SELECT *
        FROM academic_final_material_batches
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (str(batch_id), int(teacher_id)),
    ).fetchone()
    if not row:
        raise HTTPException(404, "未找到该期末材料同步记录。")
    return row


def _serialize_record_payload(record: Any) -> dict[str, Any]:
    if not record:
        return {}
    payload = _record_export_payload(record)
    return {
        "id": int(record["id"]),
        "document_type": record["document_type"] or "",
        "document_type_label": record["document_type_label"] or "",
        "updated_at": record["updated_at"] or "",
        "fields": payload.get("fields") if isinstance(payload.get("fields"), dict) else {},
        "structured": payload.get("structured") if isinstance(payload.get("structured"), dict) else {},
        **academic_final_material_record_urls(int(record["id"]), record["updated_at"]),
    }


def _make_parse_result(
    export_payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    ai_used: bool = False,
) -> MaterialParseResult:
    result = build_parse_result_dict(
        export_payload,
        extraction_method="gxufl_fine_report_rtf_ai_validated",
        warnings=warnings,
        ai_used=ai_used,
    )
    content_quality = {
        "status": "ok",
        "usable": True,
        "validator": "paired_deterministic_score_crosscheck_v1",
    }
    parsed_payload = {
        **result,
        "content_quality": content_quality,
        "extraction": {
            "method": result["extraction_method"],
            "source_kind": "doc",
            "quality": content_quality,
        },
    }
    return MaterialParseResult(
        metadata=dict(result["metadata"]),
        content_markdown=str(result["content_markdown"]),
        tables=[],
        warnings=list(result["warnings"]),
        export_payload=dict(result["export_payload"]),
        raw_ai_result={"reviewed": ai_used},
        parsed_payload=parsed_payload,
        content_quality=content_quality,
        extraction_method=str(result["extraction_method"]),
        document_group="final_material",
        document_type=str(result["document_type"]),
        document_type_label=str(result["document_type_label"]),
        ai_used=ai_used,
    )


def _load_course_analysis_context(conn: Any, class_offering_id: int, teacher_id: int) -> dict[str, Any]:
    classroom = _load_final_material_classroom_context(
        conn,
        int(class_offering_id),
        {"id": int(teacher_id), "role": "teacher"},
    )
    assignments = conn.execute(
        """
        SELECT a.id,
               a.title,
               a.status,
               a.due_at,
               CASE WHEN a.exam_paper_id IS NULL OR a.exam_paper_id = '' THEN 0 ELSE 1 END AS is_exam,
               COUNT(s.id) AS submission_count,
               COUNT(CASE WHEN s.score IS NOT NULL THEN 1 END) AS scored_count,
               ROUND(AVG(CASE WHEN s.score IS NOT NULL THEN s.score END), 2) AS average_score,
               MIN(CASE WHEN s.score IS NOT NULL THEN s.score END) AS minimum_score,
               MAX(CASE WHEN s.score IS NOT NULL THEN s.score END) AS maximum_score
        FROM assignments a
        LEFT JOIN submissions s ON CAST(s.assignment_id AS TEXT) = CAST(a.id AS TEXT)
        WHERE a.class_offering_id = ?
        GROUP BY a.id, a.title, a.status, a.due_at, a.exam_paper_id
        ORDER BY a.created_at, a.id
        """,
        (int(class_offering_id),),
    ).fetchall()
    roster = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM students s
        JOIN class_offerings o ON o.class_id = s.class_id
        WHERE o.id = ? AND COALESCE(s.enrollment_status, 'active') = 'active'
        """,
        (int(class_offering_id),),
    ).fetchone()
    return {
        "classroom": classroom,
        "roster_count": int(roster["total"] or 0) if roster else 0,
        "assignments": [dict(row) for row in assignments],
    }


def _fallback_analysis_text(export_payload: dict[str, Any], course_context: dict[str, Any]) -> str:
    fields = export_payload.get("fields") or {}
    structured = export_payload.get("structured") or {}
    stats = structured.get("statistics") or {}
    distribution = structured.get("score_distribution") or []
    excellent = next((item for item in distribution if item.get("segment") == "90-100"), {})
    low = next((item for item in distribution if item.get("segment") == "<60"), {})
    assignment_count = len(course_context.get("assignments") or [])
    return (
        f"本课程试题围绕《{fields.get('course_name') or '本课程'}》核心知识、综合应用与实践能力组织，"
        f"结构和难度梯度总体合理。期末卷面平均分{float(stats.get('average') or 0):.2f}分，"
        f"标准差{float(stats.get('standard_deviation') or 0):.2f}分，90分以上"
        f"{int(excellent.get('count') or 0)}人，不及格{int(low.get('count') or 0)}人，"
        "说明多数学生已掌握主要知识与操作要点。少数学生在综合分析、规范表达和知识迁移方面仍有提升空间。"
        f"结合本学期{assignment_count}项作业与测评反馈，后续将增加分层案例、限时诊断和课堂复盘，"
        "针对薄弱知识点安排专项训练，并强化过程反馈与实践任务的闭环评价。"
    )


async def _ai_review_and_analysis(
    grade_payload: dict[str, Any],
    analysis_payload: dict[str, Any],
    course_context: dict[str, Any],
    *,
    extra_prompt: str = "",
) -> tuple[str, list[str], bool]:
    source = {
        "grade_register": grade_payload,
        "exam_analysis": analysis_payload,
        "course_context": course_context,
    }
    try:
        response = await _call_ai_chat(
            "你是高校教务材料复核专家和任课教师。只能基于给定数据写作，不得改写学号、姓名、成绩、统计值。",
            "\n\n".join(
                [
                    "请深度复核两份报表字段是否完整，并撰写约200字中文教学分析。",
                    "输出 JSON：analysis_text（150-260个中文字符）、warnings（字符串数组）。",
                    "分析必须覆盖试题结构、成绩分布、掌握情况、原因、改进措施；语气客观专业，避免空话。",
                    f"教师强化要求：{extra_prompt.strip() or '无'}",
                    f"数据 JSON：{json.dumps(jsonable_encoder(source), ensure_ascii=False)}",
                ]
            ),
            capability="thinking",
            response_format="json",
            task_type="academic_final_material_review",
            task_priority="background",
            task_label="materials:academic-final-review",
            timeout=300.0,
        )
        result = response if isinstance(response, dict) else {}
        text = _clean_analysis_text(result.get("analysis_text"))
        if len(text) < 80:
            raise ValueError("AI 返回的教学分析过短。")
        warnings = [str(item).strip()[:300] for item in (result.get("warnings") or []) if str(item).strip()]
        return text, warnings, True
    except Exception as exc:
        warning = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return _fallback_analysis_text(analysis_payload, course_context), [f"AI 深度复核暂不可用，已生成可编辑的本地严谨草稿：{warning}"], False


def _failed_validation_checks(validation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (validation.get("checks") or [])
        if not bool(item.get("ok")) and str(item.get("severity") or "error") == "error"
    ]


async def _ai_assist_metadata_validation(
    result: dict[str, Any],
) -> tuple[dict[str, Any], list[str], bool]:
    """Resolve metadata aliases with AI, then rerun every deterministic check.

    The model receives only report headers and failure descriptions: no student
    names, numbers or scores.  Numeric/statistical failures never enter this
    fallback and AI cannot directly mark a batch as valid.
    """

    validation = result.get("validation") or {}
    failed_checks = _failed_validation_checks(validation)
    requested_keys = {str(item.get("key") or "") for item in failed_checks}
    if not requested_keys or not requested_keys.issubset(AI_METADATA_VALIDATION_KEYS):
        return validation, [], False

    field_keys = ("course_name", "teacher_name", "class_name", "academic_year", "semester")
    recognition_source = {
        "failed_checks": [
            {"key": str(item.get("key") or ""), "message": str(item.get("message") or "")}
            for item in failed_checks
        ],
        "grade_register": {key: (result.get("grade") or {}).get("fields", {}).get(key, "") for key in field_keys},
        "exam_analysis": {key: (result.get("analysis") or {}).get("fields", {}).get(key, "") for key in field_keys},
        "selected_classroom": {key: (result.get("context") or {}).get(key, "") for key in field_keys},
    }
    compared_values = {
        "paired_course_name": (
            recognition_source["grade_register"]["course_name"],
            recognition_source["exam_analysis"]["course_name"],
        ),
        "paired_teacher_name": (
            recognition_source["grade_register"]["teacher_name"],
            recognition_source["exam_analysis"]["teacher_name"],
        ),
        "paired_class_name": (
            recognition_source["grade_register"]["class_name"],
            recognition_source["exam_analysis"]["class_name"],
        ),
        "context_course_name": (
            recognition_source["grade_register"]["course_name"],
            recognition_source["selected_classroom"]["course_name"],
        ),
        "context_teacher_name": (
            recognition_source["grade_register"]["teacher_name"],
            recognition_source["selected_classroom"]["teacher_name"],
        ),
    }
    if any(not all(str(value or "").strip() for value in compared_values[key]) for key in requested_keys):
        return validation, [], False
    try:
        response = await _call_ai_chat(
            "你是高校教务报表字段识别助手。只判断名称是否为同一对象的大小写、简称、别名或格式差异；不得修改学生、成绩、人数或统计数据。无法确定时必须返回不等价。",
            "\n\n".join(
                [
                    "判断失败字段是否语义等价。只输出 JSON：equivalences 数组，每项包含 key、equivalent、confidence（0到1）、reason。",
                    "只有高度确定是同一课程、教师或班级时才可 equivalent=true。",
                    f"待识别数据：{json.dumps(recognition_source, ensure_ascii=False)}",
                ]
            ),
            capability="thinking",
            response_format="json",
            task_type="academic_final_material_recognition",
            task_priority="background",
            task_label="materials:academic-final-recognition",
            timeout=180.0,
        )
    except Exception as exc:
        warning = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return validation, [f"AI 字段识别暂不可用：{warning}"], False

    items = response.get("equivalences") if isinstance(response, dict) else []
    accepted_keys: set[str] = set()
    decisions: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        try:
            confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        equivalent = item.get("equivalent") is True
        if key in requested_keys and equivalent and confidence >= 0.98:
            accepted_keys.add(key)
        if key in requested_keys:
            decisions.append(
                {
                    "key": key,
                    "equivalent": equivalent,
                    "confidence": round(max(0.0, min(1.0, confidence)), 4),
                    "reason": str(item.get("reason") or "")[:200],
                }
            )

    if accepted_keys != requested_keys:
        return validation, ["AI 未能高置信度确认报表名称等价，已继续阻止入库。"], True

    revised = validate_paired_reports(
        result.get("grade") or {},
        result.get("analysis") or {},
        context=result.get("context") or {},
        remote_student_count=len(result.get("remote_students") or []),
        accepted_metadata_keys=accepted_keys,
    )
    revised["ai_assistance"] = {
        "used": True,
        "scope": "metadata_equivalence_only",
        "accepted_keys": sorted(accepted_keys),
        "decisions": decisions,
        "deterministic_recheck_passed": bool(revised.get("passed")),
    }
    return revised, (["AI 已辅助确认名称别名，全部成绩与统计规则已重新校验。"] if revised.get("passed") else []), True


async def _attach_source_document(
    *,
    record_id: int,
    source_bytes: bytes,
    source_name: str,
    user: dict,
) -> dict[str, Any]:
    file_hash = hashlib.sha256(source_bytes).hexdigest()
    await _write_material_file(file_hash, source_bytes)
    profile = infer_material_profile(source_name, "application/rtf")
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        record = _record_for_teacher(conn, record_id, int(user["id"]))
        if not record:
            raise HTTPException(404, "解析记录不存在，无法归档教务原件。")
        package_id = int(record["package_material_id"] or 0)
        if not package_id:
            raise HTTPException(409, "期末材料包不完整，无法归档教务原件。")
        package = ensure_teacher_material_owner(conn, package_id, int(user["id"]))
        source_id = int(record["source_material_id"] or 0)
        if source_id:
            ensure_teacher_material_owner(conn, source_id, int(user["id"]))
            conn.execute(
                """
                UPDATE course_materials
                SET file_hash = ?, file_size = ?, mime_type = ?, preview_type = ?,
                    ai_capability = ?, file_ext = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    file_hash,
                    len(source_bytes),
                    profile["mime_type"],
                    profile["preview_type"],
                    profile["ai_capability"],
                    profile["file_ext"],
                    now,
                    source_id,
                ),
            )
        else:
            owner_scope = load_teacher_org_scope(conn, int(user["id"]))
            source_name = make_unique_material_name(conn, int(user["id"]), package_id, source_name)
            source_path = normalize_material_path(f"{package['material_path']}/{source_name}")
            source_id = _insert_material_file_row(
                conn,
                user=user,
                name=source_name,
                material_path=source_path,
                parent_id=package_id,
                root_id=int(package["root_id"]),
                file_profile=profile,
                file_hash=file_hash,
                file_size=len(source_bytes),
                owner_scope=owner_scope,
                now=now,
            )
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET source_material_id = ?, source_file_name = ?, source_file_hash = ?,
                source_file_size = ?, source_mime_type = ?, signature_revision = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                source_id,
                source_name,
                file_hash,
                len(source_bytes),
                profile["mime_type"],
                uuid.uuid4().hex,
                now,
                int(record_id),
            ),
        )
        refresh_root_git_metadata(conn, int(package["root_id"]))
        conn.commit()
    return {"source_material_id": source_id, "file_hash": file_hash, "file_size": len(source_bytes)}


async def _save_or_update_record(
    *,
    existing_record_id: int | None,
    class_offering_id: int,
    parse_result: MaterialParseResult,
    source_bytes: bytes,
    source_name: str,
    user: dict,
) -> dict[str, Any]:
    if existing_record_id:
        with get_db_connection() as conn:
            existing = _record_for_teacher(conn, existing_record_id, int(user["id"]))
        if existing:
            task = await _persist_final_material_record_update(existing_record_id, existing, parse_result, user)
        else:
            task = await _create_generated_final_material_package(
                class_offering_id=class_offering_id,
                parent_id=None,
                parse_result=parse_result,
                user=user,
            )
    else:
        task = await _create_generated_final_material_package(
            class_offering_id=class_offering_id,
            parent_id=None,
            parse_result=parse_result,
            user=user,
        )
    record_id = int(task.get("id") or 0)
    if not record_id:
        raise HTTPException(500, "期末材料已解析，但未能取得入库记录编号。")
    source = await _attach_source_document(
        record_id=record_id,
        source_bytes=source_bytes,
        source_name=source_name,
        user=user,
    )
    return {**task, "source": source}


def _render_page(
    request: Request,
    user: dict,
    *,
    document_type: str,
):
    is_grade = document_type == ACADEMIC_GRADE_REGISTER_TYPE
    return templates.TemplateResponse(
        request,
        "manage/academic_final_materials.html",
        _build_manage_template_context(
            request,
            user,
            page_title=ACADEMIC_GRADE_REGISTER_LABEL if is_grade else ACADEMIC_EXAM_ANALYSIS_LABEL,
            active_page="academic_grade_registers" if is_grade else "academic_exam_analyses",
            extra={
                "document_type": document_type,
                "document_type_label": ACADEMIC_GRADE_REGISTER_LABEL if is_grade else ACADEMIC_EXAM_ANALYSIS_LABEL,
                "sibling_url": (
                    "/manage/teaching/academic-exam-analyses"
                    if is_grade
                    else "/manage/teaching/academic-grade-registers"
                ),
                "sibling_label": ACADEMIC_EXAM_ANALYSIS_LABEL if is_grade else ACADEMIC_GRADE_REGISTER_LABEL,
            },
        ),
    )


@router.get("/manage/teaching/academic-grade-registers", response_class=HTMLResponse)
async def manage_academic_grade_registers(request: Request, user: dict = Depends(get_current_teacher)):
    return _render_page(request, user, document_type=ACADEMIC_GRADE_REGISTER_TYPE)


@router.get("/manage/teaching/academic-exam-analyses", response_class=HTMLResponse)
async def manage_academic_exam_analyses(request: Request, user: dict = Depends(get_current_teacher)):
    return _render_page(request, user, document_type=ACADEMIC_EXAM_ANALYSIS_TYPE)


@router.get("/api/academic-final-materials/candidates", response_class=JSONResponse)
async def api_academic_final_material_candidates(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        candidates = list_teacher_final_material_candidates(conn, int(user["id"]))
        default_semester = resolve_default_semester_selection(conn, int(user["id"]))
    return {"status": "success", "items": candidates, "default_semester": default_semester}


@router.get("/api/academic-final-materials", response_class=JSONResponse)
async def api_academic_final_material_list(
    document_type: str = Query(default=ACADEMIC_GRADE_REGISTER_TYPE),
    user: dict = Depends(get_current_teacher),
):
    if document_type not in ACADEMIC_FINAL_MATERIAL_TYPES:
        raise HTTPException(400, "期末材料类型不受支持。")
    with get_db_connection() as conn:
        reclaimed = reclaim_stale_academic_final_material_batches(conn, int(user["id"]))
        if reclaimed:
            conn.commit()
        items = list_teacher_final_material_batches(conn, int(user["id"]), document_type=document_type)
        default_semester = resolve_default_semester_selection(conn, int(user["id"]))
    return {"status": "success", "items": items, "default_semester": default_semester}


@router.get("/api/academic-final-materials/{batch_id}", response_class=JSONResponse)
async def api_academic_final_material_detail(batch_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        batch = _batch_for_teacher(conn, batch_id, int(user["id"]))
        grade = _record_for_teacher(conn, batch["grade_record_id"], int(user["id"]))
        analysis = _record_for_teacher(conn, batch["analysis_record_id"], int(user["id"]))
    return {
        "status": "success",
        "batch": serialize_batch(batch),
        "grade": _serialize_record_payload(grade),
        "analysis": _serialize_record_payload(analysis),
    }


async def _run_academic_final_material_sync(
    body: AcademicFinalMaterialSyncRequest,
    user: dict,
) -> dict[str, Any]:
    result = await sync_paired_reports_from_academic_system(
        int(user["id"]),
        int(body.class_offering_id),
        exam_course_key=body.exam_course_key,
        force=bool(body.force),
    )
    if result.get("status") == "cached":
        return result
    if result.get("status") != "downloaded":
        return result

    validation = result["validation"]
    recognition_warnings: list[str] = []
    recognition_ai_used = False
    if not validation.get("passed"):
        validation, recognition_warnings, recognition_ai_used = await _ai_assist_metadata_validation(result)
        result["validation"] = validation
    course_info = result.get("course") or {}
    # 教务返回的是原始 xnm/xqm 码（如 "2025"/"12"）；落库前统一成平台
    # 规范学年学期（"2025-2026"/"第二学期"），避免卡片显示原始代码。
    _semester_code, _semester_label, display_year, display_term = batch_semester_parts(
        course_info.get("academic_year"), course_info.get("academic_term")
    )
    common_batch_values = {
        "academic_year": display_year or str(course_info.get("academic_year") or ""),
        "academic_term": display_term or str(course_info.get("academic_term") or ""),
        "exam_course_key": str((result.get("course") or {}).get("key") or ""),
        "course_code": str((result.get("course") or {}).get("course_code") or ""),
        "course_name": str((result.get("course") or {}).get("course_name") or ""),
        "teaching_class_id": str((result.get("course") or {}).get("teaching_class_id") or ""),
        "teaching_class_name": str((result.get("course") or {}).get("teaching_class_name") or ""),
        "grade_entry_status": str((result.get("course") or {}).get("grade_entry_status") or ""),
        "grade_source_hash": result["grade_hash"],
        "analysis_source_hash": result["analysis_hash"],
        "grade_source_size": len(result["grade_bytes"]),
        "analysis_source_size": len(result["analysis_bytes"]),
        "validation_status": validation.get("status") or "failed",
        "validation_json": json.dumps(validation, ensure_ascii=False),
        "sync_options_json": "{}",
        "source_summary_json": json.dumps(result.get("source_summary") or [], ensure_ascii=False),
    }
    if not validation.get("passed"):
        with get_db_connection() as conn:
            batch = upsert_batch_state(
                conn,
                teacher_id=int(user["id"]),
                class_offering_id=int(body.class_offering_id),
                values={
                    **common_batch_values,
                    "sync_status": "validation_failed",
                    "last_error": str((validation.get("errors") or ["双表校验未通过。"])[0])[:500],
                },
            )
            conn.commit()
        return {
            "status": "validation_failed",
            "message": "两份文档已经下载，但成绩与统计交叉校验未通过，系统已阻止入库。",
            "batch": batch,
            "validation": validation,
        }

    with get_db_connection() as conn:
        existing = upsert_batch_state(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(body.class_offering_id),
            values={**common_batch_values, "sync_status": "processing", "last_error": ""},
        )
        # Signature binding is an explicit, feature-bound action performed in
        # the editor.  Synchronization never inserts or persists a signature.
        teacher_signature_id = None
        teacher_signature_path = ""
        course_context = _load_course_analysis_context(conn, int(body.class_offering_id), int(user["id"]))
        conn.commit()

    grade_payload = build_grade_register_export_payload(
        result["grade"],
        validation,
        teacher_signature_id=teacher_signature_id,
        teacher_signature_path=teacher_signature_path,
    )
    analysis_defaults: dict[str, Any] = {}
    analysis_payload = build_exam_analysis_export_payload(
        result["analysis"],
        validation,
        defaults=analysis_defaults,
    )
    # 报表 RTF 偶发缺失学年学期表头时，用教务课程的规范学期兜底，
    # 保证材料属性始终携带完整学期信息。
    for payload in (grade_payload, analysis_payload):
        payload_fields = payload.get("fields") or {}
        if display_year and not str(payload_fields.get("academic_year") or "").strip():
            payload_fields["academic_year"] = display_year
        if display_term and not str(payload_fields.get("semester") or "").strip():
            payload_fields["semester"] = display_term
        payload["fields"] = payload_fields
    analysis_text, ai_warnings, ai_used = await _ai_review_and_analysis(
        grade_payload,
        analysis_payload,
        course_context,
    )
    analysis_payload["structured"]["analysis_text"] = analysis_text
    analysis_payload["fields"]["analysis_text"] = analysis_text
    all_ai_warnings = [*recognition_warnings, *ai_warnings]
    grade_result = _make_parse_result(grade_payload, warnings=all_ai_warnings, ai_used=ai_used or recognition_ai_used)
    analysis_result = _make_parse_result(analysis_payload, warnings=all_ai_warnings, ai_used=ai_used or recognition_ai_used)

    try:
        grade_task = await _save_or_update_record(
            existing_record_id=existing.get("grade_record_id"),
            class_offering_id=int(body.class_offering_id),
            parse_result=grade_result,
            source_bytes=result["grade_bytes"],
            source_name="教务原件-期末成绩登记表.doc",
            user=user,
        )
        analysis_task = await _save_or_update_record(
            existing_record_id=existing.get("analysis_record_id"),
            class_offering_id=int(body.class_offering_id),
            parse_result=analysis_result,
            source_bytes=result["analysis_bytes"],
            source_name="教务原件-试卷分析表.doc",
            user=user,
        )
    except Exception as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        with get_db_connection() as conn:
            upsert_batch_state(
                conn,
                teacher_id=int(user["id"]),
                class_offering_id=int(body.class_offering_id),
                values={"sync_status": "failed", "last_error": f"报表已下载，但入库失败：{message}"[:500]},
            )
            conn.commit()
        raise

    grade_record_id = int(grade_task["id"])
    analysis_record_id = int(analysis_task["id"])
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        batch = upsert_batch_state(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(body.class_offering_id),
            values={
                **common_batch_values,
                "sync_status": "completed",
                "grade_record_id": grade_record_id,
                "analysis_record_id": analysis_record_id,
                "edit_state_json": json.dumps(
                    {
                        "teacher_signature_ready": bool(teacher_signature_id),
                        "grade_complete": bool(teacher_signature_id),
                        "analysis_required_fields": sorted(ANALYSIS_EDIT_FIELDS),
                        "analysis_ai_generated": ai_used,
                        "validation_ai_assisted": recognition_ai_used,
                        "analysis_complete": False,
                    },
                    ensure_ascii=False,
                ),
                "last_error": "",
                "synced_at": now,
            },
        )
        conn.commit()
    return {
        "status": "success",
        "message": "两份教务报表已一次同步、交叉校验、解析入库，并生成可预览文档。",
        "batch": batch,
        "grade": grade_task,
        "analysis": analysis_task,
        "warnings": all_ai_warnings,
    }


def _mark_academic_final_material_job_failed(
    *,
    teacher_id: int,
    class_offering_id: int,
    message: str,
) -> None:
    with get_db_connection() as conn:
        upsert_batch_state(
            conn,
            teacher_id=int(teacher_id),
            class_offering_id=int(class_offering_id),
            values={
                "sync_status": "failed",
                "last_error": str(message or "后台同步失败。")[:500],
            },
        )
        conn.commit()


async def _run_academic_final_material_sync_job(
    body: AcademicFinalMaterialSyncRequest,
    user: dict,
) -> None:
    teacher_id = int(user["id"])
    class_offering_id = int(body.class_offering_id)
    try:
        await asyncio.wait_for(
            _run_academic_final_material_sync(body, user),
            timeout=ACADEMIC_FINAL_MATERIAL_JOB_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _mark_academic_final_material_job_failed(
            teacher_id=teacher_id,
            class_offering_id=class_offering_id,
            message="教务系统响应超时，后台同步已停止，请稍后重试。",
        )
    except asyncio.CancelledError:
        _mark_academic_final_material_job_failed(
            teacher_id=teacher_id,
            class_offering_id=class_offering_id,
            message="后台同步因服务重启而中断，请重新同步。",
        )
        raise
    except Exception as exc:  # Worker boundary: persist a retryable terminal state.
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _mark_academic_final_material_job_failed(
            teacher_id=teacher_id,
            class_offering_id=class_offering_id,
            message=f"后台同步失败：{message}",
        )


def _academic_final_material_task_key(teacher_id: int, class_offering_id: int) -> tuple[int, int]:
    return int(teacher_id), int(class_offering_id)


def _academic_final_material_task_is_running(teacher_id: int, class_offering_id: int) -> bool:
    task = _academic_final_material_tasks.get(
        _academic_final_material_task_key(teacher_id, class_offering_id)
    )
    return bool(task and not task.done())


def _schedule_academic_final_material_sync(
    body: AcademicFinalMaterialSyncRequest,
    user: dict,
) -> bool:
    key = _academic_final_material_task_key(int(user["id"]), int(body.class_offering_id))
    existing = _academic_final_material_tasks.get(key)
    if existing and not existing.done():
        return False

    task = asyncio.create_task(_run_academic_final_material_sync_job(body, dict(user)))
    _academic_final_material_tasks[key] = task

    def _cleanup(done: asyncio.Task) -> None:
        if _academic_final_material_tasks.get(key) is done:
            _academic_final_material_tasks.pop(key, None)
        try:
            done.exception()
        except (asyncio.CancelledError, Exception):
            pass

    task.add_done_callback(_cleanup)
    return True


@router.post("/api/academic-final-materials/sync", response_class=JSONResponse)
async def api_sync_academic_final_materials(
    body: AcademicFinalMaterialSyncRequest,
    user: dict = Depends(get_current_teacher),
):
    teacher_id = int(user["id"])
    class_offering_id = int(body.class_offering_id)
    if not body.force:
        cached = load_fresh_cached_batch(teacher_id, class_offering_id)
        if cached and cached.get("grade_record_id") and cached.get("analysis_record_id"):
            return {
                "status": "cached",
                "message": "已使用最近同步结果。",
                "batch": cached,
            }

    if _academic_final_material_task_is_running(teacher_id, class_offering_id):
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM academic_final_material_batches
                WHERE teacher_id = ? AND class_offering_id = ?
                LIMIT 1
                """,
                (teacher_id, class_offering_id),
            ).fetchone()
        return JSONResponse(
            jsonable_encoder({
                "status": "already_running",
                "message": "该课堂正在后台同步。",
                "batch": serialize_batch(row) if row else None,
            }),
            status_code=202,
        )

    with get_db_connection() as conn:
        batch = upsert_batch_state(
            conn,
            teacher_id=teacher_id,
            class_offering_id=class_offering_id,
            values={
                "sync_status": "queued",
                "sync_options_json": "{}",
                "last_error": "",
            },
        )
        conn.commit()

    try:
        scheduled = _schedule_academic_final_material_sync(body, dict(user))
    except RuntimeError as exc:
        _mark_academic_final_material_job_failed(
            teacher_id=teacher_id,
            class_offering_id=class_offering_id,
            message="后台同步任务启动失败，请稍后重试。",
        )
        raise HTTPException(503, "后台同步任务启动失败，请稍后重试。") from exc

    return JSONResponse(
        jsonable_encoder({
            "status": "queued" if scheduled else "already_running",
            "message": "已开始后台同步。" if scheduled else "该课堂正在后台同步。",
            "batch": batch,
        }),
        status_code=202,
    )


def _validate_analysis_choices(payload: dict[str, Any]) -> None:
    for key, allowed in ANALYSIS_CHOICE_SETS.items():
        if key in payload and str(payload.get(key) or "").strip() not in allowed:
            raise HTTPException(400, f"{key} 的选项不受支持。")


def _apply_signatures(
    conn: Any,
    user: dict,
    fields: dict[str, Any],
    *,
    id_key: str,
    ids_key: str,
    path_key: str,
    signature_ids: list[int],
    function_point_key: str,
    context_type: str,
    context_id: str,
    context_label: str,
    ip: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    normalized_ids: list[int] = []
    for value in signature_ids:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0 and normalized not in normalized_ids:
            normalized_ids.append(normalized)
    if len(normalized_ids) > 12:
        raise HTTPException(400, "同一签名点最多选择 12 个签名。")
    fields[ids_key] = normalized_ids
    fields[id_key] = normalized_ids[0] if normalized_ids else None
    fields[path_key] = ""
    for signature_id in normalized_ids:
        try:
            row, _actor = signature_service.get_signature_row_for_actor(
                conn,
                user,
                int(signature_id),
                require_use=False,
            )
        except signature_service.SignatureServiceError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        if not signature_service.resolve_signature_file_path(row):
            raise HTTPException(422, "所选签名图片文件不存在。")
    return {
        "signature_ids": normalized_ids,
        "function_point_key": function_point_key,
        "context_type": context_type,
        "context_id": context_id,
        "context_label": context_label,
        "metadata": {"document_type": context_type},
        "ip": ip,
        "user_agent": user_agent,
    }


@router.patch("/api/academic-final-materials/{batch_id}", response_class=JSONResponse)
async def api_update_academic_final_material(
    batch_id: str,
    body: AcademicFinalMaterialUpdateRequest,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    if body.document_type not in ACADEMIC_FINAL_MATERIAL_TYPES:
        raise HTTPException(400, "期末材料类型不受支持。")
    body_payload = body.model_dump(exclude_unset=True)
    _validate_analysis_choices(body_payload)
    with get_db_connection() as conn:
        batch = _batch_for_teacher(conn, batch_id, int(user["id"]))
        record_id = (
            batch["grade_record_id"]
            if body.document_type == ACADEMIC_GRADE_REGISTER_TYPE
            else batch["analysis_record_id"]
        )
        record = _record_for_teacher(conn, record_id, int(user["id"]))
        if not record:
            raise HTTPException(409, "请先同步该课堂的两份期末材料。")
        export_payload = _record_export_payload(record)
        fields = dict(export_payload.get("fields") or {})
        structured = dict(export_payload.get("structured") or {})
        for stale_path_key in (
            "teacher_signature_image_path",
            "department_signature_image_path",
            "dean_signature_image_path",
        ):
            fields.pop(stale_path_key, None)
        signature_use_intents: list[dict[str, Any]] = []
        context_label = f"{fields.get('course_name') or ''} · {fields.get('class_name') or ''}".strip(" ·")
        if body.document_type == ACADEMIC_GRADE_REGISTER_TYPE:
            if "teacher_signature_ids" in body_payload or "teacher_signature_id" in body_payload:
                selected_ids = body.teacher_signature_ids if body.teacher_signature_ids is not None else ([body.teacher_signature_id] if body.teacher_signature_id else [])
                intent = _apply_signatures(
                    conn,
                    user,
                    fields,
                    id_key="teacher_signature_id",
                    ids_key="teacher_signature_ids",
                    path_key="teacher_signature_image_path",
                    signature_ids=selected_ids,
                    function_point_key="academic_final_material.grade_register.teacher_signature",
                    context_type="academic_final_material",
                    context_id=str(record["id"]),
                    context_label=context_label or f"期末成绩登记表 #{record['id']}",
                    ip=get_client_ip(request),
                    user_agent=request.headers.get("user-agent", ""),
                )
                signature_use_intents.append(intent)
        else:
            for key in ANALYSIS_EDIT_FIELDS:
                if key in body_payload:
                    fields[key] = str(body_payload.get(key) or "").strip()
            if "analysis_text" in body_payload:
                text = _clean_analysis_text(body.analysis_text)
                if not text:
                    raise HTTPException(400, "教学分析不能为空。")
                structured["analysis_text"] = text
                fields["analysis_text"] = text
            if "department_signature_ids" in body_payload or "department_signature_id" in body_payload:
                selected_ids = body.department_signature_ids if body.department_signature_ids is not None else ([body.department_signature_id] if body.department_signature_id else [])
                intent = _apply_signatures(
                    conn,
                    user,
                    fields,
                    id_key="department_signature_id",
                    ids_key="department_signature_ids",
                    path_key="department_signature_image_path",
                    signature_ids=selected_ids,
                    function_point_key="academic_final_material.exam_analysis.department_review_signature",
                    context_type="academic_final_material",
                    context_id=str(record["id"]),
                    context_label=context_label or f"试卷分析表 #{record['id']}",
                    ip=get_client_ip(request),
                    user_agent=request.headers.get("user-agent", ""),
                )
                signature_use_intents.append(intent)
            if "dean_signature_ids" in body_payload or "dean_signature_id" in body_payload:
                selected_ids = body.dean_signature_ids if body.dean_signature_ids is not None else ([body.dean_signature_id] if body.dean_signature_id else [])
                intent = _apply_signatures(
                    conn,
                    user,
                    fields,
                    id_key="dean_signature_id",
                    ids_key="dean_signature_ids",
                    path_key="dean_signature_image_path",
                    signature_ids=selected_ids,
                    function_point_key="academic_final_material.exam_analysis.dean_review_signature",
                    context_type="academic_final_material",
                    context_id=str(record["id"]),
                    context_label=context_label or f"试卷分析表 #{record['id']}",
                    ip=get_client_ip(request),
                    user_agent=request.headers.get("user-agent", ""),
                )
                signature_use_intents.append(intent)
        export_payload["fields"] = fields
        export_payload["structured"] = structured
        conn.commit()
    parse_result = _make_parse_result(export_payload, ai_used=bool(record["parse_mode"] in {"ai", "ai_generated"}))
    task = await _persist_final_material_record_update(
        int(record["id"]),
        record,
        parse_result,
        user,
        signature_use_intents=signature_use_intents,
    )
    with get_db_connection() as conn:
        batch_row = _batch_for_teacher(conn, batch_id, int(user["id"]))
        edit_state = _json_object(batch_row["edit_state_json"])
        edit_state.update(
            {
                "last_edited_document_type": body.document_type,
                "last_edited_at": datetime.now().isoformat(),
            }
        )
        if body.document_type == ACADEMIC_EXAM_ANALYSIS_TYPE:
            has_department_signature = bool(
                fields.get("department_signature_ids") or fields.get("department_signature_id")
            )
            has_dean_signature = bool(
                fields.get("dean_signature_ids") or fields.get("dean_signature_id")
            )
            edit_state["analysis_complete"] = bool(
                all(str(fields.get(key) or "").strip() for key in ANALYSIS_EDIT_FIELDS)
                and str(structured.get("analysis_text") or "").strip()
                and has_department_signature
                and has_dean_signature
            )
        else:
            has_teacher_signature = bool(
                fields.get("teacher_signature_ids") or fields.get("teacher_signature_id")
            )
            edit_state["teacher_signature_ready"] = has_teacher_signature
            edit_state["grade_complete"] = has_teacher_signature
        updated = upsert_batch_state(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(batch_row["class_offering_id"]),
            values={"edit_state_json": json.dumps(edit_state, ensure_ascii=False)},
        )
        conn.commit()
    return {"status": "success", "message": "期末材料信息已保存，预览与下载将使用最新内容。", "batch": updated, "task": task}


@router.post("/api/academic-final-materials/{batch_id}/regenerate-analysis", response_class=JSONResponse)
async def api_regenerate_academic_final_analysis(
    batch_id: str,
    body: AcademicFinalMaterialRegenerateRequest,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        batch = _batch_for_teacher(conn, batch_id, int(user["id"]))
        grade_record = _record_for_teacher(conn, batch["grade_record_id"], int(user["id"]))
        analysis_record = _record_for_teacher(conn, batch["analysis_record_id"], int(user["id"]))
        if not grade_record or not analysis_record:
            raise HTTPException(409, "请先同步该课堂的两份期末材料。")
        grade_payload = _record_export_payload(grade_record)
        analysis_payload = _record_export_payload(analysis_record)
        course_context = _load_course_analysis_context(
            conn,
            int(batch["class_offering_id"]),
            int(user["id"]),
        )
    text, warnings, ai_used = await _ai_review_and_analysis(
        grade_payload,
        analysis_payload,
        course_context,
        extra_prompt=body.prompt,
    )
    if not ai_used:
        raise HTTPException(503, warnings[0] if warnings else "思考型 AI 暂不可用，请稍后重试。")
    analysis_payload.setdefault("structured", {})["analysis_text"] = text
    analysis_payload.setdefault("fields", {})["analysis_text"] = text
    parse_result = _make_parse_result(analysis_payload, warnings=warnings, ai_used=True)
    task = await _persist_final_material_record_update(
        int(analysis_record["id"]),
        analysis_record,
        parse_result,
        user,
    )
    return {
        "status": "success",
        "message": "已按强化要求重新生成教学分析，并同步更新预览与下载文档。",
        "analysis_text": text,
        "task": task,
    }
