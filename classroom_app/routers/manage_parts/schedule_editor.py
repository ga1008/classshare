"""课表编辑模式 API（教师端）。

Local drafts are validated and stored on the platform; "保存到教务" pushes them
into the 教务 调停课申请 *draft* list only. Submitting the application is done
by the teacher inside 教务系统.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ...database import get_db_connection
from ...dependencies import get_current_teacher
from ...services.academic_schedule_draft_push_service import (
    push_drafts_to_academic_system, withdraw_draft_from_academic_system,
)
from ...services.schedule_editor_service import (
    ScheduleEditError, build_editor_payload, delete_draft, get_draft, save_draft, search_rooms,
)
from ...services.smart_classroom_schedule_sync_service import build_teacher_course_schedule_overview
from .common import _parse_json_request

router = APIRouter()
_NO_STORE = {"Cache-Control": "private, no-store"}


def _term(value: str) -> str:
    return str(value or "").strip()


def _load_overview(conn, teacher_id: int, year: str, term: str) -> dict:
    return build_teacher_course_schedule_overview(conn, teacher_id, year=_term(year), term=_term(term))


@router.get("/academic/course-schedule/editor", response_class=JSONResponse)
async def api_schedule_editor_payload(year: str = "", term: str = "", user: dict = Depends(get_current_teacher)):
    """编辑模式数据：整学期周课表 + 本地调课草稿 + 规则。"""
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        payload = build_editor_payload(conn, int(user["id"]), overview)
        conn.commit()
    return JSONResponse({"status": "success", **payload}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/drafts", response_class=JSONResponse)
async def api_schedule_editor_save_draft(request: Request, user: dict = Depends(get_current_teacher)):
    """新建/更新一条调课草稿（按课次 event_key 去重）。"""
    payload = await _parse_json_request(request)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        try:
            draft = save_draft(conn, int(user["id"]), overview, payload)
        except ScheduleEditError as exc:
            conn.rollback()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        conn.commit()
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({"status": "success", "draft": draft, **editor}, headers=_NO_STORE)


@router.delete("/academic/course-schedule/editor/drafts/{draft_id}", response_class=JSONResponse)
async def api_schedule_editor_delete_draft(draft_id: int, year: str = "", term: str = "", user: dict = Depends(get_current_teacher)):
    """撤销一条尚未保存到教务的本地草稿。"""
    with get_db_connection() as conn:
        try:
            removed = delete_draft(conn, int(user["id"]), int(draft_id))
        except ScheduleEditError as exc:
            conn.rollback()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        conn.commit()
        overview = _load_overview(conn, int(user["id"]), year or removed["year"], term or removed["term"])
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({"status": "success", "removed": removed, **editor}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/push", response_class=JSONResponse)
async def api_schedule_editor_push(request: Request, user: dict = Depends(get_current_teacher)):
    """把本地草稿保存到教务调停课申请草稿（不提交申请）。"""
    payload = await _parse_json_request(request)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    if not year or not term:
        raise HTTPException(status_code=400, detail="请先选择学年学期。")
    raw_ids = payload.get("draft_ids") or []
    try:
        draft_ids = [int(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="草稿编号格式错误。") from exc
    result = await push_drafts_to_academic_system(
        int(user["id"]), year=year, term=term, draft_ids=draft_ids or None,
        force=bool(payload.get("force")), force_note=str(payload.get("force_note") or "")[:200],
    )
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({**editor, "status": "success", "result": result}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/drafts/{draft_id}/withdraw", response_class=JSONResponse)
async def api_schedule_editor_withdraw(draft_id: int, user: dict = Depends(get_current_teacher)):
    """从教务草稿中撤回一条已保存的明细（不影响已提交的申请）。"""
    with get_db_connection() as conn:
        draft = get_draft(conn, int(user["id"]), int(draft_id))
    if draft is None:
        raise HTTPException(status_code=404, detail="草稿不存在。")
    result = await withdraw_draft_from_academic_system(int(user["id"]), int(draft_id))
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), draft["year"], draft["term"])
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({**editor, "status": "success", "result": result}, headers=_NO_STORE)


@router.get("/academic/course-schedule/editor/rooms", response_class=JSONResponse)
async def api_schedule_editor_rooms(q: str = "", limit: int = 30, user: dict = Depends(get_current_teacher)):
    """教室候选（来自已同步的教务教学场地，含教务场地 id）。"""
    with get_db_connection() as conn:
        rooms = search_rooms(conn, q, limit=max(1, min(int(limit or 30), 100)))
    return JSONResponse({"status": "success", "rooms": rooms}, headers=_NO_STORE)
