"""WebSocket ``/ws/social`` — one per logged-in page (social doc §7, §8).

Auth mirrors the classroom socket (``/ws/{class_offering_id}`` in files.py):
cookie ``access_token`` -> ``verify_token`` (with a session-level fallback for
proxies), close 1008 on failure. Client frames: ``ping`` (every 25 s; nginx
reads time out at 60 s), ``typing``, ``focus`` and the ``lan_*`` WebRTC
signalling relay, which only flows between friends or co-group members and
never between a blocked pair.

Abuse limits: each socket has a token bucket (20 frames/s, burst 40); excess
frames are dropped and a socket that keeps flooding (≥ 100 drops within 10 s)
is closed (1008). At most 5 sockets per identity — the oldest is closed (4008)
when a sixth connects. ``typing`` is relayed at most once per 3 s per
(sender, conversation). ``lan_*`` payloads are capped at 16 KB and only go to
an online friend/co-member who is not blocked either way. DB work runs on the
dedicated social executor (``social_common.run_db``).

Single-worker assumption: this process holds every social socket; see
``social_push_service`` for delivery from other processes.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..database import get_db_connection
from ..dependencies import normalize_ip, verify_token
from ..services import presence_service, social_push_service
from ..services.social_common import SocialError, blocked_either, ensure_schema, normalize_identity, run_db
from ..services.social_conversation_service import unread_total
from ..services.social_message_service import typing_audience
from ..services.social_relations import are_friends, friend_identities, share_group

router = APIRouter()

LAN_TYPES = ("lan_offer", "lan_answer", "lan_ice", "lan_cancel")
MAX_FRAME_CHARS = 32_000
TYPING_CACHE_SECONDS = 30.0
FRAME_RATE = 20.0
FRAME_BURST = 40.0
ABUSE_DROPS = 100
ABUSE_WINDOW_SECONDS = 10.0
TYPING_INTERVAL_SECONDS = 3.0
LAN_PAYLOAD_MAX_CHARS = 16 * 1024
CLOSE_TOO_MANY_SOCKETS = 4008
_typing_last: dict[tuple[str, str], float] = {}


class FrameLimiter:
    """Token bucket per socket; ``allow`` False = drop the frame; ``abusive`` True = close the socket."""

    def __init__(self, rate: float | None = None, burst: float | None = None) -> None:
        self.rate = FRAME_RATE if rate is None else rate
        self.burst = FRAME_BURST if burst is None else burst
        self.tokens, self.stamp = self.burst, time.monotonic()
        self.drops: list[float] = []

    def allow(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        self.tokens = min(self.burst, self.tokens + max(0.0, moment - self.stamp) * self.rate)
        self.stamp = moment
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        self.drops = [stamp for stamp in self.drops if moment - stamp < ABUSE_WINDOW_SECONDS] + [moment]
        return False

    @property
    def abusive(self) -> bool:
        return len(self.drops) >= ABUSE_DROPS


def typing_allowed(sender: str, key: str, now: float | None = None) -> bool:
    """Server-side throttle: one typing relay per (sender, conversation) per 3 s."""
    moment = time.monotonic() if now is None else now
    last = _typing_last.get((sender, key))
    if last is not None and moment - last < TYPING_INTERVAL_SECONDS:
        return False
    if len(_typing_last) > 50_000:
        _typing_last.clear()
    _typing_last[(sender, key)] = moment
    return True


def _client_ip(websocket: WebSocket) -> Optional[str]:
    forwarded = websocket.headers.get("x-forwarded-for")
    real_ip = websocket.headers.get("x-real-ip")
    if forwarded:
        raw = forwarded.split(",")[0].strip()
    elif real_ip:
        raw = real_ip.strip()
    else:
        raw = websocket.client.host if websocket.client else None
    return normalize_ip(raw) or raw


async def _authenticate(websocket: WebSocket) -> Optional[str]:
    token = websocket.cookies.get("access_token")
    user = await asyncio.to_thread(verify_token, token, _client_ip(websocket))
    if user is None and token is not None:
        user = await asyncio.to_thread(verify_token, token, None)
    if not user:
        return None
    try:
        return normalize_identity(f"{user.get('role')}:{int(user.get('id'))}")
    except (SocialError, TypeError, ValueError):
        return None


def _hello_payload(me: str) -> dict[str, Any]:
    with get_db_connection() as conn:
        ensure_schema(conn)
        online = presence_service.presence_map(conn, friend_identities(conn, me))
        return {"type": "hello", "me": me,
                "online_friends": sorted(item for item, state in online.items() if state["online"]),
                "unread_total": unread_total(conn, me)}


def _typing_targets(me: str, key: Any) -> tuple[str, list[str]]:
    with get_db_connection() as conn:
        return typing_audience(conn, me, key)


def _lan_allowed(me: str, target: str) -> bool:
    with get_db_connection() as conn:
        if blocked_either(conn, me, target):
            return False
        return are_friends(conn, me, target) or share_group(conn, me, target)


class _Session:
    def __init__(self, websocket: WebSocket, me: str) -> None:
        self.websocket = websocket
        self.me = me
        self.conn_id = uuid.uuid4().hex
        self.focus: Optional[str] = None
        self.typing_cache: dict[str, tuple[float, str, list[str]]] = {}

    async def send(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def typing(self, key: Any) -> None:
        if not typing_allowed(self.me, str(key)):
            return
        now = asyncio.get_running_loop().time()
        cached = self.typing_cache.get(str(key))
        if cached and now - cached[0] < TYPING_CACHE_SECONDS:
            normalized, targets = cached[1], cached[2]
        else:
            normalized, targets = await run_db(_typing_targets, self.me, key)
            self.typing_cache[str(key)] = (now, normalized, targets)
        await social_push_service.deliver_local(
            targets, {"type": "typing", "conversation_key": normalized, "identity": self.me})

    async def lan(self, frame: dict[str, Any]) -> None:
        target = normalize_identity(frame.get("to"))
        if len(json.dumps(frame.get("payload"), ensure_ascii=False)) > LAN_PAYLOAD_MAX_CHARS:
            raise SocialError("直传信令过大。", 413)
        if target == self.me or not social_push_service.connection_count(target):
            raise SocialError("对方不在线，无法直传。", 409)
        if not await run_db(_lan_allowed, self.me, target):
            raise SocialError("无法与该用户建立直传。", 403)
        await social_push_service.deliver_local([target], {
            "type": frame["type"], "from": self.me, "transfer_id": frame.get("transfer_id"),
            "payload": frame.get("payload"),
        })

    async def handle(self, frame: dict[str, Any]) -> None:
        kind = str(frame.get("type") or "")
        presence_service.touch(self.me)
        if kind == "ping":
            await self.send({"type": "pong"})
        elif kind == "typing":
            await self.typing(frame.get("conversation_key"))
        elif kind == "focus":
            key = frame.get("conversation_key")
            self.focus = str(key) if key else None
        elif kind in LAN_TYPES:
            await self.lan(frame)
        else:
            await self.send({"type": "error", "detail": "未知的消息类型。"})


def _parse_frame(raw: str) -> Optional[dict[str, Any]]:
    if not raw or len(raw) > MAX_FRAME_CHARS:
        return None
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return frame if isinstance(frame, dict) else None


async def _serve(session: _Session) -> None:
    limiter = FrameLimiter()
    while True:
        raw = await session.websocket.receive_text()
        if not limiter.allow():
            if limiter.abusive:
                await session.websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            continue  # dropped silently: answering would amplify the flood
        frame = _parse_frame(raw)
        if frame is None:
            await session.send({"type": "error", "detail": "消息格式无效。"})
            continue
        try:
            await session.handle(frame)
        except SocialError as exc:
            await session.send({"type": "error", "detail": exc.detail})


@router.websocket("/ws/social")
async def social_websocket(websocket: WebSocket):
    me = await _authenticate(websocket)
    if not me:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    session = _Session(websocket, me)
    for old_id, old_socket in social_push_service.register(me, session.conn_id, websocket):
        presence_service.connection_closed(me, old_id)
        await social_push_service.close_quietly(old_socket, code=CLOSE_TOO_MANY_SOCKETS)
    presence_service.connection_opened(me, session.conn_id, "social_ws")
    try:
        await session.send(await run_db(_hello_payload, me))
        await _serve(session)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - one broken socket must not leak its registrations
        print(f"[SOCIAL_WS] connection error for {me}: {exc!r}")
    finally:
        social_push_service.unregister(me, session.conn_id)
        presence_service.connection_closed(me, session.conn_id)
