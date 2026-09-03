"""Owner-only editing API. Synchronous storage work runs in the worker pool."""

import json
import tempfile
from html import escape
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ..database import get_db_connection
from ..core import templates
from ..dependencies import get_current_teacher, get_current_user
from ..services.lessondoc import assets, custom_elements, editability, editor_service as editor, media, render
from ..services.lessondoc import pack_service, ai_edit, generate

router = APIRouter(prefix="/api/lessondoc/editor")
page_router = APIRouter(prefix="/materials/lessondoc-editor")
MAX_REQUEST_BYTES = 2 * 1024 * 1024 + 4096


def _return_path(value):
    value = str(value or "")
    if not value.startswith("/") or value.startswith("//"):
        return "/manage/materials"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or "\\" in value or any(ord(c) < 32 for c in value):
        return "/manage/materials"
    allowed = ("/manage/materials", "/manage/teaching/materials", "/manage/teaching/courses", "/materials/render-view/", "/materials/view/", "/classroom/")
    if any(parts.path == p or (p.endswith("/") and parts.path.startswith(p)) for p in allowed):
        return value
    return "/manage/materials"


@page_router.get("/{pack_id}", response_class=HTMLResponse)
def get_editor_page(request: Request, pack_id: int, lesson: int = Query(0, ge=0, le=200),
                    slide: int = Query(1, ge=1, le=40), slide_id: str = Query("", max_length=40),
                    return_to: str = Query("", max_length=2048), user: dict = Depends(get_current_user)):
    try:
        if user.get("role") != "teacher":
            raise editor.EditorError("FORBIDDEN", "只有文档所有者可以编辑学习文档", 403)
        _require_enabled()
        with get_db_connection() as conn:
            pack = editor.owned_pack(conn, pack_id, int(user["id"]))
            editor._lesson_state(conn, pack, lesson)
            manifest = pack_service.read_manifest(conn, pack)
        config = dict(packId=pack_id, lessonNo=lesson, userId=int(user["id"]), slide=slide, slideId=slide_id,
                      rootMaterialId=pack["root_material_id"], returnUrl=_return_path(return_to),
                      lessons=[{"n": item["n"], "title": item.get("title", "")} for item in manifest.get("lessons") or []])
        return templates.TemplateResponse(request, "lessondoc_editor.html", {"request": request, "user_info": user, "editor_config": config},
                                          headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    except editor.EditorError as exc:
        return HTMLResponse(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>无法编辑</title><body><p>{escape(str(exc))}</p><a href="/manage/materials">返回材料库</a></body></html>',
                            status_code=exc.status, headers={"Cache-Control": "private, no-store"})


def _number(payload, key, *, default=None, minimum=1, maximum=2_147_483_647):
    raw = payload.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or not minimum <= raw <= maximum:
        raise editor.EditorError("INVALID_PARAMETER", f"{key} 参数无效")
    return raw


def _error(exc):
    return JSONResponse({"status": "error", "detail": str(exc), "error": {"code": exc.code, "message": str(exc), **exc.details}}, status_code=exc.status)


def _require_enabled():
    if not editability.editor_enabled():
        raise editor.EditorError("EDITOR_DISABLED", "学习文档编辑器暂时维护中，现有文档仍可阅读", 503)


async def _payload(request):
    _require_enabled()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise editor.EditorError("REQUEST_TOO_LARGE", "编辑请求不能超过 2 MiB", 413)
        chunks.append(chunk)
    try:
        payload = await run_in_threadpool(json.loads, b"".join(chunks))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise editor.EditorError("INVALID_JSON", "请求不是有效 JSON", 400) from exc
    if not isinstance(payload, dict):
        raise editor.EditorError("INVALID_JSON", "请求必须为 JSON 对象", 400)
    return payload


def _call(function, **kwargs):
    try:
        with get_db_connection() as conn:
            result = function(conn, **kwargs)
        return {"status": "ok", "result": result}
    except editor.EditorError as exc:
        return _error(exc)


@router.get("/packs/{pack_id}/document")
def get_editor_document(pack_id: int, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    return _call(editor.load_document, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no)


@router.get("/editability/{material_id}")
def get_editor_editability(material_id: int, path: str = Query("", max_length=1024), user: dict = Depends(get_current_teacher)):
    return _call(editability.inspect_material, material_id=material_id, teacher_id=int(user["id"]), subpath=path)


@router.get("/legacy-context/{material_id}")
def get_legacy_context(material_id: int, user: dict = Depends(get_current_teacher)):
    return _call(editability.legacy_context, material_id=material_id, teacher_id=int(user["id"]))


@page_router.get("/{pack_id}/preview", response_class=HTMLResponse)
def get_editor_preview(pack_id: int, lesson: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_user)):
    try:
        if user.get("role") != "teacher":
            raise editor.EditorError("FORBIDDEN", "只有文档所有者可以打开编辑预览", 403)
        with get_db_connection() as conn:
            loaded = editor.load_document(conn, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson)
        html = render.render_editor_preview(loaded["document"], root_material_id=loaded["root_material_id"], lesson_no=lesson, asset_version=assets.assets_fingerprint())
        return HTMLResponse(html, headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'none'",
        })
    except editor.EditorError as exc:
        return HTMLResponse(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>无法打开编辑预览</title><body><p>{escape(str(exc))}</p></body></html>',
                            status_code=exc.status, headers={"Cache-Control": "private, no-store"})


