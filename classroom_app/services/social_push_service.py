"""Real-time delivery to /ws/social connections (social doc §7 WebSocket, §8).

``push(identities, event)`` delivers in-process when running inside the app
process (``mark_app_process()`` is called by ``classroom_app.app``); any other
process — notably the scheduler container where 散修 act — forwards the event
to ``POST /api/internal/social/push`` on the main app with the shared
``X-Wanderer-Internal-Token``.

Single-worker assumption: one app worker holds every /ws/social socket, so the
module-level connection registry is complete.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Iterable

from . import presence_service

INTERNAL_PUSH_PATH = "/api/internal/social/push"
INTERNAL_HTTP_TIMEOUT_SECONDS = 5.0
SEND_TIMEOUT_SECONDS = 5.0

_connections: dict[str, dict[str, Any]] = {}
_state = {"app_process": False}


def mark_app_process() -> None:
    _state["app_process"] = True


def is_app_process() -> bool:
    return bool(_state["app_process"])


MAX_SOCKETS_PER_IDENTITY = 5


def register(identity: str, conn_id: str, websocket: Any) -> list[tuple[str, Any]]:
    """Add a socket; beyond 5 per identity the oldest ones are unregistered and returned (caller closes them)."""
    sockets = _connections.setdefault(identity, {})
    sockets[conn_id] = websocket
    evicted = []
    while len(sockets) > MAX_SOCKETS_PER_IDENTITY:
        oldest = next(iter(sockets))
        evicted.append((oldest, sockets.pop(oldest)))
    return evicted


def unregister(identity: str, conn_id: str) -> None:
    sockets = _connections.get(identity)
    if sockets is None:
        return
    sockets.pop(conn_id, None)
    if not sockets:
        _connections.pop(identity, None)


def connection_count(identity: str | None = None) -> int:
    if identity is not None:
        return len(_connections.get(identity, {}))
    return sum(len(item) for item in _connections.values())


def reset_for_tests() -> None:
    _connections.clear()


async def _send(identity: str, conn_id: str, websocket: Any, text: str) -> bool:
    try:
        await asyncio.wait_for(websocket.send_text(text), timeout=SEND_TIMEOUT_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001 - a dead socket is dropped, the others still receive
        print(f"[SOCIAL_PUSH] send failed to {identity}/{conn_id}: {exc!r}")
        unregister(identity, conn_id)
        await close_quietly(websocket)  # the client notices and reconnects instead of silently missing pushes
        return False


async def close_quietly(websocket: Any, code: int = 1011) -> None:
    try:
        await asyncio.wait_for(websocket.close(code=code), timeout=SEND_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - already closed / broken: nothing more to do
        pass


async def deliver_local(identities: Iterable[str], event: dict[str, Any]) -> int:
    text = json.dumps(event, ensure_ascii=False)
    jobs = [
        _send(identity, conn_id, websocket, text)
        for identity in {str(item) for item in identities if item}
        for conn_id, websocket in list(_connections.get(identity, {}).items())
    ]
    if not jobs:
        return 0
    results = await asyncio.gather(*jobs)
    return sum(1 for ok in results if ok)


async def _post_internal(body: dict[str, Any]) -> int:
    import httpx

    from .wanderer_settings_service import INTERNAL_TOKEN_HEADER, main_app_internal_url, wanderer_internal_token

    url = f"{main_app_internal_url()}{INTERNAL_PUSH_PATH}"
    try:
        async with httpx.AsyncClient(timeout=INTERNAL_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers={INTERNAL_TOKEN_HEADER: wanderer_internal_token()}, json=body)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[SOCIAL_PUSH] internal push failed: {exc!r}")
        return 0
    return int(payload.get("delivered") or 0) if isinstance(payload, dict) else 0


async def _deliver_remote(identities: list[str], event: dict[str, Any]) -> int:
    return await _post_internal({"identities": identities, "event": event})


async def push(identities: Iterable[str], event: dict[str, Any]) -> int:
    """Deliver ``event`` to every socket of ``identities``; returns sockets reached."""
    targets = sorted({str(item) for item in identities if item})
    if not targets or not isinstance(event, dict) or not event.get("type"):
        return 0
    if is_app_process():
        return await deliver_local(targets, event)
    return await _deliver_remote(targets, event)


INTERNAL_BATCH_MAX = 250


async def push_events(events: Iterable[tuple[Iterable[str], dict[str, Any]]]) -> int:
    """Deliver ``(identities, event)`` pairs collected by the services, after commit.

    Outside the app process the pairs travel in batches (one internal request
    per ≤ 250 pairs) instead of one request per pair — a group message fans out
    to one event per member.
    """
    pairs = [(sorted({str(item) for item in identities if item}), event) for identities, event in events or []]
    pairs = [(targets, event) for targets, event in pairs if targets and isinstance(event, dict) and event.get("type")]
    if is_app_process() or len(pairs) <= 1:
        delivered = 0
        for identities, event in pairs:
            delivered += await push(identities, event)
        return delivered
    delivered = 0
    for start in range(0, len(pairs), INTERNAL_BATCH_MAX):
        chunk = pairs[start:start + INTERNAL_BATCH_MAX]
        delivered += await _post_internal({"batch": [{"identities": targets, "event": event}
                                                     for targets, event in chunk]})
    return delivered


# ---------------------------------------------------------------------------
# Presence fan-out (friends + co-group members only)
# ---------------------------------------------------------------------------

def _audience(identity: str) -> list[str]:
    from ..database import get_db_connection
    from .social_relations import presence_audience

    with get_db_connection() as conn:
        return sorted(presence_audience(conn, identity))


async def _on_presence_change(identity: str, online: bool, last_seen_at: str) -> None:
    if not is_app_process():
        return
    from .social_common import run_db

    audience = await run_db(_audience, identity)
    await deliver_local(audience, {"type": "presence", "identity": identity, "online": bool(online),
                                   "last_seen_at": last_seen_at})


presence_service.add_listener(_on_presence_change)
