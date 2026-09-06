"""Student resume pages and commands, using versioned documents and durable jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.career_engagement_service import record_student_career_event_safely
from ..services.chat_image_derivatives import CHAT_IMAGE_TYPES
from ..services.file_service import resolve_global_file_path, save_file_globally
from ..services.resume import resume_ai_service as ai
from ..services.resume import resume_application_service as applications
from ..services.resume import resume_attachment_service as attach
from ..services.resume import resume_document_service as docs
from ..services.resume import resume_generation_service as gen
from ..services.resume import resume_import_service as resume_import
from ..services.resume import resume_job_target_service as job_targets
from ..services.resume import resume_profile_service as profile
from ..services.resume import resume_readiness_service as readiness
from ..services.resume import resume_suggestion_service as suggestions
from ..services.resume import resume_render_service as render
from ..services.resume.resume_nav_service import build_resume_nav, get_resume_nav_item
from ..services.student_career_job_service import CareerJobCapacityError, public_job_state, supersede_student_career_jobs, cancel_student_career_job

from ..services.career_rollout_service import CareerRolloutLimited, ai_availability, current_policy, require_student_ai

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


def _expected_revision(payload: dict[str, Any]) -> int:
    if payload.get("revision") is None:
        raise HTTPException(428, "请携带当前版本后保存，输入内容无需丢弃。")
    try:
        return int(payload["revision"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "版本格式不正确") from exc


def _resume_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CareerRolloutLimited):
        return HTTPException(403, exc.detail)
    if isinstance(exc, CareerJobCapacityError):
        return HTTPException(429, str(exc), headers={"Retry-After": "30"})
    if isinstance(exc, docs.ResumeConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    return HTTPException(400, str(exc))


def _validate_source_context(conn, student_id: int, source: Any, *, previous: Any = None) -> None:
    if not isinstance(source, dict):
        return
    target = source.get("job_target_id") or source.get("job_id")
    if target:
        try:
            job_targets.get_job_target(conn, student_id, int(target))
        except (TypeError, ValueError, LookupError) as exc:
            raise ValueError("关联岗位不存在或无权访问") from exc
    direction = str(source.get("direction_id") or "").strip()
    if direction:
        prior = previous if isinstance(previous, dict) else {}
        if direction == str(prior.get("direction_id") or "") and source.get("recommendation_revision") == prior.get("recommendation_revision"):
            return  # Existing owned documents retain their historical source.
        from ..services.career_lifecycle_service import build_state
        state = build_state(conn, student_id)
        if direction not in {str(node.get("direction_id") or node.get("tag")) for node in (state.get("network") or {}).get("nodes") or []}:
            raise ValueError("职业方向已变化，请从当前职业网络重新选择来源。")
        if source.get("recommendation_revision") and str(source["recommendation_revision"]) != str(state.get("result_version") or ""):
            raise docs.ResumeConflict("职业推荐已更新，请保留草稿并刷新来源后重试。")


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
    policy = current_policy()
    if policy.valid and policy.mode != "all":
        with get_db_connection() as conn:
            ctx["ai_availability"] = ai_availability(conn, int(user["id"]))
    else:
        ctx["ai_availability"] = ai_availability()
    ctx.update(extra)
    return ctx


# ===========================================================================
# Pages
# ===========================================================================
@router.get("/resume", response_class=HTMLResponse)
def resume_home(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "resume/home.html", _page_context(request, user, "home"))


@router.get("/resume/profile/personal", response_class=HTMLResponse)
def resume_personal_page(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    student_id = int(user["id"])
    with get_db_connection() as conn:
        profile.seed_personal_info_from_platform(conn, student_id, user)
        conn.commit()
    return templates.TemplateResponse(request, "resume/personal.html", _page_context(request, user, "personal"))


@router.get("/resume/job-targets", response_class=HTMLResponse)
def resume_job_targets_page(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "resume/job_targets.html",
        _page_context(request, user, "job_targets"),
    )


@router.get("/resume/applications", response_class=HTMLResponse)
def resume_applications_page(request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "resume/applications.html",
        _page_context(request, user, "applications"),
    )


@router.get("/resume/profile/{section}", response_class=HTMLResponse)
async def resume_section_page(section: str, request: Request, user: dict = Depends(get_current_user)):
    if not _is_student(user):
        return RedirectResponse("/dashboard", status_code=302)
    key = _SECTION_PAGE_KEYS.get(str(section))
    if not key:
        raise HTTPException(404, "页面不存在")
    student_id = int(user["id"])
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
    payload["revision"] = _expected_revision(payload)
    def command():
        try:
            with get_db_connection() as conn:
                info = profile.update_personal_info(conn, student_id, payload)
                conn.commit()
        except ValueError as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "info": info}
    return await run_in_threadpool(command)


@router.post("/api/resume/personal/suggest", response_class=JSONResponse)
async def api_personal_suggest(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    def command():
        try:
            with get_db_connection() as conn:
                state = suggestions.queue_suggestion(conn, student_id, "personal")
                conn.commit()
                return state
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
    return JSONResponse(await run_in_threadpool(command), status_code=202)


@router.post("/api/resume/personal/avatar", response_class=JSONResponse)
async def api_personal_avatar(user: dict = Depends(get_current_user), file: UploadFile = File(...), revision: int | None = Form(None)):
    student_id = _require_student(user)
    expected = _expected_revision({"revision": revision})
    content_type = str(file.content_type or "").split(";", 1)[0].lower()
    if content_type not in CHAT_IMAGE_TYPES:
        raise HTTPException(400, "仅支持 PNG / JPG / GIF / WebP 图片")
    await resume_import.validate_upload_stream(file, max_bytes=attach.RESUME_ATTACHMENT_MAX_BYTES)
    result = await save_file_globally(file)
    if not result:
        raise HTTPException(500, "头像保存失败")
    if int(result.get("size") or 0) > attach.RESUME_ATTACHMENT_MAX_BYTES:
        raise HTTPException(413, "头像不能超过 5MB")
    def command():
        try:
            with get_db_connection() as conn:
                new_revision = profile.set_personal_avatar(conn, student_id, result["hash"], content_type, expected_revision=expected)
                conn.commit()
                return new_revision
        except ValueError as exc:
            raise _resume_error(exc) from exc
    new_revision = await run_in_threadpool(command)
    return {"ok": True, "revision": new_revision, "avatar_url": f"/api/resume/personal/avatar?v={result['hash'][:12]}"}


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
# API — job-description analysis
# ===========================================================================
@router.get("/api/resume/job-targets", response_class=JSONResponse)
def api_job_targets_list(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        items = job_targets.list_job_targets(conn, student_id)
        conn.commit()
    return {"ok": True, "items": items}


@router.post("/api/resume/job-targets/analyze", response_class=JSONResponse)
async def api_job_target_analyze(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                item = job_targets.create_job_target(
                    conn,
                    student_id,
                    target_position=payload.get("target_position"),
                    company_name=payload.get("company_name"),
                    job_description=payload.get("job_description"),
                )
                record_student_career_event_safely(
                    conn,
                    student_id,
                    surface="job",
                    event_name="job_description_analyzed",
                    context={
                        "job_id": item.get("id"),
                        "target_position": item.get("target_position"),
                        "status": item.get("status"),
                    },
                )
                conn.commit()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "item": item}
    return await run_in_threadpool(command)


@router.get("/api/resume/job-targets/{target_id}", response_class=JSONResponse)
def api_job_target_get(target_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            item = job_targets.get_job_target(conn, student_id, target_id)
            conn.commit()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "item": item}


@router.delete("/api/resume/job-targets/{target_id}", response_class=JSONResponse)
def api_job_target_delete(target_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            job_targets.delete_job_target(conn, student_id, target_id)
            conn.commit()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@router.get("/api/resume/applications", response_class=JSONResponse)
def api_resume_applications_list(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        items = applications.list_applications(conn, student_id)
        conn.commit()
    return {
        "ok": True,
        "items": items,
        "statuses": [
            {"value": status, "label": applications.STATUS_LABELS[status]}
            for status in applications.APPLICATION_STATUSES
        ],
    }


@router.post("/api/resume/applications", response_class=JSONResponse)
async def api_resume_application_create(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                item = applications.create_application(conn, student_id, payload)
                record_student_career_event_safely(
                    conn,
                    student_id,
                    surface="job",
                    event_name="application_created",
                    context={
                        "application_id": item.get("id"),
                        "job_id": item.get("job_target_id"),
                        "resume_id": item.get("resume_id"),
                        "target_position": item.get("target_position"),
                        "status": item.get("status"),
                    },
                )
                conn.commit()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "item": item}
    return await run_in_threadpool(command)


@router.put("/api/resume/applications/{application_id}", response_class=JSONResponse)
async def api_resume_application_update(
    application_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    student_id = _require_student(user)
    payload = await _read_json(request)
    payload["revision"] = _expected_revision(payload)
    def command():
        try:
            with get_db_connection() as conn:
                item = applications.update_application(conn, student_id, application_id, payload)
                status_changed = bool(item.pop("_status_changed", False))
                if status_changed:
                    record_student_career_event_safely(
                        conn,
                        student_id,
                        surface="job",
                        event_name="application_status_changed",
                        context={
                            "application_id": item.get("id"),
                            "job_id": item.get("job_target_id"),
                            "resume_id": item.get("resume_id"),
                            "target_position": item.get("target_position"),
                            "status": item.get("status"),
                        },
                    )
                conn.commit()
        except ValueError as exc:
            raise _resume_error(exc) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "item": item}
    return await run_in_threadpool(command)


@router.delete("/api/resume/applications/{application_id}", response_class=JSONResponse)
def api_resume_application_delete(application_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            applications.delete_application(conn, student_id, application_id)
            conn.commit()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


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
    return {"ok": True, "items": items, "meta": {"experience_kinds": [{"value": key, "label": label} for key, label in profile.EXPERIENCE_KINDS.items()], "education_degrees": list(profile.EDUCATION_DEGREES)}}


@router.post("/api/resume/sections/{section}", response_class=JSONResponse)
async def api_section_create(section: str, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                item_id = profile.create_section_item(conn, student_id, section, payload)
                item = profile.get_section_item(conn, student_id, section, item_id)
                conn.commit()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "id": item_id, "item": item}
    return await run_in_threadpool(command)


@router.put("/api/resume/sections/{section}/{item_id}", response_class=JSONResponse)
async def api_section_update(section: str, item_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    payload["revision"] = _expected_revision(payload)
    def command():
        try:
            with get_db_connection() as conn:
                profile.update_section_item(conn, student_id, section, item_id, payload)
                item = profile.get_section_item(conn, student_id, section, item_id)
                conn.commit()
        except ValueError as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "item": item}
    return await run_in_threadpool(command)


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
    def command():
        try:
            with get_db_connection() as conn:
                state = suggestions.queue_suggestion(conn, student_id, "self_intro", text=str(payload.get("text") or ""))
                conn.commit()
                return state
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
    return JSONResponse(await run_in_threadpool(command), status_code=202)


@router.post("/api/resume/self-intro/generate", response_class=JSONResponse)
async def api_self_intro_generate(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    def command():
        try:
            with get_db_connection() as conn:
                intro_id, job = gen.begin_intro_job(conn, student_id)
                conn.commit()
        except ValueError as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": intro_id, "job": job}
    return await run_in_threadpool(command)


@router.post("/api/resume/education/seed", response_class=JSONResponse)
async def api_education_seed(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    def command():
        with get_db_connection() as conn:
            item_id = gen.seed_education_from_context(conn, student_id)
            conn.commit()
        return {"ok": True, "started": False, "created": bool(item_id), "id": item_id}
    return await run_in_threadpool(command)


@router.get("/api/resume/suggestions/jobs/{job_id}", response_class=JSONResponse)
def api_suggestion_job(job_id: int, user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            return suggestions.suggestion_state(conn, _require_student(user), job_id)
    except (ValueError, LookupError) as exc:
        raise _resume_error(exc) from exc


@router.post("/api/resume/suggestions/jobs/{job_id}/{action}", response_class=JSONResponse)
async def api_suggestion_action(job_id: int, action: str, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    if action not in {"cancel", "retry"}:
        raise HTTPException(404, "操作不存在")
    def command():
        try:
            with get_db_connection() as conn:
                suggestions._owned_job(conn, student_id, job_id)
                if action == "cancel":
                    cancel_student_career_job(conn, job_id, student_id=student_id)
                    state = suggestions.suggestion_state(conn, student_id, job_id)
                else:
                    state = suggestions.retry_suggestion(conn, student_id, job_id)
                conn.commit()
                return state
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
    return JSONResponse(await run_in_threadpool(command), status_code=202)


@router.get("/api/resume/self-intro/{intro_id}/job", response_class=JSONResponse)
def api_intro_job(intro_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            item = profile.get_section_item(conn, student_id, "self_intro", intro_id)
            job = public_job_state(conn, item.get("active_job_id"), student_id=student_id)
    except ValueError as exc:
        raise _resume_error(exc) from exc
    return {"ok": True, "job": job, "status": item["status"], "revision": item["revision"]}


@router.post("/api/resume/self-intro/{intro_id}/job/{action}", response_class=JSONResponse)
async def api_intro_job_action(intro_id: int, action: str, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    if action not in {"cancel", "retry"}:
        raise HTTPException(404, "操作不存在")
    def command():
        try:
            with get_db_connection() as conn:
                item = profile.get_section_item(conn, student_id, "self_intro", intro_id)
                revision = docs.require_revision(item, _expected_revision(payload))
                status = "generating" if action == "retry" else ("ready" if item.get("content_md") else "draft")
                changed = conn.execute("UPDATE resume_self_intros SET revision=revision+1,status=?,active_job_id='',error_text='' WHERE id=? AND student_id=? AND revision=?", (status, intro_id, student_id, revision))
                if changed.rowcount != 1:
                    raise docs.ResumeConflict("自我介绍已更新，请保留输入并重新载入。")
                if item.get("active_job_id"):
                    cancel_student_career_job(conn, int(item["active_job_id"]), student_id=student_id)
                job = gen.queue_intro_job(conn, student_id, intro_id) if action == "retry" else {}
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": intro_id, "revision": revision + 1, "status": status, "job": job}
    return await run_in_threadpool(command)


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
    def preflight():
        with get_db_connection() as conn:
            attach.check_attachment_owner(conn, student_id, owner_kind, owner_id)
    await run_in_threadpool(preflight)
    saved = await attach.prepare_attachment_upload(file)
    def command():
        try:
            with get_db_connection() as conn:
                item = attach.bind_attachment(conn, student_id, owner_kind, owner_id, saved)
                conn.commit()
                return item
        except ValueError as exc:
            raise _resume_error(exc) from exc
    item = await run_in_threadpool(command)
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
        profile.seed_personal_info_from_platform(conn, student_id, user)
        bundle = profile.collect_profile_bundle(conn, student_id)
        position_options = profile.build_expected_position_options(conn, student_id)
        conn.commit()
    return {
        "ok": True,
        "personal": bundle.get("personal") or {},
        "personal_labels": render._PERSONAL_LABELS,
        "position_options": position_options,
        "education": bundle.get("education", []),
        "experience": bundle.get("experience", []),
        "skill": bundle.get("skill", []),
        "certificate": bundle.get("certificate", []),
        "self_intro": bundle.get("self_intro", []),
        "templates": render.list_templates(),
    }


@router.get("/api/resume/readiness", response_class=JSONResponse)
def api_resume_readiness(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        data = readiness.build_resume_readiness(conn, student_id)
        conn.commit()
    return {"ok": True, "readiness": data}


@router.post("/api/resume/builder/validate", response_class=JSONResponse)
async def api_resume_builder_validate(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        with get_db_connection() as conn:
            result = readiness.validate_resume_build(
                conn,
                student_id,
                target_position=str(payload.get("target_position") or ""),
                layout=payload.get("layout"),
            )
            conn.commit()
        return {"ok": True, "validation": result}
    return await run_in_threadpool(command)


@router.get("/api/resume/resumes", response_class=JSONResponse)
def api_resumes_list(user: dict = Depends(get_current_user), limit: int = 50, offset: int = 0, compact: bool = False):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        items = (docs.list_resume_states if compact else docs.list_resumes)(conn, student_id, limit=limit, offset=offset)
        conn.commit()
    return {"ok": True, "items": items, "has_more": len(items) >= max(1, min(100, limit)), "offset": max(0, offset)}


@router.get("/api/resume/resumes/{resume_id}", response_class=JSONResponse)
def api_resume_get(resume_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            item = docs.get_resume(conn, student_id, resume_id)
            try:
                version = docs.get_version(conn, student_id, resume_id)
                item["snapshot"] = version["snapshot"]
                item["content_snapshot"] = version["snapshot"].get("bundle") or {}
                item["content_overrides"] = version["snapshot"].get("content_overrides") or []
            except LookupError:
                item["content_overrides"] = []
            conn.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    item.pop("render_html", None)  # large; fetched via /preview
    return {"ok": True, "resume": item}


def _publish_validation(conn, student_id: int, resume_id: int) -> None:
    validation = readiness.validate_frozen_resume(docs.get_version(conn, student_id, resume_id)["snapshot"])
    if not validation["ok"]:
        raise ValueError("请先完善：" + "、".join(item["label"] for item in validation["missing"]))


@router.post("/api/resume/resumes", response_class=JSONResponse)
async def api_resume_create(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    draft = bool(payload.get("draft", False))
    def command():
        try:
            with get_db_connection() as conn:
                profile._ensure_personal_row(conn, student_id)
                # Serialize only this student's create commands, keeping repeated
                # client ids stable without relying on process-local locks.
                conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ?", (student_id,))
                client_id = str(payload.get("client_id") or "").strip()[:100] or None
                existing = conn.execute("SELECT id,archived FROM resumes WHERE student_id = ? AND client_id = ?", (student_id, client_id)).fetchone() if client_id else None
                if existing and existing["archived"]:
                    raise docs.ResumeConflict("这份本地草稿已删除，请创建新的草稿。")
                if existing:
                    item = docs.get_resume(conn, student_id, int(existing["id"]))
                    conn.commit()
                    return {"ok": True, "id": item["id"], "revision": item["revision"], "status": item["status"], "reused": True}
                _validate_source_context(conn, student_id, payload.get("source_context"))
                personal = profile.get_personal_info(conn, student_id)
                resume_id = docs.create_resume(conn, student_id, title=str(payload.get("title") or "我的简历"),
                    target_position=str(payload.get("target_position") or personal.get("expected_position") or ""),
                    template_key=str(payload.get("template_key") or "classic"), layout=payload.get("layout"),
                    source_context=payload.get("source_context"), draft=draft, content_overrides=payload.get("content_overrides"), client_id=client_id,
                    optimized_summary_md=payload.get("optimized_summary_md"), tech_stack=payload.get("tech_stack"))
                job = {}
                if not draft:
                    _publish_validation(conn, student_id, resume_id)
                    job = gen.queue_resume_job(conn, student_id, resume_id, "render")
                item = docs.get_resume(conn, student_id, resume_id)
                record_student_career_event_safely(conn, student_id, surface="resume", event_name="resume_created", context={"resume_id": resume_id})
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": item["revision"], "status": item["status"], "job": job}
    return await run_in_threadpool(command)


@router.put("/api/resume/resumes/{resume_id}", response_class=JSONResponse)
async def api_resume_update(resume_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    expected = _expected_revision(payload)
    draft = bool(payload.get("draft", False))
    def command():
        try:
            with get_db_connection() as conn:
                current = docs.get_resume(conn, student_id, resume_id)
                _validate_source_context(conn, student_id, payload.get("source_context"), previous=current.get("source_context"))
                revision = docs.update_resume(conn, student_id, resume_id,
                    title=str(payload.get("title", current["title"])), target_position=str(payload.get("target_position", current["target_position"])),
                    template_key=str(payload.get("template_key", current["template_key"])), layout=payload.get("layout", current.get("layout")),
                    source_context=payload.get("source_context"), expected_revision=expected, draft=draft, content_overrides=payload.get("content_overrides"),
                    optimized_summary_md=payload.get("optimized_summary_md"), tech_stack=payload.get("tech_stack"))
                supersede_student_career_jobs(conn, scope_type="resume", scope_id=str(resume_id), student_id=student_id)
                job = {}
                if not draft:
                    _publish_validation(conn, student_id, resume_id)
                    job = gen.queue_resume_job(conn, student_id, resume_id, "render")
                item = docs.get_resume(conn, student_id, resume_id)
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": revision, "status": item["status"], "job": job}
    return await run_in_threadpool(command)


@router.post("/api/resume/resumes/{resume_id}/publish", response_class=JSONResponse)
async def api_resume_publish(resume_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                current = docs.get_resume(conn, student_id, resume_id)
                docs.require_revision(current, _expected_revision(payload))
                _publish_validation(conn, student_id, resume_id)
                job = gen.queue_resume_job(conn, student_id, resume_id, "render")
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": current["revision"], "job": job}
    return await run_in_threadpool(command)


@router.post("/api/resume/resumes/{resume_id}/optimize", response_class=JSONResponse)
async def api_resume_optimize(resume_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                current = docs.get_resume(conn, student_id, resume_id)
                docs.require_revision(current, _expected_revision(payload))
                _publish_validation(conn, student_id, resume_id)
                job = gen.queue_resume_job(conn, student_id, resume_id, "optimize")
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": current["revision"], "job": job, "status": "optimizing"}
    return await run_in_threadpool(command)


@router.post("/api/resume/import", response_class=JSONResponse)
async def api_resume_import(user: dict = Depends(get_current_user), file: UploadFile = File(...)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            require_student_ai(conn, student_id)
    except CareerRolloutLimited as exc:
        raise _resume_error(exc) from exc
    meta = resume_import.validate_import_file(str(file.filename or ""), str(file.content_type or ""))
    size = await resume_import.validate_upload_stream(file)
    result = await save_file_globally(file)
    if not result:
        raise HTTPException(500, "简历文件保存失败")
    def command():
        try:
            with get_db_connection() as conn:
                profile._ensure_personal_row(conn, student_id)
                conn.execute("UPDATE resume_personal_info SET student_id = student_id WHERE student_id = ?", (student_id,))
                existing = conn.execute("SELECT id FROM resumes WHERE student_id = ? AND source_file_hash = ? AND archived = 0 ORDER BY id DESC LIMIT 1", (student_id, result["hash"])).fetchone()
                if existing:
                    item = docs.get_resume(conn, student_id, int(existing["id"]))
                    job = public_job_state(conn, item.get("active_job_id"), student_id=student_id)
                    conn.commit()
                    return {"ok": True, "id": item["id"], "revision": item["revision"], "status": item["status"], "job": job, "reused": True}
                resume_id = docs.create_import_resume(conn, student_id, filename=meta["filename"], file_hash=result["hash"], mime_type=meta["mime_type"], file_size=size)
                job = gen.queue_resume_job(conn, student_id, resume_id, "import")
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": 1, "status": "parsing", "job": job}
    return await run_in_threadpool(command)


@router.get("/api/resume/resumes/{resume_id}/candidates", response_class=JSONResponse)
def api_resume_candidates(resume_id: int, user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            items = docs.list_candidates(conn, _require_student(user), resume_id)
    except (ValueError, LookupError) as exc:
        raise _resume_error(exc) from exc
    return {"ok": True, "items": items}


@router.post("/api/resume/resumes/{resume_id}/candidates/{candidate_id}/accept", response_class=JSONResponse)
async def api_resume_candidate_accept(resume_id: int, candidate_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                candidate = docs.get_candidate(conn, student_id, resume_id, candidate_id)
                if candidate["kind"] == "import":
                    revision = resume_import.accept_import_candidate(conn, student_id, resume_id, candidate_id, _expected_revision(payload), selections=payload)
                else:
                    revision = docs.accept_optimization(conn, student_id, resume_id, candidate_id, _expected_revision(payload))
                validation = readiness.validate_frozen_resume(docs.get_version(conn, student_id, resume_id, revision)["snapshot"])
                job = {}
                if validation["ok"]:
                    job = gen.queue_resume_job(conn, student_id, resume_id, "render")
                else:
                    conn.execute("UPDATE resumes SET status = 'draft' WHERE id = ? AND student_id = ? AND revision = ?", (resume_id, student_id, revision))
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": revision, "job": job, "validation": validation}
    return await run_in_threadpool(command)


@router.post("/api/resume/resumes/{resume_id}/candidates/{candidate_id}/reject", response_class=JSONResponse)
async def api_resume_candidate_reject(resume_id: int, candidate_id: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                current = docs.get_resume(conn, student_id, resume_id)
                docs.require_revision(current, _expected_revision(payload))
                docs.get_candidate(conn, student_id, resume_id, candidate_id)
                conn.execute("UPDATE resumes SET status = ? WHERE id = ? AND student_id = ? AND revision = ?", ("ready" if current.get("render_revision") == current["revision"] else "draft", resume_id, student_id, current["revision"]))
                conn.execute("UPDATE resume_candidates SET status = 'rejected' WHERE id = ? AND student_id = ? AND status = 'pending'", (candidate_id, student_id))
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True}
    return await run_in_threadpool(command)


@router.get("/api/resume/resumes/{resume_id}/versions", response_class=JSONResponse)
def api_resume_versions(resume_id: int, user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            items = docs.list_versions(conn, _require_student(user), resume_id)
    except (ValueError, LookupError) as exc:
        raise _resume_error(exc) from exc
    return {"ok": True, "items": items}


@router.post("/api/resume/resumes/{resume_id}/versions/{version_revision}/restore", response_class=JSONResponse)
async def api_resume_version_restore(resume_id: int, version_revision: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                revision = docs.restore_version(conn, student_id, resume_id, version_revision, _expected_revision(payload))
                supersede_student_career_jobs(conn, scope_type="resume", scope_id=str(resume_id), student_id=student_id)
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": revision}
    return await run_in_threadpool(command)


@router.get("/api/resume/resumes/{resume_id}/job", response_class=JSONResponse)
def api_resume_job(resume_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        with get_db_connection() as conn:
            resume = docs.get_resume(conn, student_id, resume_id)
            job = public_job_state(conn, resume.get("active_job_id"), student_id=student_id)
    except (ValueError, LookupError) as exc:
        raise _resume_error(exc) from exc
    return {"ok": True, "job": job, "status": resume["status"], "revision": resume["revision"], "render_revision": resume["render_revision"]}


@router.post("/api/resume/resumes/{resume_id}/job/{action}", response_class=JSONResponse)
async def api_resume_job_action(resume_id: int, action: str, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    if action not in {"cancel", "retry"}:
        raise HTTPException(404, "操作不存在")
    def command():
        try:
            with get_db_connection() as conn:
                resume = docs.get_resume(conn, student_id, resume_id)
                docs.require_revision(resume, _expected_revision(payload))
                cursor = conn.execute("UPDATE resumes SET status = 'draft' WHERE id = ? AND student_id = ? AND revision = ?", (resume_id, student_id, resume["revision"]))
                if cursor.rowcount != 1:
                    raise docs.ResumeConflict("简历已更新，请重试。")
                if action == "cancel":
                    job = cancel_student_career_job(conn, int(resume["active_job_id"]), student_id=student_id) if resume.get("active_job_id") else {}
                    conn.execute("UPDATE resumes SET active_job_id = '' WHERE id = ? AND student_id = ?", (resume_id, student_id))
                else:
                    prior = public_job_state(conn, resume.get("active_job_id"), student_id=student_id)
                    kind = str(prior.get("task_type") or "").removeprefix("resume_")
                    if kind not in {"render", "optimize", "import"}:
                        kind = "import" if resume.get("source_file_hash") and not resume.get("render_revision") else "render"
                    job = gen.queue_resume_job(conn, student_id, resume_id, kind, retry=True)
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, "id": resume_id, "revision": resume["revision"], "job": job}
    return await run_in_threadpool(command)


@router.post("/api/resume/resumes/{resume_id}/import-conflicts/{conflict_index}/accept", response_class=JSONResponse)
async def api_resume_import_conflict_accept(resume_id: int, conflict_index: int, request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    payload = await _read_json(request)
    def command():
        try:
            with get_db_connection() as conn:
                current = docs.get_resume(conn, student_id, resume_id)
                docs.require_revision(current, _expected_revision(payload))
                result = resume_import.accept_import_conflict(conn, student_id, resume_id, conflict_index)
                supersede_student_career_jobs(conn, scope_type="resume", scope_id=str(resume_id), student_id=student_id)
                conn.commit()
        except (ValueError, LookupError) as exc:
            raise _resume_error(exc) from exc
        return {"ok": True, **result}
    return await run_in_threadpool(command)


@router.delete("/api/resume/resumes/{resume_id}", response_class=JSONResponse)
def api_resume_delete(resume_id: int, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        docs.delete_resume(conn, student_id, resume_id)
        supersede_student_career_jobs(conn, scope_type="resume", scope_id=str(resume_id), student_id=student_id)
        conn.commit()
    return {"ok": True}


def _rendered_version(conn, student_id: int, resume_id: int, revision: int | None):
    resume = docs.get_resume(conn, student_id, resume_id, include_archived=True)
    chosen = revision if revision is not None else int(resume.get("render_revision") or resume.get("revision") or 1)
    version = docs.get_version(conn, student_id, resume_id, chosen)
    if not version.get("render_html"):
        raise HTTPException(409, "该版本尚未完成渲染，请稍后再试或选择已完成版本。")
    return version


@router.get("/api/resume/resumes/{resume_id}/preview", response_class=HTMLResponse)
def api_resume_preview(resume_id: int, user: dict = Depends(get_current_user), revision: int | None = None):
    try:
        with get_db_connection() as conn:
            version = _rendered_version(conn, _require_student(user), resume_id, revision)
    except (ValueError, LookupError) as exc:
        raise _resume_error(exc) from exc
    return HTMLResponse(version["render_html"], headers={"X-Resume-Revision": str(version["revision"]), "Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"})


@router.get("/api/resume/resumes/{resume_id}/export")
def api_resume_export(resume_id: int, fmt: str = "pdf", user: dict = Depends(get_current_user), revision: int | None = None):
    from urllib.parse import quote
    from ..services.libreoffice_service import LibreOfficeBusy
    fmt = "docx" if fmt.lower() == "docx" else "pdf"
    try:
        with get_db_connection() as conn:
            version = _rendered_version(conn, _require_student(user), resume_id, revision)
        data = render.export_resume_cached(version["render_html"], fmt)
    except (ValueError, LookupError) as exc:
        raise _resume_error(exc) from exc
    except (render.ResumeExportBusy, LibreOfficeBusy) as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(getattr(exc, "retry_after", 10))}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "文档转换暂不可用，请稍后重试。") from exc
    title = str(version["snapshot"].get("title") or "简历")
    media = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(content=data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(title + '.' + fmt)}", "X-Resume-Revision": str(version["revision"]), "Cache-Control": "private, no-store"})
