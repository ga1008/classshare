"""Student career-development network routes.

* ``GET  /career-path``                 — the immersive page (intro/test or network).
* ``GET  /api/career-path/state``       — full lifecycle state for the page.
* ``GET  /api/career-path/questions``   — personality-test question bank.
* ``POST /api/career-path/answers``     — submit answers → schedule AI personalization.
* ``POST /api/career-path/reset``       — redo the test (debug / opt-in).

Students only. The deep-thinking AI runs on the unified scheduler, so the page
polls ``/state`` and switches phases (intro → personalizing → ready).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.career_path_service import (
    build_state,
    get_questions,
    resolve_student_context,
    reset_session,
    save_test_and_generate,
)

router = APIRouter()


def _require_student(user: dict) -> int:
    if str(user.get("role")) != "student":
        raise HTTPException(403, "职业发展网络仅对学生开放")
    return int(user["id"])


@router.get("/career-path")
def career_path_page(request: Request, user: dict = Depends(get_current_user)):
    if str(user.get("role")) != "student":
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request,
        "career_path.html",
        {"request": request, "user_info": user},
    )


@router.get("/api/career-path/state", response_class=JSONResponse)
def career_path_state(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        state = build_state(conn, student_id)
        conn.commit()
    if not state.get("ok"):
        raise HTTPException(404, "未找到你的学籍信息")
    return state


@router.get("/api/career-path/questions", response_class=JSONResponse)
def career_path_questions(user: dict = Depends(get_current_user)):
    _require_student(user)
    return {"ok": True, "questions": get_questions()}


@router.post("/api/career-path/answers", response_class=JSONResponse)
async def career_path_answers(request: Request, user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, list) or not answers:
        raise HTTPException(400, "缺少测试作答")
    with get_db_connection() as conn:
        ctx = resolve_student_context(conn, student_id)
        if not ctx:
            raise HTTPException(404, "未找到你的学籍信息")
        result = save_test_and_generate(conn, ctx, answers)
        conn.commit()
    return {"ok": True, **result}


@router.post("/api/career-path/reset", response_class=JSONResponse)
def career_path_reset(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        reset_session(conn, student_id)
        conn.commit()
    return {"ok": True}
