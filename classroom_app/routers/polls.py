from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services import poll_service
from ..services.runtime_metrics_service import record_websocket_sent


router = APIRouter(prefix="/api/polls")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


async def _broadcast_poll_changed(class_offering_ids: list[int], *, reason: str, poll_id: int | None = None) -> None:
    """Notify every assigned classroom so open clients refresh their poll list."""
    if not class_offering_ids:
        return
    from ..services.chat_handler import manager

    for class_offering_id in {int(cid) for cid in class_offering_ids}:
        payload = {
            "type": "classroom_poll_changed",
            "class_offering_id": int(class_offering_id),
            "reason": reason,
        }
        if poll_id is not None:
            payload["poll_id"] = int(poll_id)
        try:
            await manager.broadcast(int(class_offering_id), json.dumps(payload, ensure_ascii=False))
            record_websocket_sent(int(class_offering_id), max(1, len(manager.rooms.get(int(class_offering_id), {}))))
        except Exception:
            # Broadcasting is best-effort; polling fallback keeps clients fresh.
            pass


def _poll_class_ids(conn, poll_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT class_offering_id FROM poll_assignments WHERE poll_id = ?",
        (int(poll_id),),
    ).fetchall()
    return [int(row["class_offering_id"]) for row in rows]


# --------------------------------------------------------------------------- #
# classroom-scoped endpoints
# --------------------------------------------------------------------------- #
@router.get("/classrooms/{class_offering_id}/snapshot", response_class=JSONResponse)
async def classroom_poll_snapshot(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        snapshot = poll_service.load_classroom_snapshot(conn, class_offering_id, user)
    return {"status": "ok", "snapshot": snapshot}


@router.get("/classrooms/{class_offering_id}/candidates", response_class=JSONResponse)
async def classroom_poll_candidates(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        candidates = poll_service.list_class_candidates(conn, class_offering_id, user)
    return {"status": "ok", "candidates": candidates}


@router.post("/classrooms/{class_offering_id}/polls", response_class=JSONResponse)
async def create_classroom_poll(class_offering_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        poll = poll_service.create_poll(
            conn, user, payload, origin=poll_service.ORIGIN_CLASSROOM, class_offering_id=class_offering_id
        )
        snapshot = poll_service.load_classroom_snapshot(conn, class_offering_id, user)
        conn.commit()
    await _broadcast_poll_changed([class_offering_id], reason="poll_created", poll_id=poll["id"])
    return {"status": "ok", "message": "投票活动已创建", "poll": poll, "snapshot": snapshot}


# --------------------------------------------------------------------------- #
# management endpoints
# --------------------------------------------------------------------------- #
@router.get("/manage/list", response_class=JSONResponse)
async def management_poll_list(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        data = poll_service.load_management_list(conn, user)
    return {"status": "ok", **data}


@router.get("/manage/offerings", response_class=JSONResponse)
async def management_offerings(user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        offerings = poll_service.list_teacher_offerings(conn, user)
    return {"status": "ok", "offerings": offerings}


@router.post("/manage/polls", response_class=JSONResponse)
async def create_management_poll(request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        poll = poll_service.create_poll(conn, user, payload, origin=poll_service.ORIGIN_MANAGEMENT)
        data = poll_service.load_management_list(conn, user)
        class_ids = _poll_class_ids(conn, poll["id"])
        conn.commit()
    await _broadcast_poll_changed(class_ids, reason="poll_created", poll_id=poll["id"])
    return {"status": "ok", "message": "投票活动已创建", "poll": poll, **data}


# --------------------------------------------------------------------------- #
# shared poll endpoints (detail / vote / edit / status / assign / delete)
# --------------------------------------------------------------------------- #
@router.get("/{poll_id}", response_class=JSONResponse)
async def poll_detail(poll_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        poll = poll_service.load_poll_detail(conn, poll_id, user)
    return {"status": "ok", "poll": poll}


@router.post("/{poll_id}/vote", response_class=JSONResponse)
async def poll_vote(poll_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    option_ids = payload.get("option_ids")
    if option_ids is None and payload.get("option_id") is not None:
        option_ids = [payload.get("option_id")]
    with get_db_connection() as conn:
        poll = poll_service.vote(conn, poll_id, user, option_ids)
        class_ids = _poll_class_ids(conn, poll_id)
        conn.commit()
    await _broadcast_poll_changed(class_ids, reason="poll_vote", poll_id=poll_id)
    return {"status": "ok", "message": "投票已提交", "poll": poll}


@router.put("/{poll_id}", response_class=JSONResponse)
async def poll_update(poll_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        poll = poll_service.update_poll(conn, poll_id, user, payload)
        class_ids = _poll_class_ids(conn, poll_id)
        conn.commit()
    await _broadcast_poll_changed(class_ids, reason="poll_updated", poll_id=poll_id)
    return {"status": "ok", "message": "投票活动已更新", "poll": poll}


@router.post("/{poll_id}/status", response_class=JSONResponse)
async def poll_set_status(poll_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        poll = poll_service.set_poll_status(conn, poll_id, user, payload.get("status"))
        class_ids = _poll_class_ids(conn, poll_id)
        conn.commit()
    await _broadcast_poll_changed(class_ids, reason="poll_status", poll_id=poll_id)
    return {"status": "ok", "message": "投票状态已更新", "poll": poll}


@router.post("/{poll_id}/assignments", response_class=JSONResponse)
async def poll_set_assignments(poll_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    class_offering_ids = payload.get("class_offering_ids") or []
    with get_db_connection() as conn:
        old_class_ids = _poll_class_ids(conn, poll_id)
        poll = poll_service.set_poll_assignments(conn, poll_id, user, class_offering_ids)
        new_class_ids = _poll_class_ids(conn, poll_id)
        conn.commit()
    await _broadcast_poll_changed(list({*old_class_ids, *new_class_ids}), reason="poll_assigned", poll_id=poll_id)
    return {"status": "ok", "message": "分配已更新", "poll": poll}


@router.delete("/{poll_id}", response_class=JSONResponse)
async def poll_delete(poll_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        class_ids = _poll_class_ids(conn, poll_id)
        poll_service.delete_poll(conn, poll_id, user)
        conn.commit()
    await _broadcast_poll_changed(class_ids, reason="poll_deleted", poll_id=poll_id)
    return {"status": "ok", "message": "投票活动已删除"}
