"""Student-owned career commands and read-only state endpoints."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import career_path_service as career
from ..services.career_engagement_service import record_student_career_event, record_student_career_event_safely
from ..services.student_career_job_service import CareerJobCapacityError
from ..services.career_rollout_service import CareerRolloutLimited
from ..services import career_job_posting_service as postings

router = APIRouter()


class QuizInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[dict[str, Any]] = Field(max_length=12)
    mode: Literal["quick", "full"] = "quick"
    quiz_version: str = Field(default=career.QUIZ_VERSION, max_length=40)
    revision: StrictInt = Field(ge=0)
    enhance: bool = False


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["network", "personalization"]
    job_id: StrictInt | None = None
    revision: StrictInt | None = Field(default=None, ge=0)


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    career_tag: str = Field(min_length=1,max_length=64,pattern=career.SAFE_ID_RE.pattern)
    action: Literal["favorite","hide","restore"]
    revision: StrictInt = Field(ge=0)


def _require_student(user: dict) -> int:
    if str(user.get("role")) != "student":
        raise HTTPException(403, "职业发展网络仅对学生开放")
    return int(user["id"])


def _command(fn, student_id, **kwargs):
    try:
        with get_db_connection() as conn:
            result = fn(conn, student_id, **kwargs)
            conn.commit()
            return result
    except career.CareerConflict as exc:
        raise HTTPException(409, exc.detail) from exc
    except CareerRolloutLimited as exc:
        raise HTTPException(403, exc.detail) from exc
    except CareerJobCapacityError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/career-path")
def career_path_page(request: Request, user: dict = Depends(get_current_user)):
    if str(user.get("role")) != "student":
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "career_path.html", {"request": request, "user_info": user})


@router.get("/api/career-path/state")
def career_path_state(response: Response, known_result_version: str = Query(default="",max_length=80), user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        state = career.build_state(conn, _require_student(user), known_result_version=known_result_version)
    if not state.get("ok"):
        raise HTTPException(404, "未找到你的学籍信息")
    response.headers["Cache-Control"] = "private, no-store"
    return state


@router.post("/api/career-path/initialize")
def career_path_initialize(user: dict = Depends(get_current_user)):
    return _command(career.initialize_career, _require_student(user))


@router.get("/api/career-path/questions")
def career_path_questions(mode: Literal["quick", "full"] = "quick", user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    with get_db_connection() as conn:
        ctx = career.resolve_student_context(conn, student_id) or {}
    return {"ok": True, "mode": mode, "quiz_version": career.QUIZ_VERSION,
            "estimated_minutes": 3 if mode == "full" else 1,
            "questions": career.get_questions(mode=mode, major_key=ctx.get("major_key") or "")}


def _save_answers(conn, student_id, *, payload, complete):
    ctx = career.resolve_student_context(conn, student_id)
    if not ctx:
        raise ValueError("未找到你的学籍信息")
    args = payload.model_dump()
    if complete:
        result = career.save_test_and_generate(conn, ctx, **args)
        record_student_career_event_safely(conn, student_id, surface="career", event_name="career_quiz_completed",
                                           context={"result_count": len(payload.answers)})
    else:
        args.pop("enhance", None)
        result = career.save_test_progress(conn, ctx, **args)
    return {"ok": True, **result}


@router.post("/api/career-path/answers")
def career_path_answers(payload: QuizInput, user: dict = Depends(get_current_user)):
    return _command(_save_answers, _require_student(user), payload=payload, complete=True)


@router.post("/api/career-path/progress")
def career_path_progress(payload: QuizInput, user: dict = Depends(get_current_user)):
    return _command(_save_answers, _require_student(user), payload=payload, complete=False)


@router.post("/api/career-path/retry")
def career_path_retry(payload: CommandInput, user: dict = Depends(get_current_user)):
    return _command(career.career_job_command, _require_student(user), action="retry", **payload.model_dump())


@router.post("/api/career-path/cancel")
def career_path_cancel(payload: CommandInput, user: dict = Depends(get_current_user)):
    return _command(career.career_job_command, _require_student(user), action="cancel", **payload.model_dump())


@router.post("/api/career-path/reset")
def career_path_reset(payload: dict[str, Any] = Body(default={}), user: dict = Depends(get_current_user)):
    def reset(conn, student_id):
        career.reset_session(conn, student_id, revision=payload.get("revision"))
        return career.build_state(conn, student_id)
    return _command(reset, _require_student(user))


@router.post("/api/career-path/preferences")
def career_path_preferences(payload: dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    values = dict(payload)
    revision = values.pop("revision", None)
    if revision is None:
        raise HTTPException(400, "请提供资料版本")
    return _command(career.update_career_preferences, _require_student(user), payload=values, revision=revision)


@router.post("/api/career-path/feedback")
def career_path_feedback(payload: FeedbackInput, user: dict = Depends(get_current_user)):
    def feedback(conn, student_id):
        ctx = career.resolve_student_context(conn, student_id)
        if not ctx:
            raise ValueError("未找到学籍信息")
        graph = career.get_or_prepare_network(conn, ctx)["network"]
        node = next((node for node in graph["nodes"] if node["tag"] == payload.career_tag), None)
        if not node:
            raise ValueError("未找到该职业方向")
        return career.record_career_feedback(conn, student_id, node.get("direction_id") or node["tag"],
            {"favorite": "saved", "hide": "dismissed", "restore": "clear"}[payload.action],
            revision=payload.revision)
    return _command(feedback, _require_student(user))


@router.post("/api/career-path/keywords")
def career_path_keywords(payload: dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    student_id = _require_student(user)
    tag = str(payload.get("tag") or "")
    if not career.SAFE_ID_RE.fullmatch(tag):
        raise HTTPException(400, "职业方向标识不正确")
    with get_db_connection() as conn:
        ctx = career.resolve_student_context(conn, student_id)
        if not ctx:
            raise HTTPException(404, "未找到学籍信息")
        graph = career.get_or_prepare_network(conn, ctx)["network"]
    node = next((n for n in graph["nodes"] if n["tag"] == tag), None)
    if not node:
        raise HTTPException(404, "未找到该职业方向")
    return {"ok": True, "tag": tag, "keywords": career.derive_job_keywords_from_node(node), "source": "baseline"}


@router.post("/api/career-tools/events")
def career_tools_event(payload: dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    def record(conn, student_id):
        inserted = record_student_career_event(conn, student_id, surface=str(payload.get("surface") or ""),
            event_name=str(payload.get("event_name") or ""), context=payload.get("context"),
            client_event_id=str(payload.get("client_event_id") or ""))
        return {"ok": True, "inserted": inserted}
    return _command(record, _require_student(user))


@router.get("/api/career-path/job-postings")
def career_job_postings(response: Response, city: str = Query(default="",max_length=80),
                        keyword: str = Query(default="",max_length=80),
                        page: int = Query(default=1,ge=1,le=10000),
                        page_size: int = Query(default=20,ge=1,le=20),
                        qualification: Literal["all","no_known_gaps","confirmed"] = "all",
                        user: dict = Depends(get_current_user)):
    try:
        with get_db_connection() as conn:
            result=postings.list_job_postings(conn,_require_student(user),city=city,keyword=keyword,page=page,
                                             page_size=page_size,qualification=qualification)
    except LookupError as exc:
        raise HTTPException(404,str(exc)) from exc
    response.headers["Cache-Control"]="private, no-store"
    return result


@router.post("/api/career-path/job-postings/{posting_id}/target")
def career_posting_target(posting_id: int, user: dict = Depends(get_current_user)):
    return _command(postings.create_posting_target,_require_student(user),posting_id=posting_id)
