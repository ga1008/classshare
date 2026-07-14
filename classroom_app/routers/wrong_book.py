"""学生个人错题本页面与数据接口。

- ``GET /wrong-book``：错题本页面（仅学生本人）。
- ``GET /api/wrong-book``：同源 JSON，供前端刷新/后续 AI 重练扩展。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.student_wrong_book_service import build_student_wrong_book

router = APIRouter()


def _ensure_student(user: dict) -> None:
    if str(user.get("role") or "").strip().lower() != "student":
        raise HTTPException(status_code=403, detail="错题本仅面向学生本人开放。")


@router.get("/wrong-book", response_class=HTMLResponse)
async def wrong_book_page(request: Request, user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        wrong_book = build_student_wrong_book(conn, student_id=int(user["id"]))
        conn.commit()
    return templates.TemplateResponse(
        request,
        "wrong_book.html",
        {
            "request": request,
            "user_info": user,
            "wrong_book": wrong_book,
        },
    )


@router.get("/api/wrong-book", response_class=JSONResponse)
async def api_wrong_book(user: dict = Depends(get_current_user)):
    _ensure_student(user)
    with get_db_connection() as conn:
        wrong_book = build_student_wrong_book(conn, student_id=int(user["id"]))
        conn.commit()
    return {"status": "success", "wrong_book": wrong_book}
