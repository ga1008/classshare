"""学生个人成绩单页面与数据接口。

- ``GET /report-card``：成绩单页面（仅学生本人）。
- ``GET /api/report-card``：同源 JSON。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.student_report_card_service import build_student_report_card

router = APIRouter()


def _ensure_student(user: dict) -> None:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="成绩单仅面向学生本人开放。")


@router.get("/report-card", response_class=HTMLResponse)
async def report_card_page(request: Request, user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        report_card = build_student_report_card(conn, student_id=int(user["id"]))
        conn.commit()
    return templates.TemplateResponse(
        request,
        "report_card.html",
        {
            "request": request,
            "user_info": user,
            "report_card": report_card,
        },
    )


@router.get("/api/report-card", response_class=JSONResponse)
async def api_report_card(user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        report_card = build_student_report_card(conn, student_id=int(user["id"]))
        conn.commit()
    return {"status": "success", "report_card": report_card}
