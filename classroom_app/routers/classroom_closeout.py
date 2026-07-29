"""结课（end-of-term closeout）HTTP API。

两个端点，都只对本课堂授课教师（或超管）开放：

* ``GET  /api/classroom/{id}/closeout/summary``  —— 只读预览未结束的过程性任务
* ``POST /api/classroom/{id}/closeout/execute``  —— 按教师选择批量收尾

单份作业/测验的"截止"按钮走 ``POST /api/assignments/{id}/close``，实现在
``homework_parts/grading.py``，与批改相关的其它教师操作放在一起。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.behavior_tracking_service import record_behavior_event
from ..services.classroom_closeout_service import build_closeout_summary, execute_closeout
from ..services.resource_access_service import is_super_admin_teacher
from ..services.runtime_metrics_service import record_websocket_sent


router = APIRouter(prefix="/api/classroom")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        # 结课确认可以不带 body，等价于"全部收尾、默认分 0"。
        return {}
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


def _ensure_offering_access(conn, class_offering_id: int, user: dict) -> None:
    owns = conn.execute(
        "SELECT id FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
        (int(class_offering_id), int(user["id"])),
    ).fetchone()
    if not owns and not is_super_admin_teacher(conn, int(user["id"])):
        raise HTTPException(403, "无权访问该课堂")


async def _broadcast_closeout(class_offering_id: int) -> None:
    """通知在线的学生端刷新：作业已截止、投票已结束、分组已归档。"""
    from ..services.chat_handler import manager

    payload = {
        "type": "classroom_closeout_completed",
        "class_offering_id": int(class_offering_id),
    }
    try:
        await manager.broadcast(int(class_offering_id), json.dumps(payload, ensure_ascii=False))
        record_websocket_sent(
            int(class_offering_id),
            max(1, len(manager.rooms.get(int(class_offering_id), {}))),
        )
    except Exception:
        # 广播是尽力而为；学生端轮询/刷新仍会拿到新状态。
        pass


@router.get("/{class_offering_id}/closeout/summary", response_class=JSONResponse)
async def get_closeout_summary(
    class_offering_id: int,
    user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        _ensure_offering_access(conn, class_offering_id, user)
        summary = build_closeout_summary(conn, int(class_offering_id), int(user["id"]))
        # close_overdue_assignments 会落库，必须提交。
        conn.commit()
    if not summary.get("exists"):
        raise HTTPException(404, "未找到此课堂")
    return {"status": "success", **summary}


@router.post("/{class_offering_id}/closeout/execute", response_class=JSONResponse)
async def run_closeout(
    class_offering_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        _ensure_offering_access(conn, class_offering_id, user)
        try:
            result = execute_closeout(conn, int(class_offering_id), user, payload)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        conn.commit()

    try:
        record_behavior_event(
            class_offering_id=int(class_offering_id),
            user_pk=int(user["id"]),
            user_role="teacher",
            display_name=str(user.get("name") or user["id"]),
            action_type="classroom_closeout",
            session_started_at=str(user.get("login_time") or "").strip() or None,
            summary_text=f"结课收尾：处理 {result.get('processed_total', 0)} 项",
            payload={
                "processed": result.get("processed"),
                "skipped": result.get("skipped"),
                "failure_count": len(result.get("failures") or []),
                "default_score": result.get("default_score"),
            },
            page_key="classroom_main",
        )
    except Exception as exc:
        print(f"[BEHAVIOR] 记录结课失败: {exc}")

    await _broadcast_closeout(int(class_offering_id))
    return {"status": "success", **result}
