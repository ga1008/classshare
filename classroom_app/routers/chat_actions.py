"""Confirm / cancel the classroom actions the chat AI proposed.

``POST /api/ai/chat-actions/{token}/execute`` runs the proposal with the
calling teacher's own session (same service functions as the UI buttons),
then fires the same follow-ups the UI routes do (poll notifications and the
classroom websocket refresh). ``.../cancel`` discards it. Only the proposing
teacher may act, only while the proposal is pending and unexpired.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.chat_action_service import cancel_proposal, execute_proposal, mark_proposal_failed

router = APIRouter()


@router.post("/api/ai/chat-actions/{token}/execute", response_class=JSONResponse)
async def api_execute_chat_action(token: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        try:
            result = execute_proposal(conn, user, token)
            conn.commit()
        except HTTPException as exc:
            conn.rollback()
            if exc.status_code not in (404, 409):
                # The action itself was refused (400/403): keep the proposal from being retried blindly.
                mark_proposal_failed(token, str(exc.detail))
            raise
        if result.get("kind") == "poll" and result.get("notify_poll_active"):
            try:
                from ..services.message_center_service import create_poll_published_notifications

                create_poll_published_notifications(conn, int(result["poll_id"]))
                conn.commit()
            except Exception as exc:  # noqa: BLE001 — notification is best-effort, the poll already exists
                conn.rollback()
                print(f"[CHAT-ACTION] poll notification skipped: {exc}")
    if result.get("kind") == "poll":
        try:
            from .polls import _broadcast_poll_changed

            await _broadcast_poll_changed([int(result["class_offering_id"])], reason="chat_action", poll_id=int(result["poll_id"]))
        except Exception as exc:  # noqa: BLE001
            print(f"[CHAT-ACTION] poll broadcast skipped: {exc}")
    result.pop("notify_poll_active", None)
    return {"status": "success", "result": result}


@router.post("/api/ai/chat-actions/{token}/cancel", response_class=JSONResponse)
async def api_cancel_chat_action(token: str, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result = cancel_proposal(conn, user, token)
        conn.commit()
    return {"status": "success", "result": result}
