"""Teacher timetable lookup stays within an authenticated teacher's school."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...database import get_db_connection
from ...dependencies import require_teacher_domain
from ...services.academic_teacher_timetable_service import (
    TimetableQueryError, build_teacher_timetable_options, load_teacher_timetable, query_teacher_timetables,
    sync_teacher_timetable,
)

router = APIRouter()
_AUTH = require_teacher_domain("academic")
_HEADERS = {"Cache-Control": "private, no-store"}


def _failure(exc):
    return JSONResponse({"status": exc.status, "message": str(exc), "detail": str(exc)},
                        status_code=exc.http_status, headers=_HEADERS)


@router.get("/academic/teachers/options")
async def teacher_timetable_options(user=Depends(_AUTH)):
    try:
        with get_db_connection() as conn:
            result = build_teacher_timetable_options(conn, int(user["id"]))
        return JSONResponse(result, headers=_HEADERS)
    except TimetableQueryError as exc:
        return _failure(exc)


async def _query(request, user, force_sync):
    try:
        try:
            payload = await request.json()
        except ValueError:
            raise TimetableQueryError("查询条件格式无效。") from None
        result = await query_teacher_timetables(int(user["id"]), payload, force_sync=force_sync)
        return JSONResponse(result, headers=_HEADERS)
    except TimetableQueryError as exc:
        return _failure(exc)


@router.post("/academic/teachers/query")
async def teacher_timetable_query(request: Request, user=Depends(_AUTH)):
    return await _query(request, user, False)


@router.post("/academic/teachers/sync")
async def teacher_timetable_sync(request: Request, user=Depends(_AUTH)):
    return await _query(request, user, True)


@router.post("/academic/teachers/{teacher_no:path}/sync")
async def teacher_timetable_single_sync(teacher_no: str, request: Request, user=Depends(_AUTH)):
    try:
        try:
            payload = await request.json()
        except ValueError:
            raise TimetableQueryError("查询条件格式无效。") from None
        result = await sync_teacher_timetable(int(user["id"]), teacher_no, payload)
        return JSONResponse(result, headers=_HEADERS)
    except TimetableQueryError as exc:
        return _failure(exc)


@router.get("/academic/teachers/{teacher_no:path}/timetable")
async def teacher_timetable_detail(teacher_no: str, year: str, term: str, user=Depends(_AUTH)):
    try:
        with get_db_connection() as conn:
            result = load_teacher_timetable(conn, int(user["id"]), teacher_no, year=year, term=term)
        return JSONResponse(result, headers=_HEADERS)
    except TimetableQueryError as exc:
        return _failure(exc)
