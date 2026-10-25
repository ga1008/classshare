"""Internal endpoints the 散修 tick (scheduler container) calls on the main app (design §5.3).

Only the app process holds the classroom websockets, so the online counts and
the live broadcast of a stored wanderer message are exposed here. Both require
``X-Wanderer-Internal-Token`` (``wanderer_settings_service.wanderer_internal_token``);
nginx does not route ``/api/internal/*`` from the internet, the token is the
second line of defence. Nothing here touches the database.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from ..services.wanderer_settings_service import internal_token_matches

router = APIRouter()


def _require_token(token: str) -> None:
    if not internal_token_matches(token):
        raise HTTPException(status_code=401, detail="invalid internal token")


@router.get("/api/internal/wanderers/rooms")
async def wanderer_rooms(x_wanderer_internal_token: str = Header(default="")) -> dict[str, Any]:
    """``{"rooms": {class_offering_id: online participant count}}`` for rooms with anyone online."""
    _require_token(x_wanderer_internal_token)
    from ..services.chat_handler import manager

    rooms = {str(room_id): len(participants) for room_id, participants in manager.room_participants.items()
             if participants}
    return {"status": "ok", "rooms": rooms}


@router.post("/api/internal/wanderers/classroom-broadcast")
async def wanderer_classroom_broadcast(
    payload: dict[str, Any] = Body(...),
    x_wanderer_internal_token: str = Header(default=""),
) -> dict[str, Any]:
    """Push an already-stored chat message to the room's websockets (best effort)."""
    _require_token(x_wanderer_internal_token)
    try:
        class_offering_id = int(payload.get("class_offering_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="class_offering_id is required") from exc
    message = payload.get("message")
    if not isinstance(message, dict) or not message.get("id"):
        raise HTTPException(status_code=400, detail="message must be a stored chat message")
    from ..services.chat_handler import manager

    recipients = len(manager.rooms.get(class_offering_id, {}))
    if recipients:
        await manager.broadcast(class_offering_id, json.dumps(message, ensure_ascii=False))
    return {"status": "ok", "recipients": recipients}
