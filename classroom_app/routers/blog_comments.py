"""Blog comment floors, replies, editing and soft delete (plan §7, P4).

Separate from ``routers/blog.py`` so the reviewed agent capabilities pinned
to that file stay unchanged; the older comment routes keep working and now
share the same floor model through ``blog_service``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import blog_comment_service as comments
from ..services.blog_safety_service import BlogSafetyBlocked
from ..services.blog_service import BlogModerationLocked, BlogRevisionConflict

router = APIRouter()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, BlogSafetyBlocked):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, BlogRevisionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (PermissionError, BlogModerationLocked)):
        return HTTPException(status_code=403, detail=str(exc) or "没有权限")
    message = str(exc) or "请求无效"
    return HTTPException(status_code=404 if "不存在" in message else 400, detail=message)


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/api/blog/posts/{post_id}/floors", response_class=JSONResponse)
def api_list_floors(
    post_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(comments.FLOORS_PER_PAGE, ge=1, le=comments.MAX_FLOORS_PER_PAGE),
    around: Optional[int] = Query(None, ge=1),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        try:
            return {"status": "success", **comments.list_floors(conn, user, post_id, page=page, limit=limit, around=around)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.get("/api/blog/posts/{post_id}/floors/{root_id}/replies", response_class=JSONResponse)
def api_list_floor_replies(
    post_id: int,
    root_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(comments.REPLIES_PER_PAGE, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        try:
            return {"status": "success", **comments.list_replies(conn, user, post_id, root_id, offset=offset, limit=limit)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.put("/api/blog/comments/{comment_id}", response_class=JSONResponse)
def api_edit_comment(comment_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            comment = comments.edit_comment(conn, user, comment_id, content_md=data.get("content_md"),
                                            expected_revision=data.get("expected_revision"))
            conn.commit()
        except (ValueError, PermissionError) as exc:
            conn.rollback()
            raise _http_error(exc) from exc
    return {"status": "success", "comment": comment}


@router.post("/api/blog/comments/{comment_id}/delete", response_class=JSONResponse)
def api_soft_delete_comment(comment_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = comments.delete_comment(conn, user, comment_id, reason=data.get("reason"))
            conn.commit()
        except (ValueError, PermissionError) as exc:
            conn.rollback()
            raise _http_error(exc) from exc
    return {"status": "success", **result}


@router.get("/api/blog/comments/{comment_id}", response_class=JSONResponse)
def api_get_comment(comment_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            return {"status": "success", "comment": comments.get_comment_view(conn, user, comment_id)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.get("/api/blog/comments/{comment_id}/reply-context", response_class=JSONResponse)
def api_comment_reply_context(comment_id: int, user: dict = Depends(get_current_user)):
    """Quote, comment and allowed faces for replying from a notification card."""
    with get_db_connection() as conn:
        try:
            return {"status": "success", **comments.reply_context(conn, user, comment_id)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc
