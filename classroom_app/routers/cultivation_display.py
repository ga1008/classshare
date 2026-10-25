"""Cultivation emblems beside avatars + the student's 隐藏修为 setting."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.cultivation_display_service import (
    EMBLEM_BATCH_LIMIT,
    invalidate_emblem_cache,
    is_cultivation_hidden,
    load_student_emblems,
    set_cultivation_hidden,
)

router = APIRouter()
_ID = re.compile(r"[0-9]{1,12}")


def _parse_students(raw: str, user: dict) -> tuple[list[int], int | None]:
    self_id = int(user["id"]) if user.get("role") == "student" and user.get("id") is not None else None
    ids: list[int] = []
    for token in str(raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        if token == "self":
            if self_id is not None:
                ids.append(self_id)
            continue
        if _ID.fullmatch(token):
            ids.append(int(token))
        if len(ids) >= EMBLEM_BATCH_LIMIT:
            break
    return ids, self_id


@router.get("/api/cultivation/emblems", response_class=JSONResponse)
def get_cultivation_emblems(students: str = Query("", max_length=4000), user: dict = Depends(get_current_user)):
    ids, self_id = _parse_students(students, user)
    with get_db_connection() as conn:
        emblems = load_student_emblems(conn, ids)
        conn.commit()
    return {
        "status": "success",
        "self_id": self_id,
        "items": {str(student_id): payload for student_id, payload in emblems.items()},
    }


@router.get("/api/profile/cultivation-visibility", response_class=JSONResponse)
def get_cultivation_visibility(user: dict = Depends(get_current_user)):
    if user.get("role") != "student":
        return {"status": "success", "applicable": False, "hidden": False}
    with get_db_connection() as conn:
        hidden = is_cultivation_hidden(conn, int(user["id"]))
        conn.commit()
    return {"status": "success", "applicable": True, "hidden": hidden}


@router.put("/api/profile/cultivation-visibility", response_class=JSONResponse)
async def put_cultivation_visibility(request: Request, user: dict = Depends(get_current_user)):
    if user.get("role") != "student":
        raise HTTPException(403, "只有学生可以设置修为展示")
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - malformed body
        raise HTTPException(400, "请求格式错误") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("hidden"), bool):
        raise HTTPException(400, "hidden 必须是布尔值")
    with get_db_connection() as conn:
        hidden = set_cultivation_hidden(conn, int(user["id"]), payload["hidden"])
        conn.commit()
    invalidate_emblem_cache([int(user["id"])])
    return {
        "status": "success",
        "hidden": hidden,
        "message": "已隐藏修为，头像旁不再显示境界标志。" if hidden else "已公开修为，头像旁会显示你的最高境界标志。",
    }
