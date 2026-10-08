"""课表编辑模式 API（教师端）。

Local drafts are validated and stored on the platform; "保存到教务" pushes them
into the 教务 调停课申请 *draft* list only. Submitting the application is done
by the teacher inside 教务系统.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ...database import get_db_connection
from ...dependencies import get_current_teacher
from ...services.academic_schedule_draft_push_service import (
    check_drafts_conflicts, push_drafts_to_academic_system, withdraw_draft_from_academic_system,
)
from ...services.academic_availability_sync_service import search_free_rooms, sync_availability_for_term
from ...services.schedule_availability_service import build_lesson_availability
from ...services.national_holiday_service import load_national_holiday_status, refresh_national_holidays
from ...services.schedule_editor_service import (
    ScheduleEditError, add_draft_proof, apply_resequence_by_dates, build_editor_payload, build_term_calendar,
    calendar_day_info, delete_draft, find_draft_proof, get_draft, plan_resequence_for_drafts, remove_draft_proof,
    save_draft, search_rooms, slot_date, _lesson_index, _term_context,
)
from ...services.schedule_reason_service import suggest_reason
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


@router.get("/academic/course-schedule/editor/availability", response_class=JSONResponse)
async def api_schedule_editor_availability(year: str = "", term: str = "", event_key: str = "", room_id: str = "",
                                           room: str = "", user: dict = Depends(get_current_teacher)):
    """某课次的可调时段：学生有课 / 本人有课 / 教室占用 的紧凑忙碌表。"""
    if not event_key.strip():
        raise HTTPException(status_code=400, detail="缺少课次标识。")
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        availability = build_lesson_availability(conn, int(user["id"]), overview, event_key.strip(),
                                                 room_id=_term(room_id), room_name=_term(room))
    return JSONResponse({"status": "success", "availability": availability}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/availability/sync", response_class=JSONResponse)
async def api_schedule_editor_availability_sync(request: Request, user: dict = Depends(get_current_teacher)):
    """从教务拉取本学期学生（行政班）课表与教室课表，刷新可调时段缓存。"""
    payload = await _parse_json_request(request)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    if not year or not term:
        raise HTTPException(status_code=400, detail="请先选择学年学期。")
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
    result = await sync_availability_for_term(int(user["id"]), year=year, term=term, overview=overview)
    with get_db_connection() as conn:
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({**editor, "status": "success", "result": result}, headers=_NO_STORE)


@router.get("/academic/course-schedule/editor/free-rooms", response_class=JSONResponse)
async def api_schedule_editor_free_rooms(year: str = "", term: str = "", week: int = 0, weekday: int = 0, sections: str = "",
                                         q: str = "", room_id: str = "", room: str = "", building: str = "",
                                         room_type: str = "", user: dict = Depends(get_current_teacher)):
    """二次搜索：目标时段的空闲教室（实时查教务），并记录原教室在该时段的占用结论。"""
    try:
        section_list = sorted({int(part) for part in sections.split(",") if part.strip()})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="节次格式错误。") from exc
    if not year or not term or week < 1 or not 1 <= weekday <= 7 or not section_list:
        raise HTTPException(status_code=400, detail="请提供学年学期、周次、星期和节次。")
    result = await search_free_rooms(int(user["id"]), year=_term(year), term=_term(term), week=week, weekday=weekday,
                                     sections=section_list, keyword=_term(q), room_id=_term(room_id), room_name=_term(room),
                                     building=_term(building), room_type=_term(room_type))
    return JSONResponse({"status": "success", "result": result}, headers=_NO_STORE)


@router.get("/academic/course-schedule/editor/resequence-preview", response_class=JSONResponse)
async def api_schedule_editor_resequence_preview(year: str = "", term: str = "", draft_ids: str = "",
                                                 user: dict = Depends(get_current_teacher)):
    """课次重排预览：把当前草稿当作已生效的调整，展示剩余课次的新顺序（不写库）。"""
    try:
        ids = [int(part) for part in draft_ids.split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="草稿编号格式错误。") from exc
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        preview = plan_resequence_for_drafts(conn, int(user["id"]), overview, draft_ids=ids or None)
    return JSONResponse({"status": "success", **preview}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/resequence/apply", response_class=JSONResponse)
async def api_schedule_editor_resequence_apply(request: Request, user: dict = Depends(get_current_teacher)):
    """按实际日期节次连续编号，保持课次身份，教学材料随序号整体重绑。"""
    payload = await _parse_json_request(request)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    offering_id = payload.get("offering_id")
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        try:
            result = apply_resequence_by_dates(conn, int(user["id"]), overview,
                                              offering_id=int(offering_id) if offering_id else None)
        except ScheduleEditError as exc:
            conn.rollback()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        conn.commit()
        overview = _load_overview(conn, int(user["id"]), year, term)
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({**editor, "status": "success", "result": result}, headers=_NO_STORE)


@router.get("/academic/course-schedule/editor/holidays/status", response_class=JSONResponse)
async def api_schedule_editor_holiday_status(user: dict = Depends(get_current_teacher)):
    """全国节假日/调休自动获取状态（按年份的记录数与最近获取时间）。"""
    with get_db_connection() as conn:
        status = load_national_holiday_status(conn)
    return JSONResponse({"status": "success", "holidays": status}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/holidays/refresh", response_class=JSONResponse)
async def api_schedule_editor_holiday_refresh(request: Request, user: dict = Depends(get_current_teacher)):
    """立即刷新全国节假日/调休数据（默认今年与明年），并返回刷新后的编辑载荷。"""
    import asyncio

    payload = await _parse_json_request(request)
    raw_years = payload.get("years") or []
    try:
        years = [int(y) for y in raw_years] if isinstance(raw_years, list) else []
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="年份格式错误。") from exc
    summary = await asyncio.to_thread(refresh_national_holidays, years or None)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        editor = build_editor_payload(conn, int(user["id"]), overview)
        status = load_national_holiday_status(conn)
    return JSONResponse({**editor, "status": "success", "result": summary, "holidays": status}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/reason-suggest", response_class=JSONResponse)
async def api_schedule_editor_reason_suggest(request: Request, user: dict = Depends(get_current_teacher)):
    """快速 AI 根据原安排/拟安排与校历（节假日、调休）写一句简短的调课原因；AI 不可用时给规则兜底。"""
    payload = await _parse_json_request(request)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    event_key = _term(payload.get("event_key"))
    if not event_key:
        raise HTTPException(status_code=400, detail="缺少课次标识。")
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        context = _term_context(overview)
        calendar = build_term_calendar(conn, context)
    lesson = _lesson_index(overview).get(event_key)
    if lesson is None:
        raise HTTPException(status_code=404, detail="未找到要调整的课次。")
    original = {"week": int(lesson.get("week_index") or 0), "weekday": int(lesson.get("weekday") or 0),
                "sections": [int(s) for s in lesson.get("sections") or []], "date": str(lesson.get("actual_date") or ""),
                "room": str(lesson.get("classroom") or "")}
    try:
        week, weekday = int(payload.get("week") or original["week"]), int(payload.get("weekday") or original["weekday"])
        sections = [int(s) for s in (payload.get("sections") or [])] or original["sections"]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="目标时间格式错误。") from exc
    proposed = {"week": week, "weekday": weekday, "sections": sections, "date": slot_date(context["week1_monday"], week, weekday),
                "room": _term(payload.get("room")) or original["room"]}
    result = await suggest_reason({
        "course_name": lesson.get("course_name"), "class_label": lesson.get("class_label") or lesson.get("teaching_class_name"),
        "original": original, "proposed": proposed, "note": _term(payload.get("note"))[:200],
        "original_day": calendar_day_info(calendar, original["date"]), "proposed_day": calendar_day_info(calendar, proposed["date"]),
    })
    return JSONResponse({"status": "success", **result}, headers=_NO_STORE)


@router.post("/academic/course-schedule/editor/drafts/{draft_id}/proofs", response_class=JSONResponse)
async def api_schedule_editor_upload_proof(draft_id: int, files: list[UploadFile] = File(...), user: dict = Depends(get_current_teacher)):
    """为一条调课草稿附加证明材料（放假通知、会议通知等），提交教务时提醒一并上传。"""
    stored = []
    with get_db_connection() as conn:
        for upload in files:
            content = await upload.read()
            try:
                stored.append(add_draft_proof(conn, int(user["id"]), int(draft_id), filename=str(upload.filename or ""), content=content))
            except ScheduleEditError as exc:
                conn.rollback()
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        conn.commit()
        draft = get_draft(conn, int(user["id"]), int(draft_id))
    return JSONResponse({"status": "success", "stored": stored, "draft": draft}, headers=_NO_STORE)


@router.delete("/academic/course-schedule/editor/drafts/{draft_id}/proofs/{file_id}", response_class=JSONResponse)
async def api_schedule_editor_delete_proof(draft_id: int, file_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            removed = remove_draft_proof(conn, int(user["id"]), int(draft_id), file_id)
        except ScheduleEditError as exc:
            conn.rollback()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        conn.commit()
        draft = get_draft(conn, int(user["id"]), int(draft_id))
    if removed is None:
        raise HTTPException(status_code=404, detail="证明材料不存在。")
    return JSONResponse({"status": "success", "removed": removed, "draft": draft}, headers=_NO_STORE)


@router.get("/academic/course-schedule/editor/drafts/{draft_id}/proofs/{file_id}")
async def api_schedule_editor_download_proof(draft_id: int, file_id: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            record, path = find_draft_proof(conn, int(user["id"]), int(draft_id), file_id)
        except ScheduleEditError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return FileResponse(str(path), filename=str(record.get("name") or path.name), headers={"Cache-Control": "private, no-store"})


@router.post("/academic/course-schedule/editor/push/check", response_class=JSONResponse)
async def api_schedule_editor_push_check(request: Request, user: dict = Depends(get_current_teacher)):
    """提前预测：用教务自身的冲突检测试跑待保存的草稿（不保存），结果记在草稿上供卡片/抽屉展示。"""
    payload = await _parse_json_request(request)
    year, term = _term(payload.get("year")), _term(payload.get("term"))
    if not year or not term:
        raise HTTPException(status_code=400, detail="请先选择学年学期。")
    raw_ids = payload.get("draft_ids") or []
    try:
        draft_ids = [int(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="草稿编号格式错误。") from exc
    result = await check_drafts_conflicts(int(user["id"]), year=year, term=term, draft_ids=draft_ids or None)
    with get_db_connection() as conn:
        overview = _load_overview(conn, int(user["id"]), year, term)
        editor = build_editor_payload(conn, int(user["id"]), overview)
    return JSONResponse({**editor, "status": "success", "result": result}, headers=_NO_STORE)
