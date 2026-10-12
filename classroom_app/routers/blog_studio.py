"""Creator studio and author pages (docs/blog-closed-loop-plan-2026-10-12.md §9.4, P6).

A separate router so the agent capabilities pinned to ``routers/blog.py`` stay
unchanged. Authors are addressed by opaque face refs, never identities.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import blog_studio_service as studio
from ..services.blog_safety_service import BlogSafetyBlocked

router = APIRouter()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, BlogSafetyBlocked):
        return HTTPException(status_code=422, detail="简介包含不适合公开的内容，请修改")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc) or "没有权限")
    message = str(exc) or "请求无效"
    return HTTPException(status_code=404 if "不存在" in message else 400, detail=message)


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/api/blog/studio/overview", response_class=JSONResponse)
def api_studio_overview(mode: str = Query(default=""), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            return {"status": "success", **studio.overview(conn, user, mode=mode)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.get("/api/blog/studio/comments", response_class=JSONResponse)
def api_studio_comments(
    box: str = Query(default="received", pattern="^(received|sent)$"),
    page: int = Query(default=1, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        try:
            loader = studio.received_comments if box == "received" else studio.sent_comments
            return {"status": "success", "box": box, **loader(conn, user, page=page)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.get("/api/blog/studio/fans", response_class=JSONResponse)
def api_studio_fans(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            return {"status": "success", "followers": studio.followers(conn, user),
                    "following": studio.following(conn, user)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.put("/api/blog/studio/profile", response_class=JSONResponse)
def api_studio_profile(data: dict = Depends(_json), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            bios = studio.set_bios(conn, user, real_bio=data.get("real_bio"), pen_bio=data.get("pen_bio"))
            conn.commit()
        except (ValueError, PermissionError, BlogSafetyBlocked) as exc:
            conn.rollback()
            raise _http_error(exc) from exc
    return {"status": "success", "bios": bios}


@router.get("/api/blog/authors/{ref}", response_class=JSONResponse)
def api_author_page(ref: str, page: int = Query(default=1, ge=1, le=500), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        try:
            result = studio.author_page(conn, user, ref, page=page)
            conn.commit()  # A newly resolved ref is cached.
            return {"status": "success", **result}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc
