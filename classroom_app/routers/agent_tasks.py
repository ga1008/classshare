from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..config import AGENT_TASK_RUNTIME_URL, AGENT_TASKS_ENABLED
from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.agent_task_service import (
    agent_workflow_catalog,
    cancel_agent_task,
    create_agent_task,
    get_agent_task,
    list_agent_tasks,
    task_type_options,
)

router = APIRouter(prefix="/api/agent-tasks", tags=["agent-tasks"])


def _teacher_id(user: dict[str, Any]) -> int:
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="登录状态无效，请重新登录。") from exc


@router.get("/bootstrap", response_class=JSONResponse)
def bootstrap_agent_task_center(user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, limit=30)
    return {
        "status": "success",
        "enabled": bool(AGENT_TASKS_ENABLED),
        "runtime_configured": bool(AGENT_TASK_RUNTIME_URL),
        "task_types": task_type_options(),
        "workflow_catalog": agent_workflow_catalog(),
        **queue,
    }


@router.get("", response_class=JSONResponse)
def api_list_agent_tasks(
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(get_current_teacher),
):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, limit=limit)
    return {"status": "success", **queue}


@router.post("", response_class=JSONResponse)
async def api_create_agent_task(request: Request, user: dict = Depends(get_current_teacher)):
    if not AGENT_TASKS_ENABLED:
        raise HTTPException(status_code=503, detail="任务中心暂未启用。")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式错误。")
    with get_db_connection() as conn:
        task = create_agent_task(conn, user, data)
        conn.commit()
    return {"status": "success", "task": task}


@router.get("/{task_id}", response_class=JSONResponse)
def api_get_agent_task(task_id: int, user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id)
    return {"status": "success", "task": task}


@router.post("/{task_id}/cancel", response_class=JSONResponse)
def api_cancel_agent_task(task_id: int, user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = cancel_agent_task(conn, task_id, teacher_id=teacher_id)
    return {"status": "success", "task": task}
