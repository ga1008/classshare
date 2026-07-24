"""小程序端"人生一言"接口：欢迎屏反馈投票。

复用 Web 端同一份 ``record_tip_feedback``（每人每句一票可改票、
weight 回写加权采样），仅认证方式换成 mp bearer token。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ...db.connection import get_db_connection
from ...services.life_tip_service import record_tip_feedback
from .deps import get_current_mp_user

router = APIRouter(prefix="/life-tips")


class MpTipFeedbackRequest(BaseModel):
    tip_id: int
    verdict: int  # 1 有用 / -1 无感


@router.post("/feedback")
def mp_tip_feedback(
    request: Request,
    payload: MpTipFeedbackRequest,
    user: dict = Depends(get_current_mp_user),
):
    try:
        with get_db_connection() as conn:
            result = record_tip_feedback(
                conn,
                tip_id=payload.tip_id,
                user_role=str(user.get("role") or "student"),
                user_pk=int(user["id"]),
                verdict=payload.verdict,
            )
            conn.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": result, "error": None}
