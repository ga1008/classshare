"""HTTP API for the lesson-plan (教案) content asset.

Mirrors the exam-paper API surface: list / create(blank) / generate-from-classroom
/ import / content get·put / attributes get·patch / tags / delete / retry /
inherit / export(docx·pdf·png) / task(poll). Long AI jobs use the durable job
ledger when enabled; the list page shows a placeholder card that polls ``/{id}/task``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..config import AI_DURABLE_JOBS_ENABLED
from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services import lesson_plan_service as lp
from ..services.lesson_plan_generation_service import (
    build_generation_plan_preview,
    draft_manual_session,
    normalize_generation_session_plan,
    run_generation_job,
)
from ..services.lesson_plan_import_service import run_import_job
from ..services.lesson_plan_recovery_service import expire_stale_lesson_plan_tasks
from ..services.lesson_plan_render_service import SUPPORTED_EXPORT_FORMATS, export_plan_artifact
from ..services.ai_durable_job_service import cleanup_ai_job_input_files
from ..services.durable_process_job_service import (
    enqueue_process_generation,
    enqueue_process_import,
    stage_process_import_inputs,
)
from ..services.process_material_import_policy import (
    normalize_process_import_filename,
    validate_process_document_import_file_bytes,
    validate_process_document_import_file_count,
    validate_process_document_import_filename,
)
from ..services.resource_access_service import is_super_admin_teacher

router = APIRouter(prefix="/api/lesson-plans")


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
        expire_stale_lesson_plan_tasks(conn, teacher_id=int(user["id"]))
        conn.commit()
        plans = lp.list_lesson_plans(conn, teacher=user)
    return {"lesson_plans": plans}


def _ensure_offering_access(conn, class_offering_id: int, user: dict) -> None:
    owns = conn.execute(
        "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
        (int(class_offering_id), int(user["id"])),
    ).fetchone()
    if not owns and not is_super_admin_teacher(conn, int(user["id"])):
        raise HTTPException(403, "No permission to access this classroom.")


@router.get("/classroom/{class_offering_id}/generation-plan", response_class=JSONResponse)
async def get_classroom_generation_plan(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, int(class_offering_id), user)
    return await build_generation_plan_preview(int(class_offering_id), int(user["id"]))


@router.post("/classroom/{class_offering_id}/session-draft", response_class=JSONResponse)
async def create_session_draft(
    class_offering_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    body = await _json_body(request)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Please enter the topic or prompt for this session.")
    with get_db_connection() as conn:
        _ensure_offering_access(conn, int(class_offering_id), user)
    session = await draft_manual_session(
        class_offering_id=int(class_offering_id),
        teacher_id=int(user["id"]),
        prompt=prompt,
        previous_context=str(body.get("previous_context") or ""),
        next_context=str(body.get("next_context") or ""),
    )
    return {"session": session}


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
        expire_stale_lesson_plan_tasks(conn, teacher_id=int(user["id"]))
        conn.commit()
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
        requested_sessions = normalize_generation_session_plan(body.get("sessions"))
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
        lp.set_generation_status(
            conn,
            plan_id,
            task_id=plan_id,
            import_preview={"source_files": [], "warnings": []},
        )
        if AI_DURABLE_JOBS_ENABLED:
            enqueue_process_generation(
                conn,
                target_type="lesson_plan",
                target_id=plan_id,
                task_token=plan_id,
                class_offering_id=int(class_offering_id),
                teacher_id=int(user["id"]),
                session_plan=requested_sessions,
            )
        conn.commit()
        plan = lp.get_lesson_plan(conn, plan_id)
    if not AI_DURABLE_JOBS_ENABLED:
        asyncio.create_task(
            run_generation_job(
                plan_id,
                int(class_offering_id),
                int(user["id"]),
                session_plan=requested_sessions,
            )
        )
    return {"id": plan_id, "card": lp.serialize_card(plan)}


@router.post("/import", response_class=JSONResponse)
async def import_plan(
    files: list[UploadFile] = File(...),
    extra_prompt: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    validate_process_document_import_file_count(files)
    staged: list[dict[str, Any]] = []
    for index, upload in enumerate(files):
        name = normalize_process_import_filename(upload.filename, fallback=f"file_{index}")
        validate_process_document_import_filename(name, document_label="教案")
        data = await upload.read()
        validate_process_document_import_file_bytes(data, filename=name)
        staged.append({"name": name, "data": data})
    if not staged:
        raise HTTPException(400, "上传的文件均为空")

    input_refs: list[dict[str, Any]] = []
    saved: list[dict[str, str]] = []
    if AI_DURABLE_JOBS_ENABLED:
        input_refs = stage_process_import_inputs(staged)
        saved = [
            {"path": str(item.get("relative_path") or ""), "name": str(item.get("name") or "")}
            for item in input_refs
        ]
    else:
        temp_dir = tempfile.mkdtemp(prefix="lanshare-lessonplan-import-")
        for index, item in enumerate(staged):
            name = str(item["name"])
            dest = os.path.join(temp_dir, f"{index}_{name}")
            with open(dest, "wb") as fh:
                fh.write(item["data"])
            saved.append({"path": dest, "name": name})

    try:
        with get_db_connection() as conn:
            first_name = os.path.splitext(str(staged[0]["name"]))[0]
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
            lp.set_generation_status(
                conn,
                plan_id,
                task_id=plan_id,
                import_preview={
                    "source_files": [item.get("name") for item in saved],
                    "warnings": [],
                },
            )
            if AI_DURABLE_JOBS_ENABLED:
                enqueue_process_import(
                    conn,
                    target_type="lesson_plan",
                    target_id=plan_id,
                    teacher_id=int(user["id"]),
                    input_files=input_refs,
                    extra_prompt=extra_prompt or "",
                )
            conn.commit()
            plan = lp.get_lesson_plan(conn, plan_id)
    except Exception:
        if input_refs:
            cleanup_ai_job_input_files(input_refs)
        raise
    if not AI_DURABLE_JOBS_ENABLED:
        asyncio.create_task(run_import_job(plan_id, saved, extra_prompt or "", int(user["id"])))
    return {"id": plan_id, "card": lp.serialize_card(plan)}


@router.post("/{plan_id}/retry", response_class=JSONResponse)
async def retry_plan(plan_id: str, user: dict = Depends(get_current_teacher)):
    retry_token = uuid.uuid4().hex if AI_DURABLE_JOBS_ENABLED else plan_id
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
                task_id=retry_token,
            )
            if AI_DURABLE_JOBS_ENABLED:
                enqueue_process_generation(
                    conn,
                    target_type="lesson_plan",
                    target_id=plan_id,
                    task_token=retry_token,
                    class_offering_id=int(class_offering_id),
                    teacher_id=int(user["id"]),
                )
            conn.commit()
        elif source_type == "import":
            raise HTTPException(
                400, "导入解析失败的教案需重新上传文件再解析；如不再需要可直接删除。"
            )
        else:
            raise HTTPException(400, "该教案不支持重试。")
    if source_type == "classroom" and class_offering_id and not AI_DURABLE_JOBS_ENABLED:
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
@router.get("/{plan_id}/export")
async def export_plan(
    plan_id: str,
    fmt: str = "docx",
    inline: bool = False,
    user: dict = Depends(get_current_teacher),
):
    fmt = (fmt or "docx").lower()
    if fmt not in SUPPORTED_EXPORT_FORMATS:
        raise HTTPException(400, "教案当前支持导出 Word(.docx)、PDF 和 PNG")
    with get_db_connection() as conn:
        plan = _load_viewable(conn, plan_id, user)
    try:
        artifact = export_plan_artifact(plan, requested_format=fmt)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    disposition_type = "inline" if inline else "attachment"
    disposition = f"{disposition_type}; filename*=UTF-8''{quote(artifact.filename)}"
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": disposition},
    )
