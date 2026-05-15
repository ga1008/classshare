from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.file_service import stream_file
from ..services.message_center_service import (
    add_private_message_block,
    ensure_private_message_attachment_file_payload,
    get_message_center_bootstrap,
    get_latest_unread_notification,
    get_message_center_summary,
    get_private_ai_reply_job,
    get_private_message_conversation,
    list_classroom_private_message_contacts,
    list_message_center_items,
    list_private_message_blocks,
    list_private_message_contacts,
    mark_message_center_items_read,
    open_message_center_notification,
    process_private_ai_reply_job,
    remove_private_message_block,
    send_private_message_and_maybe_reply,
    MESSAGE_CATEGORY_PRIVATE,
)
from ..services.rate_limit_service import RateLimitExceededError

router = APIRouter()
private_attachment_io_semaphore = asyncio.Semaphore(80)


def _normalize_scope(scope: Optional[int]) -> Optional[int]:
    if scope is None:
        return None
    if scope == "":
        return None
    return int(scope)


async def _read_private_message_payload(request: Request) -> tuple[str, Optional[int], str, list[StarletteUploadFile]]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="请求格式不正确")
        return (
            str(data.get("contact_identity") or ""),
            _normalize_scope(data.get("class_offering_id")),
            str(data.get("content") or ""),
            [],
        )

    form = await request.form()
    files: list[StarletteUploadFile] = []
    for field_name in ("attachments", "files"):
        for item in form.getlist(field_name):
            if isinstance(item, StarletteUploadFile):
                files.append(item)
    return (
        str(form.get("contact_identity") or ""),
        _normalize_scope(form.get("class_offering_id")),
        str(form.get("content") or ""),
        files,
    )