@router.post("/packs/{pack_id}/validate")
async def validate_editor_document(pack_id: int, request: Request, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
    except editor.EditorError as exc:
        return _error(exc)
    def validate_owned(conn, **kwargs):
        pack = editor.owned_pack(conn, pack_id, int(user["id"]))
        editor._lesson_state(conn, pack, lesson_no)
        clean, warnings, diagnostics = editor.normalize_document(payload.get("document"), lesson_no)
        return {"document": clean, "warnings": warnings, "diagnostics": diagnostics, "valid": not any(d["destructive"] for d in diagnostics)}
    return await run_in_threadpool(_call, validate_owned)


@router.put("/packs/{pack_id}/document")
async def put_editor_document(pack_id: int, request: Request, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
    except editor.EditorError as exc:
        return _error(exc)
    return await run_in_threadpool(_call, editor.save_document, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no,
                                   document=payload.get("document"), expected_revision=payload.get("revision"), operation_id=payload.get("operation_id"))


@router.post("/packs/{pack_id}/ai-proposal")
async def propose_editor_improvement(pack_id: int, request: Request, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
        prepared = await run_in_threadpool(_call, ai_edit.prepare, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no,
                                          document=payload.get("document"), revision=payload.get("revision"), slide_id=payload.get("slide_id", ""),
                                          element_id=payload.get("element_id", ""), user_hint=payload.get("user_hint", ""))
        if isinstance(prepared, JSONResponse):
            return prepared
        value = prepared["result"]
        raw = await generate._call_lessondoc_ai(system_prompt=value["system_prompt"], user_message=value["user_message"],
                                                task_priority="interactive", task_label="lessondoc_editor_proposal", timeout=240.0)
        proposal = await run_in_threadpool(ai_edit.apply_proposal, value, raw, lesson_no)
        return await run_in_threadpool(_call, ai_edit.finish, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no, proposal=proposal)
    except editor.EditorError as exc:
        return _error(exc)


@router.get("/packs/{pack_id}/revisions")
def get_editor_revisions(pack_id: int, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    return _call(editor.list_revisions, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no)


@router.get("/packs/{pack_id}/revisions/{revision_id}")
def get_editor_revision(pack_id: int, revision_id: int, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    return _call(editor.preview_revision, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no, revision_id=revision_id)


@router.post("/packs/{pack_id}/revisions/{revision_id}/restore")
async def restore_editor_revision(pack_id: int, revision_id: int, request: Request, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
    except editor.EditorError as exc:
        return _error(exc)
    return await run_in_threadpool(_call, editor.restore_revision, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no,
                                   revision_id=revision_id, expected_revision=payload.get("revision"), operation_id=payload.get("operation_id"))


@router.get("/packs/{pack_id}/media")
def get_editor_media(pack_id: int, lesson_no: int = Query(0, ge=0, le=200), after_id: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=200), user: dict = Depends(get_current_teacher)):
    return _call(media.list_media, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no, after_id=after_id, limit=limit)


@router.post("/packs/{pack_id}/media")
async def upload_editor_media(pack_id: int, request: Request, filename: str = Query(..., min_length=1, max_length=240),
                              lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    """Raw File body, avoiding multipart buffering before enforcing the byte budget."""
    try:
        _require_enabled()
        profile = media.upload_profile(filename, request.headers.get("content-type"))
        def authorize():
            with get_db_connection() as conn:
                pack = editor.owned_pack(conn, pack_id, int(user["id"]))
                editor._lesson_state(conn, pack, lesson_no)
        await run_in_threadpool(authorize)
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > profile["limit"]:
                    raise editor.EditorError("MEDIA_TOO_LARGE", f"此类素材不能超过 {profile['limit'] // media.MIB} MiB", 413)
                await run_in_threadpool(stream.write, chunk)
            stored = await run_in_threadpool(media.verify_and_store, stream, profile)
        return await run_in_threadpool(_call, media.attach_upload, pack_id=pack_id, teacher_id=int(user["id"]), lesson_no=lesson_no, stored=stored)
    except editor.EditorError as exc:
        return _error(exc)


@router.get("/custom-elements")
def get_custom_elements(before_id: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=100), user: dict = Depends(get_current_teacher)):
    return _call(custom_elements.list_elements, teacher_id=int(user["id"]), before_id=before_id, limit=limit)


@router.post("/custom-elements")
async def post_custom_element(request: Request, user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
        pack_id = _number(payload, "pack_id")
        lesson_no = _number(payload, "lesson_no", default=0, minimum=0, maximum=200)
    except editor.EditorError as exc:
        return _error(exc)
    return await run_in_threadpool(_call, custom_elements.save_element, teacher_id=int(user["id"]), pack_id=pack_id, lesson_no=lesson_no,
                                   name=payload.get("name"), element=payload.get("element"), category=payload.get("category", "custom"),
                                   thumbnail_svg=payload.get("thumbnail_svg", ""))


@router.put("/custom-elements/{element_id}")
async def put_custom_element(element_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
    except editor.EditorError as exc:
        return _error(exc)
    return await run_in_threadpool(_call, custom_elements.rename_element, teacher_id=int(user["id"]), element_id=element_id, name=payload.get("name"))


@router.delete("/custom-elements/{element_id}")
def delete_custom_element(element_id: int, user: dict = Depends(get_current_teacher)):
    try:
        _require_enabled()
    except editor.EditorError as exc:
        return _error(exc)
    return _call(custom_elements.delete_element, teacher_id=int(user["id"]), element_id=element_id)


@router.post("/custom-elements/{element_id}/insert")
async def insert_custom_element(element_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
        pack_id = _number(payload, "pack_id")
        lesson_no = _number(payload, "lesson_no", default=0, minimum=0, maximum=200)
    except editor.EditorError as exc:
        return _error(exc)
    return await run_in_threadpool(_call, custom_elements.insert_element, teacher_id=int(user["id"]), element_id=element_id, pack_id=pack_id, lesson_no=lesson_no)


@router.post("/packs/{pack_id}/copy-element")
async def copy_editor_element(pack_id: int, request: Request, lesson_no: int = Query(0, ge=0, le=200), user: dict = Depends(get_current_teacher)):
    try:
        payload = await _payload(request)
        source_pack_id = _number(payload, "source_pack_id")
        source_lesson_no = _number(payload, "source_lesson_no", default=0, minimum=0, maximum=200)
    except editor.EditorError as exc:
        return _error(exc)
    if "elements" in payload:
        return await run_in_threadpool(_call, custom_elements.copy_elements, teacher_id=int(user["id"]), source_pack_id=source_pack_id, source_lesson_no=source_lesson_no,
                                       pack_id=pack_id, lesson_no=lesson_no, elements=payload.get("elements"))
    return await run_in_threadpool(_call, custom_elements.copy_element, teacher_id=int(user["id"]), source_pack_id=source_pack_id, source_lesson_no=source_lesson_no,
                                   pack_id=pack_id, lesson_no=lesson_no, element=payload.get("element"))
