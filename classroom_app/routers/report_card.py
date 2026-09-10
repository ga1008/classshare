"""学生个人成绩单页面与数据接口。

- ``GET /report-card``：成绩单页面（仅学生本人）。
- ``GET /api/report-card``：同源 JSON。
"""

from __future__ import annotations
from typing import Literal

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
async def report_card_page(request: Request, user: dict = Depends(get_current_user),
                           assessment_kind: Literal["homework", "midterm", "final"] | None = None,
                           class_offering_id: int | None = None):
    _ensure_student(user)
    with get_db_connection() as conn:
        report_card = build_student_report_card(conn, student_id=int(user["id"]),
            assessment_kind=assessment_kind, class_offering_id=class_offering_id)
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
def api_report_card(user: dict = Depends(get_current_user),
                          assessment_kind: Literal["homework", "midterm", "final"] | None = None,
                          class_offering_id: int | None = None):
    _ensure_student(user)
    with get_db_connection() as conn:
        report_card = build_student_report_card(conn, student_id=int(user["id"]),
            assessment_kind=assessment_kind, class_offering_id=class_offering_id)
    return {"status": "success", "report_card": report_card}
