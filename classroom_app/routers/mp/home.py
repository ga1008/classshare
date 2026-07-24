"""小程序首页聚合：角色感知的"今天"数据（议程 + 统计 + 聚焦）。

直接复用 Web 仪表盘的 context builder（单一真源），只投影小程序
首页需要的小子集——与 Web dashboard 单次加载同成本。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...db.connection import get_db_connection
from ...services.dashboard_service import build_dashboard_context
from .deps import get_current_mp_user

router = APIRouter(prefix="/home")

AGENDA_LIMIT = 40


@router.get("")
def mp_home(user: dict = Depends(get_current_mp_user)):
    with get_db_connection() as conn:
        context = build_dashboard_context(conn, user)
        conn.commit()
    return {
        "success": True,
        "data": {
            "role": user["role"],
            "user": {"id": user["id"], "name": user["name"]},
            "stats": context.get("dashboard_stats") or [],
            "focus": context.get("dashboard_focus") or {},
            "agenda": (context.get("dashboard_agenda_events") or [])[:AGENDA_LIMIT],
        },
        "error": None,
    }
