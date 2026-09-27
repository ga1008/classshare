"""小程序订阅消息：模板配置下发 + 授权额度上报。

前端 wx.requestSubscribeMessage 需要模板 ID（从 /config 取，ID 只在
服务端维护）；用户点"允许"后前端把 accept 的 key 列表报到 /report，
服务端给对应额度 +1（一次性订阅制）。发送侧见
services/wechat_mp_subscribe_service。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...db.connection import get_db_connection
from ...services.wechat_mp_subscribe_dispatch_service import (
    claim_subscribe_report,
    load_subscribe_balances,
)
from ...services.wechat_mp_subscribe_service import TEMPLATES, record_subscribe_grants
from .deps import get_current_mp_user

router = APIRouter(prefix="/subscribe")


class SubscribeReportPayload(BaseModel):
    accepted: list[str] = []
    # 客户端生成的一次性 ID：同一次授权的重试/重复上报只记一次额度。
    report_id: str = Field(default="", max_length=64)


@router.get("/config")
def mp_subscribe_config(user: dict = Depends(get_current_mp_user)):
    """key → 模板 ID 映射（前端拉起授权弹窗用）+ 本人各模板剩余额度。"""
    with get_db_connection() as conn:
        balances = load_subscribe_balances(conn, user_role=str(user["role"]), user_pk=int(user["id"]))
        conn.commit()
    return {
        "success": True,
        "data": {
            "templates": {
                key: template["template_id"] for key, template in TEMPLATES.items()
            },
            "balances": balances,
        },
        "error": None,
    }


@router.post("/report")
def mp_subscribe_report(
    payload: SubscribeReportPayload, user: dict = Depends(get_current_mp_user)
):
    """上报用户允许的模板 key，额度各 +1（带 report_id 时幂等）。返回全部余额。"""
    role, user_pk = str(user["role"]), int(user["id"])
    report_id = payload.report_id.strip()
    with get_db_connection() as conn:
        counted = True
        if report_id:
            counted = claim_subscribe_report(
                conn, report_id=f"{role}:{user_pk}:{report_id}", user_role=role, user_pk=user_pk
            )
        if counted:
            record_subscribe_grants(conn, user_role=role, user_pk=user_pk, template_keys=payload.accepted)
        balances = load_subscribe_balances(conn, user_role=role, user_pk=user_pk)
        conn.commit()
    return {"success": True, "data": {"balances": balances, "counted": counted}, "error": None}