@router.get("/message-center", response_class=HTMLResponse)
async def message_center_page(
    request: Request,
    tab: str = Query(default="all"),
    contact: Optional[str] = Query(default=None),
    scope: Optional[int] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    normalized_tab = str(tab or "all")
    params: dict[str, str] = {}
    if normalized_tab == MESSAGE_CATEGORY_PRIVATE:
        params["section"] = "private"
        params["tab"] = MESSAGE_CATEGORY_PRIVATE
        if contact:
            params["contact"] = str(contact)
        if scope is not None:
            params["scope"] = str(scope)
    else:
        params["section"] = "notifications"
        if normalized_tab and normalized_tab != "all":
            params["tab"] = normalized_tab
    return RedirectResponse(url=f"/profile?{urlencode(params)}#profile-message-center", status_code=303)


@router.get("/api/message-center/bootstrap", response_class=JSONResponse)
def api_message_center_bootstrap(
    include_private: bool = Query(default=True),
    private_data: bool = Query(default=True),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        return {
            "status": "success",
            **get_message_center_bootstrap(
                conn,
                user,
                include_private=include_private,
                include_private_data=private_data,
            ),
        }


@router.get("/api/message-center/summary", response_class=JSONResponse)
def api_message_center_summary(
    include_private: bool = Query(default=True),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        return {
            "status": "success",
            "summary": get_message_center_summary(conn, user, include_private=include_private),
            "latest_unread": get_latest_unread_notification(conn, user),
        }


@router.get("/api/message-center/items", response_class=JSONResponse)
def api_message_center_items(
    category: str = Query(default="all"),
    keyword: str = Query(default=""),
    filter_key: str = Query(default="all", alias="filter"),
    limit: int = Query(default=120, ge=1, le=300),
    include_private: bool = Query(default=True),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        items = list_message_center_items(
            conn,
            user,
            category=category,
            keyword=keyword,
            filter_key=filter_key,
            limit=limit,
            include_private=include_private,
        )
        return {
            "status": "success",
            "items": items,
        }


@router.get("/message-center/notifications/{notification_id}/open")
def open_notification_detail(notification_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            item = open_message_center_notification(conn, user, notification_id)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    target_url = str(item.get("link_url") or "").strip() or "/profile?section=notifications#profile-message-center"
    if target_url.startswith("/message-center/notifications/"):
        target_url = "/profile?section=notifications#profile-message-center"
    return RedirectResponse(url=target_url, status_code=303)


@router.post("/api/message-center/read", response_class=JSONResponse)
async def api_message_center_mark_read(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式不正确")
    include_private = bool(data.get("include_private", True))
    with get_db_connection() as conn:
        updated_count = mark_message_center_items_read(
            conn,
            user,
            notification_ids=data.get("notification_ids") or [],
            category=str(data.get("category") or "all"),
            contact_identity=str(data.get("contact_identity") or ""),
            class_offering_id=_normalize_scope(data.get("class_offering_id")),
            include_private=include_private,
        )
        summary = get_message_center_summary(conn, user, include_private=include_private)
        conn.commit()
    return {
        "status": "success",
        "updated_count": updated_count,
        "summary": summary,
    }


@router.get("/api/message-center/private/contacts", response_class=JSONResponse)
def api_private_message_contacts(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return {
            "status": "success",
            "contacts": list_private_message_contacts(conn, user),
        }


@router.get("/api/classrooms/{class_offering_id}/private/contacts", response_class=JSONResponse)
def api_classroom_private_message_contacts(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            payload = list_classroom_private_message_contacts(conn, user, class_offering_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        **payload,
    }


@router.get("/api/message-center/private/conversation", response_class=JSONResponse)
def api_private_message_conversation(
    contact: str = Query(..., min_length=3),
    scope: Optional[int] = Query(default=None),
    limit: int = Query(default=120, ge=1, le=300),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        try:
            conversation = get_private_message_conversation(
                conn,
                user,
                contact_identity=contact,
                class_offering_id=_normalize_scope(scope),
                limit=limit,
            )
            summary = get_message_center_summary(conn, user)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "conversation": conversation,
        "summary": summary,
    }


@router.post("/api/message-center/private/messages", response_class=JSONResponse)
async def api_send_private_message(
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    contact_identity, class_offering_id, content, attachments = await _read_private_message_payload(request)
    try:
        result = await send_private_message_and_maybe_reply(
            user,
            contact_identity=contact_identity,
            class_offering_id=class_offering_id,
            content=content,
            attachments=attachments,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from exc
    finally:
        for file in attachments:
            await file.close()

    ai_reply_job = result.get("ai_reply_job")
    if ai_reply_job and ai_reply_job.get("id") is not None:
        background_tasks.add_task(process_private_ai_reply_job, int(ai_reply_job["id"]))

    def _load_summary_and_contacts() -> tuple[dict, list[dict]]:
        with get_db_connection() as conn:
            if class_offering_id is not None:
                classroom_contacts = list_classroom_private_message_contacts(conn, user, class_offering_id)
                return get_message_center_summary(conn, user), classroom_contacts["contacts"]
            return get_message_center_summary(conn, user), list_private_message_contacts(conn, user)

    summary, contacts = await asyncio.to_thread(_load_summary_and_contacts)

    return {
        "status": "success",
        **result,
        "summary": summary,
        "contacts": contacts,
    }


@router.get("/api/message-center/private/attachments/{attachment_id}")
async def api_private_message_attachment(
    attachment_id: int,
    download: bool = Query(default=False),
    user: dict = Depends(get_current_user),
):
    try:
        attachment_payload = await ensure_private_message_attachment_file_payload(
            user,
            attachment_id,
            "original",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _stream_private_attachment_payload(attachment_payload, download=download)


@router.get("/api/message-center/private/attachments/{attachment_id}/{variant}")
async def api_private_message_attachment_variant(
    attachment_id: int,
    variant: str,
    download: bool = Query(default=False),
    user: dict = Depends(get_current_user),
):
    normalized_variant = str(variant or "").strip().lower()
    if normalized_variant not in {"thumbnail", "preview", "original"}:
        raise HTTPException(status_code=404, detail="Private message attachment variant not found")
    try:
        attachment_payload = await ensure_private_message_attachment_file_payload(
            user,
            attachment_id,
            normalized_variant,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _stream_private_attachment_payload(attachment_payload, download=download)


def _stream_private_attachment_payload(attachment_payload: dict, *, download: bool = False):
    file_path = attachment_payload["path"]

    async def streamed_file():
        async with private_attachment_io_semaphore:
            async for chunk in stream_file(file_path):
                yield chunk

    filename = quote(str(attachment_payload.get("filename") or "attachment"))
    is_inline_image = (
        str(attachment_payload.get("attachment_kind") or "") == "image"
        and str(attachment_payload.get("variant") or "") in {"thumbnail", "preview", "original"}
    )
    disposition = "attachment" if download or not is_inline_image else "inline"
    return StreamingResponse(
        streamed_file(),
        media_type=str(attachment_payload.get("mime_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": f"{disposition}; filename*=utf-8''{filename}",
            "Content-Length": str(int(attachment_payload.get("file_size") or 0)),
            "Cache-Control": "private, max-age=604800",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/message-center/private/ai-jobs/{job_id}", response_class=JSONResponse)
def api_private_ai_reply_job(job_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            job = get_private_ai_reply_job(conn, user, job_id=job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "job": job,
    }


@router.get("/api/message-center/private/blocks", response_class=JSONResponse)
def api_private_message_blocks(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return {
            "status": "success",
            "blocks": list_private_message_blocks(conn, user),
        }


@router.post("/api/message-center/private/blocks", response_class=JSONResponse)
async def api_add_private_message_block(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    with get_db_connection() as conn:
        try:
            block = add_private_message_block(
                conn,
                user,
                contact_identity=str(data.get("contact_identity") or ""),
                class_offering_id=_normalize_scope(data.get("class_offering_id")),
            )
            blocks = list_private_message_blocks(conn, user)
            contacts = list_private_message_contacts(conn, user)
            summary = get_message_center_summary(conn, user)
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "block": block,
        "blocks": blocks,
        "contacts": contacts,
        "summary": summary,
    }


@router.delete("/api/message-center/private/blocks", response_class=JSONResponse)
async def api_remove_private_message_block(
    contact_identity: str = Query(..., min_length=3),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        removed_count = remove_private_message_block(conn, user, contact_identity=contact_identity)
        blocks = list_private_message_blocks(conn, user)
        contacts = list_private_message_contacts(conn, user)
        summary = get_message_center_summary(conn, user)
        conn.commit()
    return {
        "status": "success",
        "removed_count": removed_count,
        "blocks": blocks,
        "contacts": contacts,
        "summary": summary,
    }
