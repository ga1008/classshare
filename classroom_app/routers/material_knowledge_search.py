"""Knowledge-point search over learning materials.

``GET /api/material-knowledge/search?q=…`` — any logged-in student/teacher;
results are already filtered to what the caller may open (see
``material_knowledge_search_service``). ``class_offering_id`` only boosts and
decorates links for the classroom the user is looking at; it never widens scope.

``GET/POST /api/manage/system/material-search/*`` — super admin index
housekeeping (status, force reindex).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services.material_knowledge_search_service import (
    DEFAULT_MATERIAL_LIMIT,
    search_material_knowledge,
)
from ..services.material_search_index_service import (
    index_status_snapshot,
    request_material_reindex,
)
from ..services.resource_access_service import is_super_admin_teacher

router = APIRouter()


@router.get("/api/material-knowledge/search", response_class=JSONResponse)
async def api_material_knowledge_search(
    q: str = Query(default="", max_length=120),
    class_offering_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
    limit: int = Query(default=DEFAULT_MATERIAL_LIMIT, ge=1, le=12),
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        payload = search_material_knowledge(
            conn,
            user,
            q,
            class_offering_id=class_offering_id,
            session_id=session_id,
            limit=limit,
        )
        conn.commit()
    return {"status": "success", **payload}


class MaterialReindexRequest(BaseModel):
    material_ids: list[int] | None = Field(default=None, max_length=500)


def _require_super_admin(conn, user: dict) -> None:
    if not is_super_admin_teacher(conn, user.get("id")):
        raise HTTPException(status_code=403, detail="只有超级管理员可以管理材料索引。")


@router.get("/api/manage/system/material-search/status", response_class=JSONResponse)
async def api_material_search_status(user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _require_super_admin(conn, user)
        snapshot = index_status_snapshot(conn)
        conn.commit()
    return {"status": "success", "index": snapshot}


@router.post("/api/manage/system/material-search/reindex", response_class=JSONResponse)
async def api_material_search_reindex(
    payload: MaterialReindexRequest | None = None,
    user: dict = Depends(get_current_teacher),
):
    material_ids = payload.material_ids if payload else None
    with get_db_connection() as conn:
        _require_super_admin(conn, user)
        reset = request_material_reindex(conn, material_ids)
        conn.commit()
        snapshot = index_status_snapshot(conn)
        conn.commit()
    return {"status": "success", "reset": reset, "index": snapshot}
