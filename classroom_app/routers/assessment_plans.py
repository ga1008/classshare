"""HTTP API for the assessment-plan (考核计划表 / 过程材料) content asset.

Mirrors the lesson-plan API surface: list / create(blank·form) / generate-from-
classroom / import / content get·put / attributes get·patch / tags / signature
bind / delete / retry / inherit / export(docx) / task(poll). Long AI jobs
(generate / import) run as in-process ``asyncio`` background tasks; the list page
shows a placeholder card that polls ``/{id}/task``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services import assessment_plan_service as ap
from ..services import signature_service
from ..services.assessment_plan_generation_service import run_generation_job
from ..services.assessment_plan_import_service import run_import_job
from ..services.resource_access_service import is_super_admin_teacher

router = APIRouter(prefix="/api/assessment-plans")

_MAX_IMPORT_FILES = 8
_MAX_IMPORT_BYTES = 30 * 1024 * 1024
_ALLOWED_IMPORT_EXT = {".doc", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".md", ".txt"}


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


def _load_owned_or_super(conn, plan_id: str, user: dict) -> dict:
    plan = ap.get_assessment_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "考核计划表不存在")
    teacher_id = int(user["id"])
    is_owner = int(plan.get("teacher_id") or 0) == teacher_id
    if not is_owner and not is_super_admin_teacher(conn, teacher_id):
        raise HTTPException(403, "无权操作该考核计划表")
    return plan


def _load_viewable(conn, plan_id: str, user: dict) -> dict:
    plan = ap.get_assessment_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "考核计划表不存在")
    teacher_id = int(user["id"])
    if int(plan.get("teacher_id") or 0) == teacher_id:
        return plan
    is_super = is_super_admin_teacher(conn, teacher_id)
    if is_super:
        return plan
    viewer = ap.teacher_scope(conn, teacher_id)
    if not ap.can_view_plan(plan, viewer, is_super_admin=is_super):
        raise HTTPException(403, "无权查看该考核计划表")
    return plan


def _ensure_offering_access(conn, class_offering_id: int, user: dict) -> None:
    owns = conn.execute(
        "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
        (int(class_offering_id), int(user["id"])),
    ).fetchone()
    if not owns and not is_super_admin_teacher(conn, int(user["id"])):
        raise HTTPException(403, "无权访问该课堂")


# ---------------------------------------------------------------------------
# List / read
# ---------------------------------------------------------------------------
@router.get("", response_class=JSONResponse)
async def list_plans(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plans = ap.list_assessment_plans(conn, teacher=user)
    return {"assessment_plans": plans}


@router.get("/classroom/{class_offering_id}/prefill", response_class=JSONResponse)
async def classroom_prefill(class_offering_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, int(class_offering_id), user)
        fields = ap.build_fields_from_offering(conn, int(class_offering_id), teacher=user)
    return {"fields": fields}


@router.get("/{plan_id}", response_class=JSONResponse)
async def get_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
        is_owned = int(plan.get("teacher_id") or 0) == int(user["id"])
        can_manage = is_owned or is_super_admin_teacher(conn, int(user["id"]))
    return {
        "id": plan["id"],
        "title": plan.get("title"),
        "fields": plan.get("fields"),
        "items": plan.get("items"),
        "notes": plan.get("notes"),
        "tags": plan.get("tags"),
        "scope_level": plan.get("scope_level"),
        "examiner_signature": plan.get("examiner_signature"),
        "reviewer_signature": plan.get("reviewer_signature"),
        "examiner_signature_id": plan.get("examiner_signature_id"),
        "reviewer_signature_id": plan.get("reviewer_signature_id"),
        "score_total": plan.get("score_total"),
        "score_balanced": plan.get("score_balanced"),
        "import_preview": plan.get("import_preview"),
        "class_offering_id": plan.get("class_offering_id"),
        "is_owned": is_owned,
        "can_manage": can_manage,
        "card": ap.serialize_card({**plan, "is_owned": is_owned, "can_manage": can_manage}),
    }


@router.get("/{plan_id}/task", response_class=JSONResponse)
async def get_task_status(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
        is_owned = int(plan.get("teacher_id") or 0) == int(user["id"])
        can_manage = is_owned or is_super_admin_teacher(conn, int(user["id"]))
    return {
        "id": plan["id"],
        "status": plan.get("status"),
        "ai_gen_status": plan.get("ai_gen_status") or "",
        "ai_gen_error": plan.get("ai_gen_error") or "",
        "progress": plan.get("ai_gen_progress_data") or {},
        "card": ap.serialize_card({**plan, "is_owned": is_owned, "can_manage": can_manage}),
    }


# ---------------------------------------------------------------------------
# Create (blank / form) / generate / import
# ---------------------------------------------------------------------------
@router.post("", response_class=JSONResponse)
async def create_plan(request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    items = body.get("items") if isinstance(body.get("items"), list) else None
    class_offering_id = body.get("class_offering_id")
    with get_db_connection() as conn:
        if class_offering_id:
            _ensure_offering_access(conn, int(class_offering_id), user)
            auto = ap.build_fields_from_offering(conn, int(class_offering_id), teacher=user)
            auto.update({k: v for k, v in (fields or {}).items() if str(v).strip()})
            fields = auto
        title = body.get("title") or (fields.get("course_name") or "课程考核计划表")
        plan_id = ap.create_assessment_plan(
            conn,
            teacher=user,
            title=title,
            fields=fields,
            items=items,
            class_offering_id=int(class_offering_id) if class_offering_id else None,
            source_type="blank",
            status="ready",
            scope_level=body.get("scope_level") or ap.SCOPE_PRIVATE,
        )
        conn.commit()
        plan = ap.get_assessment_plan(conn, plan_id)
    return {"id": plan_id, "card": ap.serialize_card(plan)}


@router.post("/generate", response_class=JSONResponse)
async def generate_from_classroom(request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    class_offering_id = body.get("class_offering_id")
    if not class_offering_id:
        raise HTTPException(400, "请选择要生成考核计划表的课堂")
    prompt = str(body.get("prompt") or "").strip()
    with get_db_connection() as conn:
        _ensure_offering_access(conn, int(class_offering_id), user)
        fields = ap.build_fields_from_offering(conn, int(class_offering_id), teacher=user)
        title = (fields.get("course_name") or "课程考核计划表") + "（按课堂生成）"
        plan_id = ap.create_assessment_plan(
            conn,
            teacher=user,
            title=title,
            fields=fields,
            items=[],
            class_offering_id=int(class_offering_id),
            source_type="classroom",
            status="generating",
            ai_gen_status="pending",
            ai_gen_progress={"done": 0, "total": 1, "current_label": "排队中"},
        )
        ap.set_generation_status(conn, plan_id, task_id=plan_id)
        conn.commit()
        plan = ap.get_assessment_plan(conn, plan_id)
    asyncio.create_task(run_generation_job(plan_id, int(class_offering_id), int(user["id"]), prompt))
    return {"id": plan_id, "card": ap.serialize_card(plan)}


@router.post("/import", response_class=JSONResponse)
async def import_plan(
    files: list[UploadFile] = File(...),
    extra_prompt: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    if not files:
        raise HTTPException(400, "请至少选择一个文件")
    if len(files) > _MAX_IMPORT_FILES:
        raise HTTPException(400, f"最多一次导入 {_MAX_IMPORT_FILES} 个文件")
    temp_dir = tempfile.mkdtemp(prefix="lanshare-assessplan-import-")
    saved: list[dict[str, str]] = []
    for index, upload in enumerate(files):
        name = os.path.basename(upload.filename or f"file_{index}")
        ext = os.path.splitext(name)[1].lower()
        if ext and ext not in _ALLOWED_IMPORT_EXT:
            raise HTTPException(400, f"不支持的文件格式：{ext}")
        data = await upload.read()
        if not data:
            continue
        if len(data) > _MAX_IMPORT_BYTES:
            raise HTTPException(400, f"《{name}》超过单文件大小上限")
        dest = os.path.join(temp_dir, f"{index}_{name}")
        with open(dest, "wb") as fh:
            fh.write(data)
        saved.append({"path": dest, "name": name})
    if not saved:
        raise HTTPException(400, "上传的文件均为空")

    with get_db_connection() as conn:
        first_name = os.path.splitext(saved[0]["name"])[0]
        plan_id = ap.create_assessment_plan(
            conn,
            teacher=user,
            title=f"{first_name}（导入解析中）",
            fields={},
            items=[],
            source_type="import",
            status="parsing",
            ai_gen_status="pending",
            ai_gen_progress={"done": 0, "total": 1, "current_label": "排队解析中"},
        )
        ap.set_generation_status(conn, plan_id, task_id=plan_id)
        conn.commit()
        plan = ap.get_assessment_plan(conn, plan_id)
    asyncio.create_task(run_import_job(plan_id, saved, extra_prompt or "", int(user["id"])))
    return {"id": plan_id, "card": ap.serialize_card(plan)}


@router.post("/{plan_id}/retry", response_class=JSONResponse)
async def retry_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_owned_or_super(conn, plan_id, user)
        source_type = plan.get("source_type")
        class_offering_id = plan.get("class_offering_id")
        if source_type == "classroom" and class_offering_id:
            ap.set_generation_status(
                conn,
                plan_id,
                status="generating",
                ai_gen_status="pending",
                ai_gen_error="",
                progress={"done": 0, "total": 1, "current_label": "重新排队中"},
            )
            conn.commit()
        elif source_type == "import":
            raise HTTPException(400, "导入解析失败的考核计划表需重新上传文件再解析；如不再需要可直接删除。")
        else:
            raise HTTPException(400, "该考核计划表不支持重试。")
    if source_type == "classroom" and class_offering_id:
        asyncio.create_task(run_generation_job(plan_id, int(class_offering_id), int(user["id"])))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Content / attributes / tags / signature
# ---------------------------------------------------------------------------
@router.put("/{plan_id}/content", response_class=JSONResponse)
async def put_content(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        normalized = ap.update_content(
            conn,
            plan_id,
            fields=body.get("fields") or {},
            items=body.get("items") or [],
            notes=body.get("notes"),
            status="ready",
        )
        conn.commit()
        plan = ap.get_assessment_plan(conn, plan_id)
    return {
        "ok": True,
        "card": ap.serialize_card(plan),
        "score_total": normalized["score_total"],
        "score_balanced": normalized["score_balanced"],
    }


@router.get("/{plan_id}/attributes", response_class=JSONResponse)
async def get_attributes(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
    return {
        "id": plan["id"],
        "title": plan.get("title"),
        "scope_level": plan.get("scope_level"),
        "scope_options": ap.scope_options(),
    }


@router.patch("/{plan_id}/attributes", response_class=JSONResponse)
async def patch_attributes(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        ap.update_attributes(
            conn,
            plan_id,
            title=body.get("title"),
            scope_level=body.get("scope_level"),
        )
        conn.commit()
        plan = ap.get_assessment_plan(conn, plan_id)
    return {"ok": True, "card": ap.serialize_card(plan)}


@router.put("/{plan_id}/tags", response_class=JSONResponse)
async def put_tags(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        tags = ap.update_tags(conn, plan_id, body.get("tags"))
        conn.commit()
    return {"ok": True, "tags": tags}


@router.put("/{plan_id}/signature", response_class=JSONResponse)
async def put_signature(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    role = str(body.get("role") or "").strip()
    if role not in {"examiner", "reviewer"}:
        raise HTTPException(400, "签名角色必须是 examiner 或 reviewer")
    signature_id = body.get("signature_id")
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        normalized_id: int | None = None
        if signature_id:
            # Enforce usage permission via the signature service before binding.
            try:
                signature_service.get_signature_row_for_actor(
                    conn, user, int(signature_id), require_use=True
                )
            except signature_service.SignatureServiceError as exc:
                raise HTTPException(exc.status_code, exc.message) from exc
            normalized_id = int(signature_id)
        ap.set_signature(conn, plan_id, role=role, signature_id=normalized_id)
        conn.commit()
        plan = ap.get_assessment_plan(conn, plan_id)
    return {
        "ok": True,
        "examiner_signature": plan.get("examiner_signature"),
        "reviewer_signature": plan.get("reviewer_signature"),
        "card": ap.serialize_card(plan),
    }


@router.delete("/{plan_id}", response_class=JSONResponse)
async def delete_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        ap.delete_assessment_plan(conn, plan_id)
        conn.commit()
    return {"ok": True}


@router.post("/{plan_id}/inherit", response_class=JSONResponse)
async def inherit_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
        if int(plan.get("teacher_id") or 0) == int(user["id"]):
            raise HTTPException(400, "这已是你自己的考核计划表，无需继承。")
        if ap.normalize_scope_level(plan.get("scope_level")) == ap.SCOPE_PRIVATE:
            raise HTTPException(403, "该考核计划表未公开，无法继承。")
        new_id = ap.clone_for_inherit(conn, plan_id, teacher=user)
        conn.commit()
        new_plan = ap.get_assessment_plan(conn, new_id)
    return {"id": new_id, "card": ap.serialize_card(new_plan)}


# ---------------------------------------------------------------------------
# Export (docx)
# ---------------------------------------------------------------------------
@router.get("/{plan_id}/export")
async def export_plan(plan_id: str, fmt: str = "docx", user: dict = Depends(get_current_teacher)):
    fmt = (fmt or "docx").lower()
    if fmt != "docx":
        raise HTTPException(400, "考核计划表当前仅支持导出 Word(.docx)")
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
        try:
            content, filename = ap.export_plan_docx(conn, plan)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )
