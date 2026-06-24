"""HTTP API for the lesson-plan (教案) content asset.

Mirrors the exam-paper API surface: list / create(blank) / generate-from-classroom
/ import / content get·put / attributes get·patch / tags / delete / retry /
inherit / export(docx·pdf·png) / task(poll). Long AI jobs (generate / import)
run as in-process ``asyncio`` background tasks; the list page shows a placeholder
card that polls ``/{id}/task``.
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
from ..services import lesson_plan_service as lp
from ..services.lesson_plan_docx_service import (
    build_lesson_plan_docx,
    convert_docx_to_pdf,
    convert_docx_to_png,
)
from ..services.lesson_plan_generation_service import run_generation_job
from ..services.lesson_plan_import_service import run_import_job
from ..services.resource_access_service import is_super_admin_teacher

router = APIRouter(prefix="/api/lesson-plans")

_MAX_IMPORT_FILES = 8
_MAX_IMPORT_BYTES = 30 * 1024 * 1024  # 30MB per file
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
    plan = lp.get_lesson_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "教案不存在")
    teacher_id = int(user["id"])
    is_owner = int(plan.get("teacher_id") or 0) == teacher_id
    if not is_owner and not is_super_admin_teacher(conn, teacher_id):
        raise HTTPException(403, "无权操作该教案")
    return plan


def _load_viewable(conn, plan_id: str, user: dict) -> dict:
    plan = lp.get_lesson_plan(conn, plan_id)
    if not plan:
        raise HTTPException(404, "教案不存在")
    teacher_id = int(user["id"])
    if int(plan.get("teacher_id") or 0) == teacher_id:
        return plan
    is_super = is_super_admin_teacher(conn, teacher_id)
    if is_super:
        return plan
    viewer = lp.teacher_scope(conn, teacher_id)
    if not lp.can_view_plan(plan, viewer, is_super_admin=is_super):
        raise HTTPException(403, "无权查看该教案")
    return plan


# ---------------------------------------------------------------------------
# List / read
# ---------------------------------------------------------------------------
@router.get("", response_class=JSONResponse)
async def list_plans(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plans = lp.list_lesson_plans(conn, teacher=user)
    return {"lesson_plans": plans}


@router.get("/{plan_id}", response_class=JSONResponse)
async def get_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
        card = lp.serialize_card(plan)
    return {
        "id": plan["id"],
        "title": plan.get("title"),
        "cover": plan.get("cover"),
        "sessions": plan.get("sessions"),
        "tags": plan.get("tags"),
        "scope_level": plan.get("scope_level"),
        "card": card,
        "is_owned": int(plan.get("teacher_id") or 0) == int(user["id"]),
    }


@router.get("/{plan_id}/task", response_class=JSONResponse)
async def get_task_status(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
    return {
        "id": plan["id"],
        "status": plan.get("status"),
        "ai_gen_status": plan.get("ai_gen_status") or "",
        "ai_gen_error": plan.get("ai_gen_error") or "",
        "progress": plan.get("ai_gen_progress_data") or {},
        "card": lp.serialize_card(plan),
    }


# ---------------------------------------------------------------------------
# Create (blank) / generate / import
# ---------------------------------------------------------------------------
def _scaffold_sessions(count: int) -> list[dict[str, Any]]:
    count = max(0, min(60, int(count or 0)))
    return [{"index": i} for i in range(1, count + 1)]


@router.post("", response_class=JSONResponse)
async def create_blank_plan(request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    cover = body.get("cover") if isinstance(body.get("cover"), dict) else {}
    sessions = body.get("sessions")
    class_offering_id = body.get("class_offering_id")
    with get_db_connection() as conn:
        if class_offering_id:
            auto = lp.build_cover_from_offering(conn, int(class_offering_id), teacher=user)
            # user-provided values win over auto-filled ones
            auto.update({k: v for k, v in (cover or {}).items() if str(v).strip()})
            cover = auto
        if not isinstance(sessions, list):
            sessions = _scaffold_sessions(body.get("session_count"))
        plan_id = lp.create_lesson_plan(
            conn,
            teacher=user,
            title=body.get("title") or (cover.get("course_name") or "教案"),
            cover=cover,
            sessions=sessions,
            course_id=body.get("course_id"),
            class_offering_id=int(class_offering_id) if class_offering_id else None,
            source_type="blank",
            status="ready",
            scope_level=body.get("scope_level") or lp.SCOPE_PRIVATE,
        )
        conn.commit()
        plan = lp.get_lesson_plan(conn, plan_id)
    return {"id": plan_id, "card": lp.serialize_card(plan)}


@router.post("/generate", response_class=JSONResponse)
async def generate_from_classroom(request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    class_offering_id = body.get("class_offering_id")
    if not class_offering_id:
        raise HTTPException(400, "请选择要生成教案的课堂")
    with get_db_connection() as conn:
        owns = conn.execute(
            "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
            (int(class_offering_id), int(user["id"])),
        ).fetchone()
        if not owns and not is_super_admin_teacher(conn, int(user["id"])):
            raise HTTPException(403, "无权访问该课堂")
        cover = lp.build_cover_from_offering(conn, int(class_offering_id), teacher=user)
        title = body.get("title") or (cover.get("course_name") or "教案") + "（按课堂生成）"
        plan_id = lp.create_lesson_plan(
            conn,
            teacher=user,
            title=title,
            cover=cover,
            sessions=[],
            class_offering_id=int(class_offering_id),
            source_type="classroom",
            status="generating",
            ai_gen_status="pending",
            ai_gen_progress={"done": 0, "total": 0, "current_label": "排队中"},
        )
        lp.set_generation_status(conn, plan_id, task_id=plan_id)
        conn.commit()
        plan = lp.get_lesson_plan(conn, plan_id)
    asyncio.create_task(run_generation_job(plan_id, int(class_offering_id), int(user["id"])))
    return {"id": plan_id, "card": lp.serialize_card(plan)}


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
    temp_dir = tempfile.mkdtemp(prefix="lanshare-lessonplan-import-")
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
        plan_id = lp.create_lesson_plan(
            conn,
            teacher=user,
            title=f"{first_name}（导入解析中）",
            cover={},
            sessions=[],
            source_type="import",
            status="parsing",
            ai_gen_status="pending",
            ai_gen_progress={"done": 0, "total": 0, "current_label": "排队解析中"},
        )
        lp.set_generation_status(conn, plan_id, task_id=plan_id)
        conn.commit()
        plan = lp.get_lesson_plan(conn, plan_id)
    asyncio.create_task(run_import_job(plan_id, saved, extra_prompt or "", int(user["id"])))
    return {"id": plan_id, "card": lp.serialize_card(plan)}


@router.post("/{plan_id}/retry", response_class=JSONResponse)
async def retry_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_owned_or_super(conn, plan_id, user)
        source_type = plan.get("source_type")
        class_offering_id = plan.get("class_offering_id")
        if source_type == "classroom" and class_offering_id:
            lp.set_generation_status(
                conn,
                plan_id,
                status="generating",
                ai_gen_status="pending",
                ai_gen_error="",
                progress={"done": 0, "total": 0, "current_label": "重新排队中"},
            )
            conn.commit()
        elif source_type == "import":
            raise HTTPException(
                400, "导入解析失败的教案需重新上传文件再解析；如不再需要可直接删除。"
            )
        else:
            raise HTTPException(400, "该教案不支持重试。")
    if source_type == "classroom" and class_offering_id:
        asyncio.create_task(run_generation_job(plan_id, int(class_offering_id), int(user["id"])))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Content / attributes / tags
# ---------------------------------------------------------------------------
@router.put("/{plan_id}/content", response_class=JSONResponse)
async def put_content(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        lp.update_content(
            conn,
            plan_id,
            cover=body.get("cover") or {},
            sessions=body.get("sessions") or [],
            status="ready",
        )
        conn.commit()
        plan = lp.get_lesson_plan(conn, plan_id)
    return {"ok": True, "card": lp.serialize_card(plan)}


@router.get("/{plan_id}/attributes", response_class=JSONResponse)
async def get_attributes(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
    return {
        "id": plan["id"],
        "title": plan.get("title"),
        "scope_level": plan.get("scope_level"),
        "course_id": plan.get("course_id"),
        "class_offering_id": plan.get("class_offering_id"),
        "scope_options": lp.scope_options(),
    }


@router.patch("/{plan_id}/attributes", response_class=JSONResponse)
async def patch_attributes(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        lp.update_attributes(
            conn,
            plan_id,
            title=body.get("title"),
            scope_level=body.get("scope_level"),
            course_id=body.get("course_id"),
            class_offering_id=body.get("class_offering_id"),
        )
        conn.commit()
        plan = lp.get_lesson_plan(conn, plan_id)
    return {"ok": True, "card": lp.serialize_card(plan)}


@router.put("/{plan_id}/tags", response_class=JSONResponse)
async def put_tags(plan_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        tags = lp.update_tags(conn, plan_id, body.get("tags"))
        conn.commit()
    return {"ok": True, "tags": tags}


@router.delete("/{plan_id}", response_class=JSONResponse)
async def delete_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _load_owned_or_super(conn, plan_id, user)
        lp.delete_lesson_plan(conn, plan_id)
        conn.commit()
    return {"ok": True}


@router.post("/{plan_id}/inherit", response_class=JSONResponse)
async def inherit_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
        if int(plan.get("teacher_id") or 0) == int(user["id"]):
            raise HTTPException(400, "这已是你自己的教案，无需继承。")
        if lp.normalize_scope_level(plan.get("scope_level")) == lp.SCOPE_PRIVATE:
            raise HTTPException(403, "该教案未公开，无法继承。")
        new_id = lp.clone_for_inherit(conn, plan_id, teacher=user)
        conn.commit()
        new_plan = lp.get_lesson_plan(conn, new_id)
    return {"id": new_id, "card": lp.serialize_card(new_plan)}


# ---------------------------------------------------------------------------
# Export (docx / pdf / png)
# ---------------------------------------------------------------------------
_EXPORT_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "png": "image/png",
}


@router.get("/{plan_id}/export")
async def export_plan(plan_id: str, fmt: str = "docx", user: dict = Depends(get_current_teacher)):
    fmt = (fmt or "docx").lower()
    if fmt not in _EXPORT_MEDIA:
        raise HTTPException(400, "不支持的导出格式")
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
    base_title = (plan.get("title") or "教案").replace("/", "_").replace("\\", "_")
    docx_bytes = build_lesson_plan_docx(plan)
    try:
        if fmt == "docx":
            content = docx_bytes
        elif fmt == "pdf":
            content = convert_docx_to_pdf(docx_bytes, base_name=base_title)
        else:
            content = convert_docx_to_png(docx_bytes, base_name=base_title)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    filename = f"{base_title}.{fmt}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=_EXPORT_MEDIA[fmt],
        headers={"Content-Disposition": disposition},
    )
