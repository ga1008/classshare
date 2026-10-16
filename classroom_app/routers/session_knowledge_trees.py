"""课次知识树 API：读取、AI 生成、末梢完成标记、全部完成/取消、同级排序。

教师（课堂授课教师或超管）可写；本课堂学生只读已生成的知识树与进度。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import AI_DURABLE_JOBS_ENABLED
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services import session_knowledge_tree_service as kt
from ..services.durable_process_job_service import enqueue_process_generation
from ..services.resource_access_service import ensure_classroom_access

router = APIRouter(prefix="/api/classrooms")


class LeafBody(BaseModel):
    done: bool = True


class BulkBody(BaseModel):
    done: bool


class OrderBody(BaseModel):
    parent_id: str = ""
    order: list[str] = Field(default_factory=list, max_length=64)
    version: int | None = None


def _load_session(conn, class_offering_id: int, session_id: int, user: dict) -> tuple[dict, dict]:
    offering = dict(ensure_classroom_access(conn, int(class_offering_id), user))
    session = conn.execute(
        "SELECT * FROM class_offering_sessions WHERE id = ? AND class_offering_id = ? LIMIT 1",
        (int(session_id), int(class_offering_id)),
    ).fetchone()
    if session is None:
        raise HTTPException(404, "课次不存在。")
    return offering, dict(session)


def _state(conn, offering: dict, session: dict, user: dict) -> dict:
    is_teacher = str(user.get("role") or "") == "teacher"
    row = kt.load_row(conn, int(session["id"]))
    if not is_teacher:
        if not row or not kt._loads(row.get("tree_json"), {}).get("nodes"):
            return kt.serialize(None, can_edit=False, has_material=False)
        return kt.serialize(row, can_edit=False, has_material=True)
    if row:
        row = kt.expire_stale_generation(conn, row)
    material_ids = kt.session_material_ids(conn, int(offering["id"]), session, int(offering["teacher_id"]))
    fingerprint = kt.source_fingerprint(conn, material_ids) if material_ids else ""
    payload = kt.serialize(row, can_edit=True, has_material=bool(material_ids), current_fingerprint=fingerprint)
    payload["session_id"] = int(session["id"])
    return payload


@router.get("/{class_offering_id}/sessions/{session_id}/knowledge-tree")
async def api_get_knowledge_tree(class_offering_id: int, session_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        offering, session = _load_session(conn, class_offering_id, session_id, user)
        payload = _state(conn, offering, session, user)
        conn.commit()
    return payload


@router.post("/{class_offering_id}/sessions/{session_id}/knowledge-tree/generate")
async def api_generate_knowledge_tree(class_offering_id: int, session_id: int, user: dict = Depends(get_current_teacher)):
    from ..services.session_knowledge_tree_generation_service import run_generation_job

    with get_db_connection() as conn:
        offering, session = _load_session(conn, class_offering_id, session_id, user)
        if not kt.session_material_ids(conn, int(offering["id"]), session, int(offering["teacher_id"])):
            raise HTTPException(400, "请先为本课次绑定学习材料，再生成知识树。")
        row = kt.ensure_row(
            conn, class_offering_id=int(offering["id"]), session_id=int(session["id"]), teacher_id=int(user["id"]),
        )
        token = kt.start_generation(conn, row)
        if AI_DURABLE_JOBS_ENABLED:
            enqueue_process_generation(
                conn,
                target_type="session_knowledge_tree",
                target_id=str(row["id"]),
                task_token=token,
                class_offering_id=int(offering["id"]),
                teacher_id=int(user["id"]),
                extra_payload={"session_id": int(session["id"]), "task_token": token},
            )
        conn.commit()
        payload = _state(conn, offering, session, user)
    if not AI_DURABLE_JOBS_ENABLED:
        asyncio.create_task(run_generation_job(str(row["id"]), int(session["id"]), token))
    return payload


@router.post("/{class_offering_id}/sessions/{session_id}/knowledge-tree/leaves/{leaf_id}")
async def api_mark_knowledge_leaf(
    class_offering_id: int, session_id: int, leaf_id: str, body: LeafBody, user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        offering, session = _load_session(conn, class_offering_id, session_id, user)
        kt.set_leaf_done(conn, int(session["id"]), str(leaf_id)[:32], bool(body.done))
        conn.commit()
        return _state(conn, offering, session, user)


@router.post("/{class_offering_id}/sessions/{session_id}/knowledge-tree/bulk")
async def api_bulk_knowledge_leaves(
    class_offering_id: int, session_id: int, body: BulkBody, user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        offering, session = _load_session(conn, class_offering_id, session_id, user)
        kt.set_all_done(conn, int(session["id"]), bool(body.done))
        conn.commit()
        return _state(conn, offering, session, user)


@router.put("/{class_offering_id}/sessions/{session_id}/knowledge-tree/order")
async def api_order_knowledge_tree(
    class_offering_id: int, session_id: int, body: OrderBody, user: dict = Depends(get_current_teacher),
):
    with get_db_connection() as conn:
        offering, session = _load_session(conn, class_offering_id, session_id, user)
        kt.save_order(conn, int(session["id"]), body.parent_id, body.order, body.version)
        conn.commit()
        return _state(conn, offering, session, user)
