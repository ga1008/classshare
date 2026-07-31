"""Teacher-only APIs for locally mirrored academic teaching evaluations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.academic_evaluation_sync_service import (
    build_teacher_academic_evaluation_dashboard_context,
    get_teacher_classroom_academic_evaluation_detail,
    sync_current_teacher_academic_evaluations,
)


router = APIRouter(prefix="/api/academic-evaluations", tags=["academic-evaluations"])


def _teacher_offerings(conn, teacher_id: int) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT o.id, o.semester_id, c.name AS course_name
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            WHERE o.teacher_id = ?
            ORDER BY o.id DESC
            """,
            (int(teacher_id),),
        ).fetchall()
    ]


@router.post("/sync-current")
async def sync_current_academic_evaluations(
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    try:
        body = await request.json()
    except Exception:  # Empty bodies are valid for the automatic low-frequency sync.
        body = {}
    force = bool(body.get("force")) if isinstance(body, dict) else False
    result = await sync_current_teacher_academic_evaluations(
        int(user["id"]),
        force=force,
    )
    with get_db_connection() as conn:
        offerings = _teacher_offerings(conn, int(user["id"]))
        overviews, sync_state = build_teacher_academic_evaluation_dashboard_context(
            conn,
            teacher_id=int(user["id"]),
            offerings=offerings,
        )
    return {
        **result,
        "sync": sync_state,
        "offerings": {str(key): value for key, value in overviews.items()},
    }


@router.get("/classrooms/{class_offering_id}")
def get_classroom_academic_evaluation(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        detail = get_teacher_classroom_academic_evaluation_detail(
            conn,
            teacher_id=int(user["id"]),
            class_offering_id=int(class_offering_id),
        )
    if detail is None:
        raise HTTPException(status_code=404, detail="课堂不存在或你无权查看该评价。")
    return detail


__all__ = ["router"]
