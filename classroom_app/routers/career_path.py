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
from ..services.career_engagement_service import (
    record_student_career_event,
    record_student_career_event_safely,
)
from ..services.career_path_service import (
    build_state,
    generate_keywords_on_demand,
    get_questions,
    resolve_student_context,
    reset_session,
    save_test_and_generate,
    save_test_progress,
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
def career_path_questions(mode: str = "quick", user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    selected_mode = "full" if str(mode or "").strip().lower() == "full" else "quick"
    with get_db_connection() as conn:
        ctx = resolve_student_context(conn, student_id) or {}
    questions = get_questions(mode=selected_mode, major_key=str(ctx.get("major_key") or ""))
    return {
        "ok": True,
        "mode": selected_mode,
        "estimated_minutes": 3 if selected_mode == "full" else 1,
        "questions": questions,
    }


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
        record_student_career_event_safely(
            conn,
            student_id,
            surface="career",
            event_name="career_quiz_completed",
            context={"result_count": len(answers), "location_pref": result.get("test_result", {}).get("location_pref", "")},
        )
        conn.commit()
    return {"ok": True, **result}


@router.post("/api/career-path/progress", response_class=JSONResponse)
async def career_path_progress(request: Request, user: dict = Depends(get_current_user)):
    """Persist partial answers per-question so the test can resume after exit."""
    student_id = _require_student(user)
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, list):
        raise HTTPException(400, "缺少作答")
    with get_db_connection() as conn:
        ctx = resolve_student_context(conn, student_id)
        if not ctx:
            raise HTTPException(404, "未找到你的学籍信息")
        result = save_test_progress(conn, ctx, answers)
        conn.commit()
    return {"ok": True, **result}


@router.post("/api/career-path/keywords", response_class=JSONResponse)
async def career_path_keywords(request: Request, user: dict = Depends(get_current_user)):
    """On-demand search keywords for a single direction (fast AI + fallback)."""
    student_id = _require_student(user)
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    tag = str(payload.get("tag") or "").strip() if isinstance(payload, dict) else ""
    if not tag:
        raise HTTPException(400, "缺少岗位标识")
    result = await generate_keywords_on_demand(student_id, tag)
    if not result.get("ok"):
        raise HTTPException(404, "未找到该职业方向")
    return result


@router.post("/api/career-path/reset", response_class=JSONResponse)
def career_path_reset(user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        reset_session(conn, student_id)
        conn.commit()
    return {"ok": True}


@router.post("/api/career-tools/events", response_class=JSONResponse)
async def career_tools_event(request: Request, user: dict = Depends(get_current_user)):
    """Accept one privacy-minimal funnel event from career/resume pages."""
    student_id = _require_student(user)
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    payload = payload if isinstance(payload, dict) else {}
    try:
        with get_db_connection() as conn:
            inserted = record_student_career_event(
                conn,
                student_id,
                surface=str(payload.get("surface") or ""),
                event_name=str(payload.get("event_name") or ""),
                context=payload.get("context"),
                client_event_id=str(payload.get("client_event_id") or ""),
            )
            conn.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "inserted": inserted}
