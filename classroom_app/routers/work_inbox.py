"""待我处理（统一收件箱）：``/manage/me/inbox`` 页 + ``GET /api/work-inbox``。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user, require_teacher_domain
from ..services.work_inbox_service import WORK_INBOX_SOURCES, build_work_inbox
from .ui_parts.common import _build_manage_template_context

router = APIRouter()
_SOURCE_KEYS = {item.key for item in WORK_INBOX_SOURCES}


@router.get("/manage/me/inbox", response_class=HTMLResponse)
async def work_inbox_page(
    request: Request,
    source: str = Query("", max_length=40),
    user: dict = Depends(require_teacher_domain("me")),
):
    wanted = source if source in _SOURCE_KEYS else ""
    with get_db_connection() as conn:
        inbox = build_work_inbox(conn, user, limit=0, source=wanted)
    return templates.TemplateResponse(
        request,
        "manage/work_inbox.html",
        _build_manage_template_context(
            request,
            user,
            page_title="待我处理",
            active_page="work_inbox",
            extra={"inbox": inbox},
        ),
    )


@router.get("/api/work-inbox", response_class=JSONResponse)
async def work_inbox_api(
    source: str = Query("", max_length=40),
    limit: int = Query(20, ge=0, le=200),
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="当前账号没有待处理收件箱")
    wanted = source if source in _SOURCE_KEYS else ""
    with get_db_connection() as conn:
        inbox = build_work_inbox(conn, user, limit=limit, source=wanted)
    return {"status": "success", "inbox": inbox}
