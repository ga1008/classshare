"""小程序首页聚合：角色感知的"今天"数据（议程 + 统计 + 聚焦）。

直接复用 Web 仪表盘的 context builder（单一真源），只投影小程序
首页需要的小子集——与 Web dashboard 单次加载同成本。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...db.connection import get_db_connection
from ...services.dashboard_service import build_dashboard_context
from .deps import get_current_mp_user
from .tasks import load_student_task_buckets

router = APIRouter(prefix="/home")

AGENDA_LIMIT = 40


@router.get("")
def mp_home(user: dict = Depends(get_current_mp_user)):
    with get_db_connection() as conn:
        context = build_dashboard_context(conn, user)
        stats = list(context.get("dashboard_stats") or [])
        if user["role"] == "student":
            # 待完成/已提交与"作业考试"列表共用同一数据源，数字必须一致。
            try:
                buckets = load_student_task_buckets(conn, int(user["id"]))
                overrides = {
                    "待完成": len(buckets["pending"]),
                    "已提交": len(buckets["completed"]),
                }
                stats = [
                    {**stat, "value": overrides[stat["label"]]}
                    if stat.get("label") in overrides
                    else stat
                    for stat in stats
                ]
            except Exception as exc:
                print(f"[WECHAT_MP] 首页任务计数对齐失败: {exc}")
        conn.commit()
    return {
        "success": True,
        "data": {
            "role": user["role"],
            "user": {"id": user["id"], "name": user["name"]},
            "stats": stats,
            "focus": context.get("dashboard_focus") or {},
            "agenda": (context.get("dashboard_agenda_events") or [])[:AGENDA_LIMIT],
        },
        "error": None,
    }
