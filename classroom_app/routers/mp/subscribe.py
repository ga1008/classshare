"""小程序订阅消息：模板配置下发 + 授权额度上报。

前端 wx.requestSubscribeMessage 需要模板 ID（从 /config 取，ID 只在
服务端维护）；用户点"允许"后前端把 accept 的 key 列表报到 /report，
服务端给对应额度 +1（一次性订阅制）。发送侧见
services/wechat_mp_subscribe_service。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...db.connection import get_db_connection
from ...services.wechat_mp_subscribe_service import TEMPLATES, record_subscribe_grants
from .deps import get_current_mp_user

router = APIRouter(prefix="/subscribe")


class SubscribeReportPayload(BaseModel):
    accepted: list[str] = []


@router.get("/config")
def mp_subscribe_config(user: dict = Depends(get_current_mp_user)):
    """key → 模板 ID 映射（前端拉起授权弹窗用）。"""
    return {
        "success": True,
        "data": {
            "templates": {
                key: template["template_id"] for key, template in TEMPLATES.items()
            }
        },
        "error": None,
    }


@router.post("/report")
def mp_subscribe_report(
    payload: SubscribeReportPayload, user: dict = Depends(get_current_mp_user)
):
    """上报用户允许的模板 key，额度各 +1。"""
    with get_db_connection() as conn:
        balances = record_subscribe_grants(
            conn,
            user_role=str(user["role"]),
            user_pk=int(user["id"]),
            template_keys=payload.accepted,
        )
        conn.commit()
    return {"success": True, "data": {"balances": balances}, "error": None}
