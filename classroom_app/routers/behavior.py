from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.behavior_tracking_service import record_behavior_batch

router = APIRouter(prefix="/api/classrooms", tags=["behavior"])


class BehaviorEventItem(BaseModel):
    action_type: str = Field(default="page_action", max_length=64)
    summary_text: str = Field(default="", max_length=300)
    page_key: Optional[str] = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class BehaviorBatchRequest(BaseModel):
    page_key: Optional[str] = Field(default=None, max_length=64)
    events: list[BehaviorEventItem] = Field(default_factory=list, max_length=20)


def _ensure_behavior_access(conn, class_offering_id: int, user_pk: int, user_role: str) -> None:
    if user_role == "teacher":
        offering = conn.execute(
            """
            SELECT id
            FROM class_offerings
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (class_offering_id, user_pk),
        ).fetchone()
    else:
        offering = conn.execute(
            """
            SELECT o.id
            FROM class_offerings o
            JOIN students s ON s.class_id = o.class_id
            WHERE o.id = ? AND s.id = ?
            LIMIT 1
            """,
            (class_offering_id, user_pk),
        ).fetchone()

    if not offering:
        raise HTTPException(status_code=403, detail="无权访问该课堂")


@router.post("/{class_offering_id}/behavior/batch")
async def ingest_behavior_batch(
    class_offering_id: int,
    body: BehaviorBatchRequest,
    user: dict = Depends(get_current_user),
):
    user_pk = int(user.get("id") or 0)
    user_role = str(user.get("role") or "")
    if not user_pk or not user_role:
        raise HTTPException(status_code=401, detail="无效的用户身份")
    if not body.events:
        return {"status": "success", "accepted_event_count": 0}

    with get_db_connection() as conn:
        _ensure_behavior_access(conn, class_offering_id, user_pk, user_role)

    snapshot = record_behavior_batch(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        display_name=str(user.get("name") or user.get("username") or f"{user_role}:{user_pk}"),
        page_key=body.page_key,
        events=[item.model_dump() for item in body.events],
        session_started_at=str(user.get("login_time") or "").strip() or None,
    )
    return {"status": "success", **snapshot}
