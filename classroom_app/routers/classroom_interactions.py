from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.behavior_tracking_service import record_behavior_batch
from ..services.classroom_interaction_service import (
    ACTIVITY_KIND_LABELS,
    clear_my_help_signal,
    close_activity,
    create_activity,
    load_interaction_snapshot,
    resolve_help_signal,
    resolve_question,
    respond_to_activity,
    set_help_signal,
    submit_question,
)
from ..services.peer_help_service import mark_chat_message_useful
from ..services.runtime_metrics_service import record_websocket_sent


router = APIRouter(prefix="/api/classroom-interactions")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "请求 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return payload


async def _broadcast_changed(
    class_offering_id: int,
    *,
    reason: str,
    activity_id: int | None = None,
    signal_id: int | None = None,
) -> None:
    from ..services.chat_handler import manager

    payload = {
        "type": "classroom_interaction_changed",
        "class_offering_id": int(class_offering_id),
        "reason": reason,
    }
    if activity_id is not None:
        payload["activity_id"] = int(activity_id)
    if signal_id is not None:
        payload["signal_id"] = int(signal_id)
    await manager.broadcast(int(class_offering_id), json.dumps(payload, ensure_ascii=False))
    record_websocket_sent(int(class_offering_id), max(1, len(manager.rooms.get(int(class_offering_id), {}))))


