"""Blog moderation API and the review desk page (plan §5.2–§5.6).

Kept apart from ``routers/blog.py`` so the reviewed agent capabilities pinned
to that file stay unchanged. Every write commits once, after the service call.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services import blog_moderation_service as moderation
from ..services import blog_review_queue_service as review_queue
from ..services.blog_moderation_states import REASON_LABELS
from ..services.blog_service import get_post_moderation_status
from .ui_parts.common import _build_manage_template_context

router = APIRouter()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc) or "没有权限")
    message = str(exc) or "请求无效"
    if "不存在" in message:
        return HTTPException(status_code=404, detail=message)
    if "刚刚发生了变化" in message:
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=400, detail=message)


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _run(fn, *args, **kwargs) -> dict[str, Any]:
    with get_db_connection() as conn:
        try:
            result = fn(conn, *args, **kwargs)
            conn.commit()
        except (ValueError, PermissionError) as exc:
            conn.rollback()
            raise _http_error(exc) from exc
    return {"status": "success", **(result if isinstance(result, dict) else {"items": result})}


# --- author side ---------------------------------------------------------------

@router.get("/api/blog/posts/{post_id}/moderation", response_class=JSONResponse)
def api_post_moderation_status(post_id: int, user: dict = Depends(get_current_user)):
    """Light poll for the "审核中" banner (plan §5.2)."""
    with get_db_connection() as conn:
        try:
            return {"status": "success", **get_post_moderation_status(conn, user, post_id)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.post("/api/blog/posts/{post_id}/appeal", response_class=JSONResponse)
def api_create_appeal(post_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_user)):
    return _run(moderation.create_appeal, user, post_id, reason=data.get("reason"))


# --- moderation actions ----------------------------------------------------------

@router.post("/api/blog/moderation/{target_type}/{target_id}", response_class=JSONResponse)
def api_moderate(target_type: str, target_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_teacher)):
    return _run(moderation.moderate, user, target_type, target_id, action=str(data.get("action") or ""),
                reason_code=data.get("reason_code"), note=data.get("note"))


@router.get("/api/blog/moderation/reasons", response_class=JSONResponse)
def api_moderation_reasons(user: dict = Depends(get_current_teacher)):
    return {"status": "success", "reasons": [{"code": code, "label": label} for code, label in REASON_LABELS.items()]}


# --- review desk -----------------------------------------------------------------

@router.get("/api/blog/review/queue", response_class=JSONResponse)
def api_review_queue(tab: str = Query("machine"), user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            return {"status": "success", **review_queue.list_queue(conn, user, tab)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.post("/api/blog/review/reports/{target_type}/{target_id}", response_class=JSONResponse)
def api_review_reports(target_type: str, target_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_teacher)):
    return _run(moderation.resolve_reports, user, target_type, target_id, decision=str(data.get("decision") or ""),
                reason_code=data.get("reason_code"), note=data.get("note"))


@router.post("/api/blog/review/appeals/{appeal_id}", response_class=JSONResponse)
def api_review_appeal(appeal_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_teacher)):
    return _run(moderation.decide_appeal, user, appeal_id, decision=str(data.get("decision") or ""), note=data.get("note"))


@router.post("/api/blog/review/reveal/{target_type}/{target_id}", response_class=JSONResponse)
def api_review_reveal(target_type: str, target_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_teacher)):
    return _run(moderation.reveal_identity, user, target_type, target_id,
                reason_kind=data.get("reason_kind"), note=data.get("note"))


@router.get("/api/blog/review/audit", response_class=JSONResponse)
def api_review_audit(
    action: str = Query(""), actor: str = Query(""), target_type: str = Query(""), page: int = Query(1, ge=1),
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        try:
            return {"status": "success", **moderation.list_audit_logs(
                conn, user, action=action, actor=actor, target_type=target_type, page=page)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.get("/api/blog/review/moderators", response_class=JSONResponse)
def api_review_moderators(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            return {"status": "success", "items": moderation.list_moderators(conn, user)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc


@router.post("/api/blog/review/moderators/{teacher_id}", response_class=JSONResponse)
def api_review_set_moderator(teacher_id: int, data: dict = Depends(_json), user: dict = Depends(get_current_teacher)):
    return _run(moderation.set_moderator, user, teacher_id, granted=bool(data.get("granted", True)))


@router.get("/manage/system/blog-review", response_class=HTMLResponse)
def get_blog_review_page(request: Request, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        if not moderation.is_blog_moderator(conn, user):
            raise HTTPException(status_code=403, detail="只有博客管理员可以进入审核台。")
        is_super_admin = moderation.is_super_admin(conn, user)
        counts = review_queue.queue_counts(conn, user)
    tab_items = [{"key": key, "label": label, "badge": counts.get(key) or None} for key, label in review_queue.TABS.items()]
    if is_super_admin:
        tab_items += [{"key": "audit", "label": "审计记录"}, {"key": "moderators", "label": "博客管理员"}]
    return templates.TemplateResponse(
        request,
        "manage/system/blog_review.html",
        _build_manage_template_context(
            request, user, page_title="博客审核", active_page="system_blog_review",
            extra={
                "review_tab_items": tab_items,
                "reason_labels": REASON_LABELS,
                "reveal_reasons": moderation.REVEAL_REASONS,
                "is_super_admin": is_super_admin,
            },
        ),
    )


@router.get("/api/blog/review/teachers", response_class=JSONResponse)
def api_review_search_teachers(q: str = Query(""), user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            return {"status": "success", "items": moderation.search_teachers(conn, user, q)}
        except (ValueError, PermissionError) as exc:
            raise _http_error(exc) from exc
