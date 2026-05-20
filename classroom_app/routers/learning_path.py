from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.personalized_learning_path_service import (
    build_personalized_learning_path_context,
    update_learning_path_item,
)


router = APIRouter()


def _ensure_student(user: dict) -> None:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="个性化学习路径目前仅面向学生本人开放。")


@router.get("/learning-path", response_class=HTMLResponse)
async def learning_path_page(
    request: Request,
    status: str = Query(default="active"),
    course_id: int | None = Query(default=None),
    q: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _ensure_student(user)
    with get_db_connection() as conn:
        context = build_personalized_learning_path_context(
            conn,
            user,
            status=status,
            course_id=course_id,
            keyword=q,
        )
        conn.commit()
    return templates.TemplateResponse(
        request,
        "learning_path.html",
        {
            "request": request,
            "user_info": user,
            "path_context": context,
        },
    )


@router.get("/api/learning-path/bootstrap", response_class=JSONResponse)
async def api_learning_path_bootstrap(
    status: str = Query(default="active"),
    course_id: int | None = Query(default=None),
    q: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    _ensure_student(user)
    with get_db_connection() as conn:
        context = build_personalized_learning_path_context(
            conn,
            user,
            status=status,
            course_id=course_id,
            keyword=q,
        )
        conn.commit()
    return {"status": "success", "path": context}


@router.post("/api/learning-path/items", response_class=JSONResponse)
async def api_update_learning_path_item(
    request: Request,
    user: dict = Depends(get_current_user),
):
    _ensure_student(user)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="学习路径数据格式不正确。")
    item_key = str(data.get("item_key") or "").strip()
    if not item_key:
        raise HTTPException(status_code=400, detail="缺少学习路径项目。")
    with get_db_connection() as conn:
        try:
            payload = update_learning_path_item(conn, user, item_key=item_key, payload=data)
            summary = build_personalized_learning_path_context(conn, user, status="active")
            conn.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **payload,
        "summary": summary["progress"],
        "stats": summary["stats"],
    }
