from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.behavior_tracking_service import record_behavior_batch
from ..services.behavior_inbox_service import BehaviorBatchConflict
from ..services.behavior_event_validation import BehaviorValidationError, BehaviorPayloadTooLarge
from ..services.resource_access_service import ensure_classroom_access

router = APIRouter(prefix="/api/classrooms", tags=["behavior"])


class BehaviorEventItem(BaseModel):
    action_type: str = Field(default="page_action", max_length=64)
    summary_text: str = Field(default="", max_length=300)
    page_key: Optional[str] = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class BehaviorBatchRequest(BaseModel):
    batch_id: Optional[str] = Field(default=None, min_length=16, max_length=96, pattern=r"^b1_[0-9]{10,16}_[A-Za-z0-9_-]+$")
    user_key: Optional[str] = Field(default=None, max_length=40)
    page_key: Optional[str] = Field(default=None, max_length=64)
    events: list[BehaviorEventItem] = Field(default_factory=list, max_length=20)


def _ensure_behavior_access(conn, class_offering_id: int, user_pk: int, user_role: str) -> None:
    ensure_classroom_access(conn, class_offering_id, {"id": user_pk, "role": user_role})


@router.post("/{class_offering_id}/behavior/batch")
def ingest_behavior_batch(
    class_offering_id: int,
    body: BehaviorBatchRequest,
    response: Response,
    user: dict = Depends(get_current_user),
):
    user_pk = int(user.get("id") or 0)
    user_role = str(user.get("role") or "")
    if not user_pk or not user_role:
        raise HTTPException(status_code=401, detail="无效的用户身份")
    if body.user_key and body.user_key != f"{user_role}:{user_pk}":
        raise HTTPException(status_code=409, detail="账号已更换，当前行为批次属于此前账号。")
    if not body.events:
        return {"status": "success", "accepted_event_count": 0}

    with get_db_connection() as conn:
        _ensure_behavior_access(conn, class_offering_id, user_pk, user_role)

    try:
        snapshot = record_behavior_batch(
            class_offering_id=class_offering_id,
            user_pk=user_pk,
            user_role=user_role,
            display_name=str(user.get("name") or user.get("username") or f"{user_role}:{user_pk}"),
            page_key=body.page_key,
            events=[item.model_dump() for item in body.events],
            session_started_at=str(user.get("login_time") or "").strip() or None,
            wait=False,
            batch_id=body.batch_id,
            require_durable=True,
        )
    except BehaviorPayloadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except BehaviorValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BehaviorBatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    terminal_error = snapshot.get("write_mode") in {"quarantined", "discarded"}
    response.status_code = 409 if terminal_error else (202 if snapshot.get("write_mode") == "durable_queued" else 200)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "error" if terminal_error else "success", **snapshot}