def _record_interaction_behavior(
    *,
    class_offering_id: int,
    user: dict[str, Any],
    action_type: str,
    summary_text: str,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        record_behavior_batch(
            class_offering_id=int(class_offering_id),
            user_pk=int(user.get("id") or 0),
            user_role=str(user.get("role") or ""),
            display_name=str(user.get("name") or user.get("username") or "课堂成员"),
            page_key="classroom_interaction",
            events=[
                {
                    "action_type": action_type,
                    "summary_text": summary_text,
                    "page_key": "classroom_interaction",
                    "payload": payload or {},
                }
            ],
            session_started_at=str(user.get("login_time") or "").strip() or None,
            wait=False,
        )
    except Exception:
        pass


@router.get("/classrooms/{class_offering_id}/snapshot", response_class=JSONResponse)
async def interaction_snapshot(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
    return {"status": "ok", "snapshot": snapshot}


@router.post("/classrooms/{class_offering_id}/messages/{message_id}/useful", response_class=JSONResponse)
async def mark_discussion_message_useful(
    class_offering_id: int,
    message_id: int,
    user: dict = Depends(get_current_user),
):
    with get_db_connection() as conn:
        result = mark_chat_message_useful(conn, class_offering_id, message_id, user)
        conn.commit()
    if result.get("counted"):
        await _broadcast_changed(class_offering_id, reason="peer_help_marked")
    return {
        "status": "ok",
        "message": "已记录这次同伴帮助" if result.get("counted") else "这次标记已记录过",
        "result": result,
    }


@router.post("/classrooms/{class_offering_id}/activities", response_class=JSONResponse)
async def create_live_activity(class_offering_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        activity = create_activity(conn, class_offering_id, user, payload)
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_activity_create",
        summary_text=f"发起{ACTIVITY_KIND_LABELS.get(activity.get('kind'), '课堂互动')}：{activity.get('title')}",
        payload={"activity_id": activity["id"], "kind": activity.get("kind")},
    )
    await _broadcast_changed(class_offering_id, reason="activity_created", activity_id=activity["id"])
    return {
        "status": "ok",
        "message": "课堂互动已发起",
        "activity": activity,
        "snapshot": snapshot,
    }


@router.post("/activities/{activity_id}/respond", response_class=JSONResponse)
async def respond_live_activity(activity_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        activity = respond_to_activity(conn, activity_id, user, payload)
        class_offering_id = int(activity["class_offering_id"])
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_activity_response",
        summary_text=f"提交{ACTIVITY_KIND_LABELS.get(activity.get('kind'), '课堂互动')}回应：{activity.get('title')}",
        payload={"activity_id": activity["id"], "kind": activity.get("kind")},
    )
    await _broadcast_changed(class_offering_id, reason="activity_response", activity_id=activity["id"])
    return {
        "status": "ok",
        "message": "回应已提交",
        "activity": activity,
        "snapshot": snapshot,
    }


@router.post("/activities/{activity_id}/questions", response_class=JSONResponse)
async def create_live_question(activity_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        question = submit_question(conn, activity_id, user, payload)
        activity_row = conn.execute(
            "SELECT class_offering_id FROM classroom_live_activities WHERE id = ?",
            (int(activity_id),),
        ).fetchone()
        if activity_row is None:
            raise HTTPException(404, "互动活动不存在")
        class_offering_id = int(activity_row["class_offering_id"])
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_activity_question",
        summary_text="提交课堂即时提问",
        payload={"activity_id": int(activity_id), "question_id": question["id"], "anonymous": question["is_anonymous"]},
    )
    await _broadcast_changed(class_offering_id, reason="question_created", activity_id=int(activity_id))
    return {
        "status": "ok",
        "message": "问题已提交",
        "question": question,
        "snapshot": snapshot,
    }


@router.post("/activities/{activity_id}/close", response_class=JSONResponse)
async def close_live_activity(activity_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        activity = close_activity(conn, activity_id, user)
        class_offering_id = int(activity["class_offering_id"])
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_activity_close",
        summary_text=f"结束课堂互动：{activity.get('title')}",
        payload={"activity_id": activity["id"], "kind": activity.get("kind")},
    )
    await _broadcast_changed(class_offering_id, reason="activity_closed", activity_id=activity["id"])
    return {
        "status": "ok",
        "message": "互动已结束",
        "activity": activity,
        "snapshot": snapshot,
    }


@router.post("/questions/{question_id}/resolve", response_class=JSONResponse)
async def resolve_live_question(question_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        question = resolve_question(conn, question_id, user, status=str(payload.get("status") or "addressed"))
        activity_row = conn.execute(
            "SELECT class_offering_id FROM classroom_live_activities WHERE id = ?",
            (int(question["activity_id"]),),
        ).fetchone()
        if activity_row is None:
            raise HTTPException(404, "互动活动不存在")
        class_offering_id = int(activity_row["class_offering_id"])
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_question_resolve",
        summary_text="处理课堂即时提问",
        payload={"question_id": int(question_id), "status": question["status"]},
    )
    await _broadcast_changed(class_offering_id, reason="question_resolved", activity_id=int(question["activity_id"]))
    return {
        "status": "ok",
        "message": "问题状态已更新",
        "question": question,
        "snapshot": snapshot,
    }


@router.post("/classrooms/{class_offering_id}/signals", response_class=JSONResponse)
async def set_live_signal(class_offering_id: int, request: Request, user: dict = Depends(get_current_user)):
    payload = await _json_payload(request)
    with get_db_connection() as conn:
        signal = set_help_signal(conn, class_offering_id, user, payload)
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_signal_update",
        summary_text=f"更新课堂状态：{(signal or {}).get('signal_label', '清除')}",
        payload={"signal_id": (signal or {}).get("id"), "signal_type": (signal or {}).get("signal_type")},
    )
    await _broadcast_changed(
        class_offering_id,
        reason="signal_updated",
        signal_id=signal["id"] if signal else None,
    )
    return {
        "status": "ok",
        "message": "课堂状态已更新" if signal else "课堂状态已清除",
        "signal": signal,
        "snapshot": snapshot,
    }


@router.post("/classrooms/{class_offering_id}/signals/clear", response_class=JSONResponse)
async def clear_live_signal(class_offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        clear_my_help_signal(conn, class_offering_id, user)
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_signal_clear",
        summary_text="清除课堂举手/求助状态",
        payload={},
    )
    await _broadcast_changed(class_offering_id, reason="signal_cleared")
    return {
        "status": "ok",
        "message": "课堂状态已清除",
        "snapshot": snapshot,
    }


@router.post("/signals/{signal_id}/resolve", response_class=JSONResponse)
async def resolve_live_signal(signal_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        signal = resolve_help_signal(conn, signal_id, user)
        class_offering_id = int(signal["class_offering_id"])
        snapshot = load_interaction_snapshot(conn, class_offering_id, user)
        conn.commit()
    _record_interaction_behavior(
        class_offering_id=class_offering_id,
        user=user,
        action_type="live_signal_resolve",
        summary_text=f"处理学生课堂状态：{signal.get('signal_label')}",
        payload={"signal_id": int(signal_id), "signal_type": signal.get("signal_type")},
    )
    await _broadcast_changed(class_offering_id, reason="signal_resolved", signal_id=int(signal_id))
    return {
        "status": "ok",
        "message": "学生状态已处理",
        "signal": signal,
        "snapshot": snapshot,
    }
