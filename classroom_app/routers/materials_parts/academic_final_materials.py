"""Teacher workflow for paired JWXT grade-register and exam-analysis materials."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .common import *
from .generation_helpers import *
from .ai_import_helpers import *
from .final_material_helpers import *
from ..ui_parts.common import _build_manage_template_context
from ...services import signature_service
from ...services.academic_final_material_service import (
    ACADEMIC_EXAM_ANALYSIS_LABEL,
    ACADEMIC_EXAM_ANALYSIS_TYPE,
    ACADEMIC_FINAL_MATERIAL_TYPES,
    ACADEMIC_GRADE_REGISTER_LABEL,
    ACADEMIC_GRADE_REGISTER_TYPE,
    build_content_markdown,
    build_exam_analysis_export_payload,
    build_grade_register_export_payload,
    build_parse_result_dict,
    ensure_system_consent_signatures,
    list_teacher_final_material_batches,
    list_teacher_final_material_candidates,
    resolve_signature_path,
    serialize_batch,
    sync_paired_reports_from_academic_system,
    upsert_batch_state,
)
from ...services.assessment_plan_generation_service import find_teacher_own_signature_id
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
        "export_url": f"/api/materials/ai-import-records/{int(record['id'])}/export?format=docx",
        "preview_url": f"/api/materials/ai-import-records/{int(record['id'])}/render-preview?format=docx",
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
                    f"数据 JSON：{json.dumps(source, ensure_ascii=False)}",
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
                source_file_size = ?, source_mime_type = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                source_id,
                source_name,
                file_hash,
                len(source_bytes),
                profile["mime_type"],
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
    return {"status": "success", "items": candidates}


@router.get("/api/academic-final-materials", response_class=JSONResponse)
async def api_academic_final_material_list(
    document_type: str = Query(default=ACADEMIC_GRADE_REGISTER_TYPE),
    user: dict = Depends(get_current_teacher),
):
    if document_type not in ACADEMIC_FINAL_MATERIAL_TYPES:
        raise HTTPException(400, "期末材料类型不受支持。")
    with get_db_connection() as conn:
        items = list_teacher_final_material_batches(conn, int(user["id"]), document_type=document_type)
    return {"status": "success", "items": items}


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


@router.post("/api/academic-final-materials/sync", response_class=JSONResponse)
async def api_sync_academic_final_materials(
    body: AcademicFinalMaterialSyncRequest,
    user: dict = Depends(get_current_teacher),
):
    result = await sync_paired_reports_from_academic_system(
        int(user["id"]),
        int(body.class_offering_id),
        exam_course_key=body.exam_course_key,
        force=bool(body.force),
    )
    if result.get("status") == "cached":
        return result
    if result.get("status") != "downloaded":
        return JSONResponse(result, status_code=409 if result.get("status") != "failed" else 502)

    validation = result["validation"]
    common_batch_values = {
        "academic_year": str((result.get("course") or {}).get("academic_year") or ""),
        "academic_term": str((result.get("course") or {}).get("academic_term") or ""),
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
                    "last_error": "两份教务报表交叉校验未通过，已阻止错误数据入库。",
                },
            )
            conn.commit()
        return JSONResponse(
            {
                "status": "validation_failed",
                "message": "两份文档已经下载，但成绩与统计交叉校验未通过，系统已阻止入库。",
                "batch": batch,
                "validation": validation,
            },
            status_code=422,
        )

    with get_db_connection() as conn:
        existing = upsert_batch_state(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(body.class_offering_id),
            values={**common_batch_values, "sync_status": "processing", "last_error": ""},
        )
        teacher_signature_id = find_teacher_own_signature_id(conn, int(user["id"]))
        teacher_signature_path = resolve_signature_path(conn, teacher_signature_id)
        consent = ensure_system_consent_signatures(conn)
        course_context = _load_course_analysis_context(conn, int(body.class_offering_id), int(user["id"]))
        conn.commit()

    grade_payload = build_grade_register_export_payload(
        result["grade"],
        validation,
        teacher_signature_id=teacher_signature_id,
        teacher_signature_path=teacher_signature_path,
    )
    analysis_defaults = {
        "proposition_form": "",
        "exam_form": "",
        "separate_teaching_exam": "",
        "course_nature": "",
        "marking_form": "",
        "department_consent_signature_id": (consent.get("department") or {}).get("id"),
        "department_consent_image_path": (consent.get("department") or {}).get("path", ""),
        "dean_consent_signature_id": (consent.get("dean") or {}).get("id"),
        "dean_consent_image_path": (consent.get("dean") or {}).get("path", ""),
    }
    analysis_payload = build_exam_analysis_export_payload(
        result["analysis"],
        validation,
        defaults=analysis_defaults,
    )
    analysis_text, ai_warnings, ai_used = await _ai_review_and_analysis(
        grade_payload,
        analysis_payload,
        course_context,
    )
    analysis_payload["structured"]["analysis_text"] = analysis_text
    analysis_payload["fields"]["analysis_text"] = analysis_text
    grade_result = _make_parse_result(grade_payload, warnings=ai_warnings, ai_used=ai_used)
    analysis_result = _make_parse_result(analysis_payload, warnings=ai_warnings, ai_used=ai_used)

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
        "warnings": ai_warnings,
    }


def _validate_analysis_choices(payload: dict[str, Any]) -> None:
    for key, allowed in ANALYSIS_CHOICE_SETS.items():
        if key in payload and str(payload.get(key) or "").strip() not in allowed:
            raise HTTPException(400, f"{key} 的选项不受支持。")


def _apply_signature(
    conn: Any,
    user: dict,
    fields: dict[str, Any],
    *,
    id_key: str,
    path_key: str,
    signature_id: int | None,
) -> None:
    fields[id_key] = int(signature_id) if signature_id else None
    fields[path_key] = ""
    if not signature_id:
        return
    try:
        row, _actor = signature_service.get_signature_row_for_actor(
            conn,
            user,
            int(signature_id),
            require_use=True,
        )
    except signature_service.SignatureServiceError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    path = signature_service.resolve_signature_file_path(row)
    if not path:
        raise HTTPException(422, "所选签名图片文件不存在。")
    fields[path_key] = str(path)


@router.patch("/api/academic-final-materials/{batch_id}", response_class=JSONResponse)
async def api_update_academic_final_material(
    batch_id: str,
    body: AcademicFinalMaterialUpdateRequest,
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
        if body.document_type == ACADEMIC_GRADE_REGISTER_TYPE:
            if "teacher_signature_id" in body_payload:
                _apply_signature(
                    conn,
                    user,
                    fields,
                    id_key="teacher_signature_id",
                    path_key="teacher_signature_image_path",
                    signature_id=body.teacher_signature_id,
                )
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
            if "department_signature_id" in body_payload:
                _apply_signature(
                    conn,
                    user,
                    fields,
                    id_key="department_signature_id",
                    path_key="department_signature_image_path",
                    signature_id=body.department_signature_id,
                )
            if "dean_signature_id" in body_payload:
                _apply_signature(
                    conn,
                    user,
                    fields,
                    id_key="dean_signature_id",
                    path_key="dean_signature_image_path",
                    signature_id=body.dean_signature_id,
                )
        export_payload["fields"] = fields
        export_payload["structured"] = structured
        conn.commit()
    parse_result = _make_parse_result(export_payload, ai_used=bool(record["parse_mode"] in {"ai", "ai_generated"}))
    task = await _persist_final_material_record_update(int(record["id"]), record, parse_result, user)
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
            edit_state["analysis_complete"] = bool(
                all(str(fields.get(key) or "").strip() for key in ANALYSIS_EDIT_FIELDS)
                and str(structured.get("analysis_text") or "").strip()
                and fields.get("department_signature_id")
                and fields.get("dean_signature_id")
            )
        else:
            edit_state["teacher_signature_ready"] = bool(fields.get("teacher_signature_id"))
            edit_state["grade_complete"] = bool(fields.get("teacher_signature_id"))
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
