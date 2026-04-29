from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.message_center_service import (
    add_private_message_block,
    get_message_center_bootstrap,
    get_latest_unread_notification,
    get_message_center_summary,
    get_private_ai_reply_job,
    get_private_message_conversation,
    list_message_center_items,
    list_private_message_blocks,
    list_private_message_contacts,
    mark_message_center_items_read,
    process_private_ai_reply_job,
    remove_private_message_block,
    send_private_message_and_maybe_reply,
    MESSAGE_CATEGORY_PRIVATE,
)
from ..services.rate_limit_service import RateLimitExceededError

router = APIRouter()


def _normalize_scope(scope: Optional[int]) -> Optional[int]:
    if scope is None:
        return None
    return int(scope)


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
    return RedirectResponse(url=f"/profile?{urlencode(params)}", status_code=303)


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
    data = await request.json()
    try:
        result = await send_private_message_and_maybe_reply(
            user,
            contact_identity=str(data.get("contact_identity") or ""),
            class_offering_id=_normalize_scope(data.get("class_offering_id")),
            content=str(data.get("content") or ""),
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

    ai_reply_job = result.get("ai_reply_job")
    if ai_reply_job and ai_reply_job.get("id") is not None:
        background_tasks.add_task(process_private_ai_reply_job, int(ai_reply_job["id"]))

    def _load_summary_and_contacts() -> tuple[dict, list[dict]]:
        with get_db_connection() as conn:
            return get_message_center_summary(conn, user), list_private_message_contacts(conn, user)

    summary, contacts = await asyncio.to_thread(_load_summary_and_contacts)

    return {
        "status": "success",
        **result,
        "summary": summary,
        "contacts": contacts,
    }


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
