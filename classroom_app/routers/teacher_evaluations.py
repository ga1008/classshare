"""HTTP API for the teacher 评学表 (教师评学表 / 过程材料) content asset.

Mirrors the assessment-plan API surface (minus signatures): list / create(blank·
form) / generate-from-classroom / import / content get·put / attributes get·patch /
tags / delete / retry / inherit / export(docx·pdf) / task(poll). Long AI jobs run as
in-process ``asyncio`` background tasks; the list page shows a placeholder card that
polls ``/{id}/task``. A real (attachment) export first checks the sheet is complete
and refuses (409) with the missing fields listed; inline PDF preview always renders.
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
from ..services import teacher_evaluation_service as te
from ..services.resource_access_service import is_super_admin_teacher
from ..services.teacher_evaluation_generation_service import run_generation_job
from ..services.teacher_evaluation_import_service import run_import_job

router = APIRouter(prefix="/api/teacher-evaluations")

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


def _load_owned_or_super(conn, evaluation_id: str, user: dict) -> dict:
    evaluation = te.get_evaluation(conn, evaluation_id)
    if not evaluation:
        raise HTTPException(404, "教师评学表不存在")
    teacher_id = int(user["id"])
    is_owner = int(evaluation.get("teacher_id") or 0) == teacher_id
    if not is_owner and not is_super_admin_teacher(conn, teacher_id):
        raise HTTPException(403, "无权操作该教师评学表")
    return evaluation


def _load_viewable(conn, evaluation_id: str, user: dict) -> dict:
    evaluation = te.get_evaluation(conn, evaluation_id)
    if not evaluation:
        raise HTTPException(404, "教师评学表不存在")
    teacher_id = int(user["id"])
    if int(evaluation.get("teacher_id") or 0) == teacher_id:
        return evaluation
    is_super = is_super_admin_teacher(conn, teacher_id)
    if is_super:
        return evaluation
    viewer = te.teacher_scope(conn, teacher_id)
    if not te.can_view_evaluation(evaluation, viewer, is_super_admin=is_super):
        raise HTTPException(403, "无权查看该教师评学表")
    return evaluation


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
async def list_teacher_evaluations(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        evaluations = te.list_evaluations(conn, teacher=user)
    return {"teacher_evaluations": evaluations}


@router.get("/classroom/{class_offering_id}/prefill", response_class=JSONResponse)
async def classroom_prefill(class_offering_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, int(class_offering_id), user)
        fields = te.build_fields_from_offering(conn, int(class_offering_id), teacher=user)
    return {"fields": fields}


@router.get("/{evaluation_id}", response_class=JSONResponse)
async def get_evaluation_detail(evaluation_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        evaluation = _load_viewable(conn, evaluation_id, user)
        is_owned = int(evaluation.get("teacher_id") or 0) == int(user["id"])
        can_manage = is_owned or is_super_admin_teacher(conn, int(user["id"]))
    return {
        "id": evaluation["id"],
        "title": evaluation.get("title"),
        "fields": evaluation.get("fields"),
        "items": evaluation.get("items"),
        "analysis": evaluation.get("analysis"),
        "notes": evaluation.get("notes"),
        "tags": evaluation.get("tags"),
        "scope_level": evaluation.get("scope_level"),
        "score_total": evaluation.get("score_total"),
        "rating": evaluation.get("rating"),
        "is_complete": evaluation.get("is_complete"),
        "missing_fields": te.missing_fields(evaluation),
        "import_preview": evaluation.get("import_preview"),
        "class_offering_id": evaluation.get("class_offering_id"),
        "is_owned": is_owned,
        "can_manage": can_manage,
        "card": te.serialize_card({**evaluation, "is_owned": is_owned, "can_manage": can_manage}),
    }


@router.get("/{evaluation_id}/task", response_class=JSONResponse)
async def get_task_status(evaluation_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        evaluation = _load_viewable(conn, evaluation_id, user)
        is_owned = int(evaluation.get("teacher_id") or 0) == int(user["id"])
        can_manage = is_owned or is_super_admin_teacher(conn, int(user["id"]))
    return {
        "id": evaluation["id"],
        "status": evaluation.get("status"),
        "ai_gen_status": evaluation.get("ai_gen_status") or "",
        "ai_gen_error": evaluation.get("ai_gen_error") or "",
        "progress": evaluation.get("ai_gen_progress_data") or {},
        "card": te.serialize_card({**evaluation, "is_owned": is_owned, "can_manage": can_manage}),
    }


# ---------------------------------------------------------------------------
# Create (blank / form) / generate / import
# ---------------------------------------------------------------------------
@router.post("", response_class=JSONResponse)
async def create_evaluation(request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    items = body.get("items") if isinstance(body.get("items"), list) else None
    class_offering_id = body.get("class_offering_id")
    with get_db_connection() as conn:
        if class_offering_id:
            _ensure_offering_access(conn, int(class_offering_id), user)
            auto = te.build_fields_from_offering(conn, int(class_offering_id), teacher=user)
            auto.update({k: v for k, v in (fields or {}).items() if str(v).strip()})
            fields = auto
        title = body.get("title") or (fields.get("course_name") or "教师评学表")
        evaluation_id = te.create_evaluation(
            conn,
            teacher=user,
            title=title,
            fields=fields,
            items=items,
            class_offering_id=int(class_offering_id) if class_offering_id else None,
            source_type="blank",
            status="ready",
            scope_level=body.get("scope_level") or te.SCOPE_PRIVATE,
        )
        conn.commit()
        evaluation = te.get_evaluation(conn, evaluation_id)
    return {"id": evaluation_id, "card": te.serialize_card(evaluation)}


@router.post("/generate", response_class=JSONResponse)
async def generate_from_classroom(request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    class_offering_id = body.get("class_offering_id")
    if not class_offering_id:
        raise HTTPException(400, "请选择要生成评学表的教学班级")
    prompt = str(body.get("prompt") or "").strip()
    with get_db_connection() as conn:
        _ensure_offering_access(conn, int(class_offering_id), user)
        fields = te.build_fields_from_offering(conn, int(class_offering_id), teacher=user)
        title = (fields.get("course_name") or "教师评学表") + "（按班级生成）"
        evaluation_id = te.create_evaluation(
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
        te.set_generation_status(conn, evaluation_id, task_id=evaluation_id)
        conn.commit()
        evaluation = te.get_evaluation(conn, evaluation_id)
    asyncio.create_task(run_generation_job(evaluation_id, int(class_offering_id), int(user["id"]), prompt))
    return {"id": evaluation_id, "card": te.serialize_card(evaluation)}


@router.post("/import", response_class=JSONResponse)
async def import_evaluation(
    files: list[UploadFile] = File(...),
    extra_prompt: str = Form(default=""),
    user: dict = Depends(get_current_teacher),
):
    if not files:
        raise HTTPException(400, "请至少选择一个文件")
    if len(files) > _MAX_IMPORT_FILES:
        raise HTTPException(400, f"最多一次导入 {_MAX_IMPORT_FILES} 个文件")
    temp_dir = tempfile.mkdtemp(prefix="lanshare-teacheval-import-")
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
        evaluation_id = te.create_evaluation(
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
        te.set_generation_status(conn, evaluation_id, task_id=evaluation_id)
        conn.commit()
        evaluation = te.get_evaluation(conn, evaluation_id)
    asyncio.create_task(run_import_job(evaluation_id, saved, extra_prompt or "", int(user["id"])))
    return {"id": evaluation_id, "card": te.serialize_card(evaluation)}


@router.post("/{evaluation_id}/retry", response_class=JSONResponse)
async def retry_evaluation(evaluation_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        evaluation = _load_owned_or_super(conn, evaluation_id, user)
        source_type = evaluation.get("source_type")
        class_offering_id = evaluation.get("class_offering_id")
        if source_type == "classroom" and class_offering_id:
            te.set_generation_status(
                conn,
                evaluation_id,
                status="generating",
                ai_gen_status="pending",
                ai_gen_error="",
                progress={"done": 0, "total": 1, "current_label": "重新排队中"},
            )
            conn.commit()
        elif source_type == "import":
            raise HTTPException(400, "导入解析失败的评学表需重新上传文件再解析；如不再需要可直接删除。")
        else:
            raise HTTPException(400, "该评学表不支持重试。")
    if source_type == "classroom" and class_offering_id:
        asyncio.create_task(run_generation_job(evaluation_id, int(class_offering_id), int(user["id"])))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Content / attributes / tags
# ---------------------------------------------------------------------------
@router.put("/{evaluation_id}/content", response_class=JSONResponse)
async def put_content(evaluation_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, evaluation_id, user)
        te.update_content(
            conn,
            evaluation_id,
            fields=body.get("fields") or {},
            items=body.get("items") or [],
            analysis=body.get("analysis") or "",
            status="ready",
        )
        conn.commit()
        evaluation = te.get_evaluation(conn, evaluation_id)
    return {
        "ok": True,
        "card": te.serialize_card(evaluation),
        "score_total": evaluation["score_total"],
        "rating": evaluation["rating"],
        "is_complete": evaluation["is_complete"],
        "missing_fields": te.missing_fields(evaluation),
    }


@router.get("/{evaluation_id}/attributes", response_class=JSONResponse)
async def get_attributes(evaluation_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        evaluation = _load_viewable(conn, evaluation_id, user)
    return {
        "id": evaluation["id"],
        "title": evaluation.get("title"),
        "scope_level": evaluation.get("scope_level"),
        "scope_options": te.scope_options(),
    }


@router.patch("/{evaluation_id}/attributes", response_class=JSONResponse)
async def patch_attributes(evaluation_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, evaluation_id, user)
        te.update_attributes(
            conn,
            evaluation_id,
            title=body.get("title"),
            scope_level=body.get("scope_level"),
        )
        conn.commit()
        evaluation = te.get_evaluation(conn, evaluation_id)
    return {"ok": True, "card": te.serialize_card(evaluation)}


@router.put("/{evaluation_id}/tags", response_class=JSONResponse)
async def put_tags(evaluation_id: str, request: Request, user: dict = Depends(get_current_teacher)):
    body = await _json_body(request)
    with get_db_connection() as conn:
        _load_owned_or_super(conn, evaluation_id, user)
        tags = te.update_tags(conn, evaluation_id, body.get("tags"))
        conn.commit()
    return {"ok": True, "tags": tags}


@router.delete("/{evaluation_id}", response_class=JSONResponse)
async def delete_evaluation(evaluation_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _load_owned_or_super(conn, evaluation_id, user)
        te.delete_evaluation(conn, evaluation_id)
        conn.commit()
    return {"ok": True}


@router.post("/{evaluation_id}/inherit", response_class=JSONResponse)
async def inherit_evaluation(evaluation_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        evaluation = _load_viewable(conn, evaluation_id, user)
        if int(evaluation.get("teacher_id") or 0) == int(user["id"]):
            raise HTTPException(400, "这已是你自己的评学表，无需继承。")
        if te.normalize_scope_level(evaluation.get("scope_level")) == te.SCOPE_PRIVATE:
            raise HTTPException(403, "该评学表未公开，无法继承。")
        new_id = te.clone_for_inherit(conn, evaluation_id, teacher=user)
        conn.commit()
        new_evaluation = te.get_evaluation(conn, new_id)
    return {"id": new_id, "card": te.serialize_card(new_evaluation)}


# ---------------------------------------------------------------------------
# Export (docx / pdf) — refuses to ship an incomplete sheet
# ---------------------------------------------------------------------------
@router.get("/{evaluation_id}/export")
async def export_evaluation(
    evaluation_id: str,
    fmt: str = "docx",
    inline: bool = False,
    force: bool = False,
    user: dict = Depends(get_current_teacher),
):
    fmt = (fmt or "docx").lower()
    if fmt not in {"docx", "pdf"}:
        raise HTTPException(400, "教师评学表当前支持导出 Word(.docx) 和 PDF")
    with get_db_connection() as conn:
        evaluation = _load_viewable(conn, evaluation_id, user)
        missing = te.missing_fields(evaluation)
        # Inline PDF is the live editor/preview surface, so it must always render.
        # A real download (attachment) of an incomplete sheet is refused (409).
        if missing and not force and not inline:
            raise HTTPException(
                409,
                "评学表尚未填写完整，请先补全后再导出：" + "、".join(missing),
            )
        try:
            artifact = te.export_evaluation_artifact(evaluation, requested_format=fmt)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
    disposition_type = "inline" if inline else "attachment"
    disposition = f"{disposition_type}; filename*=UTF-8''{quote(artifact.filename)}"
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": disposition},
    )
