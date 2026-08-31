"""小程序私人待办更新 shim。

微信 uni.request 不支持 PATCH 方法，既有 ``PATCH /api/todos/{id}`` 与
``PATCH /api/classrooms/{oid}/todos/{id}`` 小程序无法直调；本端点只做
HTTP 方法适配，校验与业务全部委托 ``update_manual_todo``——服务内按
owner_role + owner_user_pk 校验归属，非本人的待办返回 404，学生/教师
两种角色的账户级与课堂级待办统一覆盖（enforce_classroom_scope=False
仅放宽课堂过滤，归属校验不受影响）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...db.connection import get_db_connection
from ...services.todo_service import TodoValidationError, update_manual_todo
from .deps import get_current_mp_user

router = APIRouter(prefix="/todos")


class MpTodoUpdatePayload(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    due_at: Optional[str] = None
    reminder_enabled: Optional[bool] = None
    completed: Optional[bool] = None


@router.post("/{todo_id}/update")
def mp_update_todo(
    todo_id: int,
    payload: MpTodoUpdatePayload,
    user: dict = Depends(get_current_mp_user),
):
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的内容")
    with get_db_connection() as conn:
        try:
            result = update_manual_todo(
                conn,
                class_offering_id=None,
                todo_id=int(todo_id),
                user=user,
                payload=data,
                enforce_classroom_scope=False,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TodoValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        conn.commit()
    return {"success": True, "data": result, "error": None}
