"""学生成就墙页面与数据接口。

- ``GET /achievements``：成就墙页面（打开即评定补发，仅学生本人）。
- ``GET /api/achievements``：同源 JSON。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.student_achievement_service import build_achievement_wall

router = APIRouter()


def _ensure_student(user: dict) -> None:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="成就墙仅面向学生本人开放。")


@router.get("/achievements", response_class=HTMLResponse)
async def achievements_page(request: Request, user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        wall = build_achievement_wall(conn, int(user["id"]))
        conn.commit()
    return templates.TemplateResponse(
        request,
        "achievements.html",
        {
            "request": request,
            "user_info": user,
            "achievement_wall": wall,
        },
    )


@router.get("/api/achievements", response_class=JSONResponse)
async def api_achievements(user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        wall = build_achievement_wall(conn, int(user["id"]))
        conn.commit()
    return {"status": "success", "achievement_wall": wall}
