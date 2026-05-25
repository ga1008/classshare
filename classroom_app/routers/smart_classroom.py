from __future__ import annotations

import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services.materials_service import ensure_classroom_access
from ..services.smart_attendance_export_service import build_smart_attendance_export
from ..services.smart_classroom_checkin_sync_service import (
    build_classroom_smart_attendance_analytics,
    load_session_smart_checkin_summary,
    sync_teacher_smart_classroom_checkins,
)


router = APIRouter(prefix="/api/classrooms")


def _ensure_teacher_session_access(conn, class_offering_id: int, session_id: int, user: dict):
    offering = ensure_classroom_access(conn, int(class_offering_id), user)
    session = conn.execute(
        """
        SELECT *
        FROM class_offering_sessions
        WHERE id = ? AND class_offering_id = ?
        LIMIT 1
        """,
        (int(session_id), int(class_offering_id)),
    ).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="课次不存在。")
    return offering, session


@router.get("/{class_offering_id}/sessions/{session_id}/smart-checkin", response_class=JSONResponse)
async def api_get_session_smart_checkin(
    class_offering_id: int,
    session_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_session_access(conn, class_offering_id, session_id, user)
        summary = load_session_smart_checkin_summary(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
            session_id=int(session_id),
        )
    return summary


@router.post("/{class_offering_id}/sessions/{session_id}/smart-checkin/sync", response_class=JSONResponse)
async def api_sync_session_smart_checkin(
    class_offering_id: int,
    session_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_teacher_session_access(conn, class_offering_id, session_id, user)

    sync_result = await sync_teacher_smart_classroom_checkins(
        int(user["id"]),
        class_offering_id=int(class_offering_id),
        session_id=int(session_id),
    )
    with get_db_connection() as conn:
        summary = load_session_smart_checkin_summary(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
            session_id=int(session_id),
        )
    return {
        "status": sync_result.get("status") or summary.get("status") or "unknown",
        "message": sync_result.get("message") or summary.get("message") or "智慧课堂点名同步完成。",
        "sync": sync_result,
        "checkin": summary,
    }


@router.get("/{class_offering_id}/smart-attendance/analytics", response_class=JSONResponse)
async def api_get_classroom_smart_attendance_analytics(
    class_offering_id: int,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, int(class_offering_id), user)
        analytics = build_classroom_smart_attendance_analytics(
            conn,
            class_offering_id=int(class_offering_id),
            viewer_role=str(user.get("role") or ""),
            student_id=int(user["id"]) if str(user.get("role") or "") == "student" else None,
        )
    if analytics.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=analytics.get("message") or "课堂不存在。")
    return analytics


@router.get("/{class_offering_id}/smart-attendance/export")
async def api_export_classroom_smart_attendance(
    class_offering_id: int,
    format: str = "xlsx",
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        ensure_classroom_access(conn, int(class_offering_id), user)
        try:
            export = build_smart_attendance_export(
                conn,
                class_offering_id=int(class_offering_id),
                teacher_id=int(user["id"]),
                file_format=format,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    encoded_filename = quote(export.filename)
    return StreamingResponse(
        io.BytesIO(export.content),
        media_type=export.media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )
