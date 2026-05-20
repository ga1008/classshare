from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.feedback_review_service import (
    build_feedback_review_context,
    update_feedback_review_item,
)


router = APIRouter()


def _ensure_student(user: dict) -> None:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="错题本目前仅面向学生本人开放。")


@router.get("/feedback-review", response_class=HTMLResponse)
async def feedback_review_page(
    request: Request,
    status: str = Query(default="active"),
    course_id: int | None = Query(default=None),
    q: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _ensure_student(user)
    with get_db_connection() as conn:
        context = build_feedback_review_context(
            conn,
            user,
            status=status,
            course_id=course_id,
            keyword=q,
        )
    return templates.TemplateResponse(
        request,
        "feedback_review.html",
        {
            "request": request,
            "user_info": user,
            "review_context": context,
        },
    )


@router.get("/api/feedback-review/bootstrap", response_class=JSONResponse)
def api_feedback_review_bootstrap(
    status: str = Query(default="active"),
    course_id: int | None = Query(default=None),
    q: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _ensure_student(user)
    with get_db_connection() as conn:
        return {
            "status": "success",
            "review": build_feedback_review_context(
                conn,
                user,
                status=status,
                course_id=course_id,
                keyword=q,
            ),
        }


@router.post("/api/feedback-review/items", response_class=JSONResponse)
async def api_update_feedback_review_item(
    request: Request,
    user: dict = Depends(get_current_user),
):
    _ensure_student(user)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="复盘数据格式不正确。")
    try:
        submission_id = int(data.get("submission_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="缺少提交记录。")
    question_key = str(data.get("question_key") or "").strip()
    if not question_key:
        raise HTTPException(status_code=400, detail="缺少反馈项标识。")
    with get_db_connection() as conn:
        try:
            payload = update_feedback_review_item(
                conn,
                user,
                submission_id=submission_id,
                question_key=question_key,
                payload=data,
            )
            summary = build_feedback_review_context(conn, user, status="active")
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "summary": summary["progress"],
        "stats": summary["stats"],
    }
