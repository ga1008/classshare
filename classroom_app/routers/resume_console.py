"""Student resume console (简历管理与优化) — pages + JSON API.

Students only (mirrors ``career_path`` gating). Pages render the console shell
(``templates/resume/*`` extending ``templates/resume/layout.html``); the browser
then drives everything through ``/api/resume/*``. Long AI work (self-intro
generation, résumé render, education seed) runs as ``asyncio`` background tasks
exactly like ``assessment_plans``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.chat_image_derivatives import CHAT_IMAGE_TYPES
from ..services.file_service import resolve_global_file_path, save_file_globally
from ..services.resume import resume_ai_service as ai
from ..services.resume import resume_attachment_service as attach
from ..services.resume import resume_document_service as docs
from ..services.resume import resume_generation_service as gen
from ..services.resume import resume_profile_service as profile
from ..services.resume import resume_render_service as render
from ..services.resume.resume_nav_service import build_resume_nav, get_resume_nav_item

router = APIRouter()

_SECTION_PAGE_KEYS = {
    "education": "education",
    "experience": "experience",
    "skill": "skill",
    "certificate": "certificate",
    "self-intro": "self_intro",
}
_ATTACHMENT_SECTIONS = {"certificate": "certificate", "skill": "skill", "experience": "experience"}


def _require_student(user: dict) -> int:
    if str(user.get("role")) != "student":
        raise HTTPException(403, "简历控制台仅对学生开放")
    return int(user["id"])


def _is_student(user: dict) -> bool:
    return str(user.get("role")) == "student"


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    return payload if isinstance(payload, dict) else {}


def _page_context(request: Request, user: dict, active_key: str, **extra: Any) -> dict[str, Any]:
    item = get_resume_nav_item(active_key)
    ctx = {
        "request": request,
        "user_info": user,
        "resume_nav": build_resume_nav(active_key),
        "active_key": active_key,
        "page_title": item.label if item else "简历控制台",
    }
    ctx.update(extra)
    return ctx


# ===========================================================================
# Pages
# ===========================================================================
@router.get("/resume")
def resume_home(user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/resume/profile/personal", status_code=302)


@router.get("/resume/profile/personal", response_class=HTMLResponse)
def resume_personal_page(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    student_id = int(user["id"])
    with get_db_connection() as conn:
        profile.seed_personal_info_from_platform(conn, student_id, user)
        conn.commit()
    return templates.TemplateResponse(request, "resume/personal.html", _page_context(request, user, "personal"))


@router.get("/resume/profile/{section}", response_class=HTMLResponse)
async def resume_section_page(section: str, request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    key = _SECTION_PAGE_KEYS.get(str(section))
    if not key:
        raise HTTPException(404, "页面不存在")
    student_id = int(user["id"])
    # First visit to 学历 auto-seeds one education entry in the background.
    if key == "education":
        with get_db_connection() as conn:
            empty = not profile.has_any_education(conn, student_id)
            conn.commit()
        if empty:
            asyncio.create_task(gen.run_education_seed_job(student_id))
    return templates.TemplateResponse(
        request, "resume/section.html",
        _page_context(request, user, key, section_key=key),
    )


@router.get("/resume/builder", response_class=HTMLResponse)
def resume_builder_page(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "resume/builder.html", _page_context(request, user, "builder"))


@router.get("/resume/list", response_class=HTMLResponse)
def resume_list_page(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "resume/list.html", _page_context(request, user, "list"))


# ===========================================================================
# API — personal info
# ===========================================================================
@router.get("/api/resume/personal", response_class=JSONResponse)
def api_personal_get(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        info = profile.seed_personal_info_from_platform(conn, student_id, user)
        position_options = profile.build_expected_position_options(conn, student_id)
        conn.commit()
    return {
        "ok": True,
        "info": info,
        "required": list(profile.PERSONAL_REQUIRED),
        "fields": list(profile.PERSONAL_FIELDS),
        "position_options": position_options,
    }


@router.post("/api/resume/personal", response_class=JSONResponse)
async def api_personal_update(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    try:
        with get_db_connection() as conn:
            info = profile.update_personal_info(conn, student_id, payload)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "info": info}


@router.post("/api/resume/personal/suggest", response_class=JSONResponse)
async def api_personal_suggest(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        info = profile.get_personal_info(conn, student_id)
        ctx = gen._student_context(conn, student_id)
        conn.commit()
    return await ai.build_personal_info_suggestions(info, ctx)


@router.post("/api/resume/personal/avatar", response_class=JSONResponse)
async def api_personal_avatar(user: dict = Depends(get_current_user), file: UploadFile = File(...)):
    student_id = _require_student(user)
    content_type = str(file.content_type or "").split(";", 1)[0].lower()
    if content_type not in CHAT_IMAGE_TYPES:
        raise HTTPException(400, "仅支持 PNG / JPG / GIF / WebP 图片")
    result = await save_file_globally(file)
    if not result:
        raise HTTPException(500, "头像保存失败")
    if int(result.get("size") or 0) > attach.RESUME_ATTACHMENT_MAX_BYTES:
        raise HTTPException(413, "头像不能超过 5MB")
    with get_db_connection() as conn:
        profile.set_personal_avatar(conn, student_id, result["hash"], content_type)
        conn.commit()
    return {"ok": True, "avatar_url": f"/api/resume/personal/avatar?v={result['hash'][:12]}"}


@router.get("/api/resume/personal/avatar")
def api_personal_avatar_get(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        info = profile.get_personal_info(conn, student_id)
        conn.commit()
    file_hash = str(info.get("avatar_file_hash") or "")
    if not file_hash:
        return RedirectResponse("/api/profile/avatar", status_code=302)
    path = resolve_global_file_path(file_hash)
    if not path:
        return RedirectResponse("/api/profile/avatar", status_code=302)
    return FileResponse(str(path), media_type=str(info.get("avatar_mime_type") or "image/png"))


# ===========================================================================
# API — list sections (education / experience / skill / certificate / self_intro)
# ===========================================================================
def _attach_section_attachments(conn, student_id: int, section: str, items: list[dict[str, Any]]) -> None:
    owner_kind = _ATTACHMENT_SECTIONS.get(section)
    if not owner_kind or not items:
        return
    grouped = attach.list_attachments_for_owners(conn, student_id, owner_kind, [int(i["id"]) for i in items])
    for item in items:
        item["attachments"] = grouped.get(int(item["id"]), [])


@router.get("/api/resume/sections/{section}", response_class=JSONResponse)
def api_section_list(section: str, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            items = profile.list_section(conn, student_id, section)
            _attach_section_attachments(conn, student_id, section.replace("-", "_"), items)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "items": items}


@router.post("/api/resume/sections/{section}", response_class=JSONResponse)
async def api_section_create(section: str, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    try:
        with get_db_connection() as conn:
            item_id = profile.create_section_item(conn, student_id, section, payload)
            item = profile.get_section_item(conn, student_id, section, item_id)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "id": item_id, "item": item}


@router.put("/api/resume/sections/{section}/{item_id}", response_class=JSONResponse)
async def api_section_update(section: str, item_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    try:
        with get_db_connection() as conn:
            profile.update_section_item(conn, student_id, section, item_id, payload)
            item = profile.get_section_item(conn, student_id, section, item_id)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "item": item}


@router.delete("/api/resume/sections/{section}/{item_id}", response_class=JSONResponse)
def api_section_delete(section: str, item_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    norm = section.replace("-", "_")
    try:
        with get_db_connection() as conn:
            profile.delete_section_item(conn, student_id, section, item_id)
            if norm in _ATTACHMENT_SECTIONS:
                attach.delete_owner_attachments(conn, student_id, norm, item_id)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# ===========================================================================
# API — self-intro AI
# ===========================================================================
@router.post("/api/resume/self-intro/optimize", response_class=JSONResponse)
async def api_self_intro_optimize(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    with get_db_connection() as conn:
        info = profile.get_personal_info(conn, student_id)
        conn.commit()
    return await ai.optimize_self_intro(str(payload.get("text") or ""), info)


@router.post("/api/resume/self-intro/generate", response_class=JSONResponse)
async def api_self_intro_generate(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        intro_id = profile.create_self_intro_placeholder(conn, student_id)
        conn.commit()
    asyncio.create_task(gen.run_self_intro_generation_job(intro_id, student_id))
    return {"ok": True, "id": intro_id}


@router.post("/api/resume/education/seed", response_class=JSONResponse)
async def api_education_seed(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        has = profile.has_any_education(conn, student_id)
        conn.commit()
    if has:
        return {"ok": True, "started": False}
    asyncio.create_task(gen.run_education_seed_job(student_id))
    return {"ok": True, "started": True}


# ===========================================================================
# API — attachments
# ===========================================================================
@router.post("/api/resume/attachments", response_class=JSONResponse)
async def api_attachment_upload(
    user: dict = Depends(get_current_user),
    owner_kind: str = "",
    owner_id: int = 0,
    file: UploadFile = File(...),
):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        try:
            profile.get_section_item(conn, student_id, owner_kind, owner_id)
        except ValueError as exc:
            raise HTTPException(404, "请先保存该记录再上传附件") from exc
        item = await attach.create_attachment(conn, student_id, owner_kind, owner_id, file)
        conn.commit()
    return {"ok": True, "attachment": item}


@router.get("/api/resume/attachments/{attachment_id}")
def api_attachment_get(attachment_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        path, mime, filename = attach.resolve_attachment_file(conn, student_id, attachment_id)
        conn.commit()
    return FileResponse(str(path), media_type=mime, filename=filename)


@router.delete("/api/resume/attachments/{attachment_id}", response_class=JSONResponse)
def api_attachment_delete(attachment_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        attach.delete_attachment(conn, student_id, attachment_id)
        conn.commit()
    return {"ok": True}


# ===========================================================================
# API — résumé builder + documents
# ===========================================================================
@router.get("/api/resume/templates", response_class=JSONResponse)
def api_templates(user: dict = Depends(get_current_user)):
    _require_student(user)
    return {"ok": True, "templates": render.list_templates()}


@router.get("/api/resume/builder/palette", response_class=JSONResponse)
def api_builder_palette(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        bundle = profile.collect_profile_bundle(conn, student_id)
        conn.commit()
    return {
        "ok": True,
        "personal": bundle.get("personal") or {},
        "personal_labels": render._PERSONAL_LABELS,
        "education": bundle.get("education", []),
        "experience": bundle.get("experience", []),
        "skill": bundle.get("skill", []),
        "certificate": bundle.get("certificate", []),
        "self_intro": bundle.get("self_intro", []),
        "templates": render.list_templates(),
    }


@router.get("/api/resume/resumes", response_class=JSONResponse)
def api_resumes_list(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        items = docs.list_resumes(conn, student_id)
        conn.commit()
    return {"ok": True, "items": items}


@router.get("/api/resume/resumes/{resume_id}", response_class=JSONResponse)
def api_resume_get(resume_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            item = docs.get_resume(conn, student_id, resume_id)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    item.pop("render_html", None)  # large; fetched via /preview
    return {"ok": True, "resume": item}


@router.post("/api/resume/resumes", response_class=JSONResponse)
async def api_resume_create(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    with get_db_connection() as conn:
        resume_id = docs.create_resume(
            conn, student_id,
            title=str(payload.get("title") or "我的简历"),
            template_key=str(payload.get("template_key") or "classic"),
            layout=payload.get("layout"),
        )
        conn.commit()
    asyncio.create_task(gen.run_resume_render_job(resume_id, student_id))
    return {"ok": True, "id": resume_id}


@router.put("/api/resume/resumes/{resume_id}", response_class=JSONResponse)
async def api_resume_update(resume_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    try:
        with get_db_connection() as conn:
            docs.update_resume(
                conn, student_id, resume_id,
                title=str(payload.get("title") or "我的简历"),
                template_key=str(payload.get("template_key") or "classic"),
                layout=payload.get("layout"),
            )
            conn.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    asyncio.create_task(gen.run_resume_render_job(resume_id, student_id))
    return {"ok": True, "id": resume_id}


@router.delete("/api/resume/resumes/{resume_id}", response_class=JSONResponse)
def api_resume_delete(resume_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        docs.delete_resume(conn, student_id, resume_id)
        conn.commit()
    return {"ok": True}


@router.get("/api/resume/resumes/{resume_id}/preview", response_class=HTMLResponse)
def api_resume_preview(resume_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            resume = docs.get_resume(conn, student_id, resume_id)
            html = resume.get("render_html") or render.assemble_resume_html(conn, student_id, resume)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return HTMLResponse(html)


@router.get("/api/resume/resumes/{resume_id}/export")
def api_resume_export(resume_id: int, fmt: str = "pdf", user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    fmt = "docx" if str(fmt).lower() == "docx" else "pdf"
    try:
        with get_db_connection() as conn:
            resume = docs.get_resume(conn, student_id, resume_id)
            html = resume.get("render_html") or render.assemble_resume_html(conn, student_id, resume)
            conn.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not html:
        raise HTTPException(409, "简历尚未渲染完成，请稍后再试")
    try:
        data = render.export_resume_bytes(html, fmt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"导出失败：{type(exc).__name__}") from exc
    title = str(resume.get("title") or "简历")
    if fmt == "pdf":
        media = "application/pdf"
        filename = f"{title}.pdf"
    else:
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"{title}.docx"
    from urllib.parse import quote
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(content=data, media_type=media, headers={"Content-Disposition": disposition})
