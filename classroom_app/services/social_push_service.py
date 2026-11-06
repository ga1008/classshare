"""Real-time delivery to /ws/social connections (social doc §7 WebSocket, §8).

``push(identities, event)`` delivers in-process when running inside the app
process (``mark_app_process()`` is called by ``classroom_app.app``); any other
process — notably the scheduler container where 散修 act — forwards the event
to ``POST /api/internal/social/push`` on the main app with the shared
``X-Wanderer-Internal-Token``.

Single-worker assumption: one app worker holds every /ws/social socket, so the
module-level connection registry is complete.

Recipients retain the authorization snapshot of their committed transaction.
Each socket has a bounded critical FIFO and a coalesced presence mailbox.
Critical frames have a five-second queue-age deadline, all sends time out in
five seconds; overflow/expiry closes 1013 for access/history revalidation.
Admissions are not delivery acknowledgements: internal callers still await
physical sends, while committed REST responses never wait on client I/O.
"""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from time import monotonic
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
MAX_OUTBOUND_FRAMES = 32
MAX_PRESENCE_ENTRIES = 256
MAX_OUTBOUND_BYTES = 256 * 1024
MAX_QUEUE_AGE_SECONDS = 5.0
_writers: dict[tuple[str, str], "_SocketWriter"] = {}
_writer_tasks: set[asyncio.Task] = set()
_closing_tasks: set[asyncio.Task] = set()
_push_stats = {"queued": 0, "sent": 0, "failed": 0, "discarded_frames": 0,
               "presence_coalesced": 0, "overflow_disconnects": 0, "expired_disconnects": 0}


def _complete(future, result: bool) -> None:
    if future is not None and not future.done():
        future.set_result(result)


def _track_close(websocket: Any, code: int) -> None:
    task = asyncio.create_task(close_quietly(websocket, code))
    _closing_tasks.add(task)
    task.add_done_callback(_closing_tasks.discard)


class _SocketWriter:
    """One writer owns a critical FIFO and a coalesced presence-state mailbox.

    Presence replaces only an unsent state for the same identity. It shares
    the byte bound, but never consumes chat/control slots during join storms.
    Critical frames retain their order and take priority after hello.
    """
    def __init__(self, identity: str, conn_id: str, websocket: Any, *, awaiting_hello: bool = False):
        self.identity, self.conn_id, self.websocket = identity, conn_id, websocket
        self.queue: asyncio.Queue = asyncio.Queue()
        self.presence: OrderedDict[str, tuple] = OrderedDict()
        self.presence_frames = 0
        self.wake = asyncio.Event()
        self.frames = 0
        self.bytes = 0
        self.current = None
        self.current_presence = False
        self.closed = False
        self.ready = asyncio.Event()
        self.hello_frame = None
        if awaiting_hello:
            # Reserve the first position before the hello snapshot starts.
            # Committed events may queue behind it while DB work is running.
            self.hello_frame = [None, 0, asyncio.get_running_loop().create_future(), monotonic()]
            self.queue.put_nowait(self.hello_frame)
            self.frames = 1
        else:
            self.ready.set()
        self.task = asyncio.create_task(self.run())
        _writer_tasks.add(self.task)
        self.task.add_done_callback(_writer_tasks.discard)

    def enqueue(self, text: str, size: int, *, confirmed: bool, presence_key: str | None = None):
        if self.closed:
            return False, None
        previous = self.presence.get(presence_key) if presence_key is not None else None
        previous_size = previous[1] if previous else 0
        full = (previous is None and self.presence_frames >= MAX_PRESENCE_ENTRIES) if presence_key is not None else (
            self.frames >= MAX_OUTBOUND_FRAMES)
        if full or self.bytes - previous_size + size > MAX_OUTBOUND_BYTES:
            _push_stats["overflow_disconnects"] += 1
            # Frames are durable DB facts or invalidation hints. Force a fresh
            # hello/cursor recovery rather than leave a silently incomplete UI.
            self.remove_registration()
            self.stop()
            _track_close(self.websocket, 1013)
            return False, None
        ack = asyncio.get_running_loop().create_future() if confirmed else None
        frame = (text, size, ack, monotonic())
        if presence_key is not None:
            if previous is not None:
                _complete(previous[2], False)
                _push_stats["discarded_frames"] += 1
                _push_stats["presence_coalesced"] += 1
            else:
                self.presence_frames += 1
            self.presence[presence_key] = frame
        else:
            self.frames += 1
            self.queue.put_nowait(frame)
        self.bytes += size - previous_size
        self.wake.set()
        _push_stats["queued"] += 1
        return True, ack

    def complete_hello(self, text: str, size: int):
        frame = self.hello_frame
        if self.closed or frame is None:
            return False, None
        if self.bytes + size > MAX_OUTBOUND_BYTES:
            _push_stats["overflow_disconnects"] += 1
            self.remove_registration()
            self.stop()
            _track_close(self.websocket, 1013)
            return False, None
        frame[0], frame[1], frame[3] = text, size, monotonic()
        self.bytes += size
        self.hello_frame = None
        self.ready.set()
        self.wake.set()
        _push_stats["queued"] += 1
        return True, frame[2]

    def remove_registration(self) -> None:
        key = (self.identity, self.conn_id)
        # An old task must never unregister a replacement that reused its ID.
        if _writers.get(key) is not self:
            return
        _writers.pop(key, None)
        sockets = _connections.get(self.identity)
        if sockets is not None and sockets.get(self.conn_id) is self.websocket:
            sockets.pop(self.conn_id, None)
            if not sockets:
                _connections.pop(self.identity, None)
        presence_service.connection_closed(self.identity, self.conn_id)

    def drain(self) -> None:
        while not self.queue.empty():
            _text, size, ack, _admitted_at = self.queue.get_nowait()
            self.frames -= 1
            self.bytes -= size
            _complete(ack, False)
            if _text is not None:
                _push_stats["discarded_frames"] += 1
            self.queue.task_done()
        while self.presence:
            _identity, (_text, size, ack, _admitted_at) = self.presence.popitem(last=False)
            self.presence_frames -= 1
            self.bytes -= size
            _complete(ack, False)
            _push_stats["discarded_frames"] += 1

    def stop(self) -> None:
        self.closed = True
        self.drain()
        if self.current is not None:
            _complete(self.current[2], False)
        if not self.task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if self.task is not current:
                self.task.cancel()

    async def run(self) -> None:
        try:
            await self.ready.wait()
            while not self.closed:
                if not self.queue.empty():
                    frame = self.queue.get_nowait()
                    is_presence = False
                elif self.presence:
                    _identity, frame = self.presence.popitem(last=False)
                    is_presence = True
                else:
                    self.wake.clear()
                    await self.wake.wait()
                    continue
                self.current = frame
                self.current_presence = is_presence
                text, size, ack, admitted_at = frame
                try:
                    # Presence is the latest admitted state, not an ordered
                    # history event. Keep it until sent/replaced; critical
                    # frames still have their short recovery deadline.
                    if not is_presence and monotonic() - admitted_at > MAX_QUEUE_AGE_SECONDS:
                        _push_stats["expired_disconnects"] += 1
                        _push_stats["discarded_frames"] += 1
                        self.closed = True
                        self.remove_registration()
                        self.drain()
                        await close_quietly(self.websocket, 1013)
                        _complete(ack, False)
                        return
                    await asyncio.wait_for(self.websocket.send_text(text), timeout=SEND_TIMEOUT_SECONDS)
                    _push_stats["sent"] += 1
                    _complete(ack, True)
                except asyncio.CancelledError:
                    _push_stats["discarded_frames"] += 1
                    _complete(ack, False)
                    raise
                except Exception:
                    _push_stats["failed"] += 1
                    _push_stats["discarded_frames"] += 1
                    self.closed = True
                    self.remove_registration()
                    self.drain()
                    # Synchronous delivery callers retain their completed-send
                    # contract, including observing a failed socket's closure.
                    await close_quietly(self.websocket, 1011)
                    _complete(ack, False)
                    return
                finally:
                    _complete(ack, False)
                    if is_presence:
                        self.presence_frames -= 1
                    else:
                        self.frames -= 1
                    self.bytes -= size
                    self.current = None
                    self.current_presence = False
                    if not is_presence:
                        self.queue.task_done()
        finally:
            self.closed = True
            self.drain()
            self.remove_registration()


def register(identity: str, conn_id: str, websocket: Any, *, awaiting_hello: bool = False) -> list[tuple[str, Any]]:
    """Register a socket, returning evictions for the caller's 4008 closure."""
    unregister(identity, conn_id)
    sockets = _connections.setdefault(identity, {})
    sockets[conn_id] = websocket
    if awaiting_hello:
        _writers[(identity, conn_id)] = _SocketWriter(identity, conn_id, websocket, awaiting_hello=True)
    evicted = []
    while len(sockets) > MAX_SOCKETS_PER_IDENTITY:
        oldest = next(iter(sockets))
        evicted.append((oldest, sockets[oldest]))
        unregister(identity, oldest)
    return evicted


def unregister(identity: str, conn_id: str) -> None:
    writer = _writers.pop((identity, conn_id), None)
    if writer is not None:
        writer.stop()
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


def outbound_snapshot() -> dict[str, Any]:
    writers = list(_writers.values())
    return {**_push_stats, "writers": len(writers),
            "pending_frames": sum(item.frames + item.presence_frames for item in writers),
            "pending_critical_frames": sum(item.frames for item in writers),
            "pending_presence_entries": sum(item.presence_frames for item in writers),
            "pending_bytes": sum(item.bytes for item in writers), "closing": len(_closing_tasks),
            "writer_tasks": len(_writer_tasks),
            "max_frames_per_socket": MAX_OUTBOUND_FRAMES, "max_bytes_per_socket": MAX_OUTBOUND_BYTES,
            "max_presence_entries_per_socket": MAX_PRESENCE_ENTRIES,
            "max_queue_age_seconds": MAX_QUEUE_AGE_SECONDS}


def reset_for_tests() -> None:
    for identity, sockets in list(_connections.items()):
        for conn_id in list(sockets):
            unregister(identity, conn_id)
    for task in list(_closing_tasks):
        task.cancel()
    _closing_tasks.clear()
    for task in list(_writer_tasks):
        task.cancel()
    _writer_tasks.clear()
    for key in _push_stats:
        _push_stats[key] = 0


async def close_quietly(websocket: Any, code: int = 1011) -> None:
    try:
        await asyncio.wait_for(websocket.close(code=code), timeout=SEND_TIMEOUT_SECONDS)
    except Exception:
        pass


def _enqueue(identity: str, conn_id: str, text: str, size: int, *, confirmed: bool, presence_key: str | None = None):
    websocket = _connections.get(identity, {}).get(conn_id)
    if websocket is None:
        return False, None
    key = (identity, conn_id)
    writer = _writers.get(key)
    if writer is None:
        writer = _SocketWriter(identity, conn_id, websocket)
        _writers[key] = writer
    return writer.enqueue(text, size, confirmed=confirmed, presence_key=presence_key)


def _presence_key(event: dict[str, Any]) -> str | None:
    value = event.get("identity") if event.get("type") == "presence" else None
    return value if isinstance(value, str) and value else None


def queue_connection(identity: str, conn_id: str, event: dict[str, Any], *, initial: bool = False):
    """Queue hello/control immediately, returning its completion future."""
    text = json.dumps(event, ensure_ascii=False)
    size = len(text.encode("utf-8"))
    if initial:
        writer = _writers.get((identity, conn_id))
        accepted, ack = writer.complete_hello(text, size) if writer else (False, None)
    else:
        accepted, ack = _enqueue(identity, conn_id, text, size, confirmed=True, presence_key=_presence_key(event))
    if accepted:
        return ack
    completed = asyncio.get_running_loop().create_future()
    completed.set_result(False)
    return completed


async def send_connection(identity: str, conn_id: str, event: dict[str, Any]) -> bool:
    return await queue_connection(identity, conn_id, event)


async def deliver_local(identities: Iterable[str], event: dict[str, Any]) -> int:
    text = json.dumps(event, ensure_ascii=False)
    size = len(text.encode("utf-8"))
    presence_key = _presence_key(event)
    completions = []
    for identity in set(identities):
        for conn_id in list(_connections.get(identity, {})):
            accepted, ack = _enqueue(identity, conn_id, text, size, confirmed=True, presence_key=presence_key)
            if accepted:
                completions.append(ack)
    if not completions:
        return 0
    return sum(await asyncio.gather(*completions))


async def enqueue_committed_events(events: Iterable[tuple[Iterable[str], dict[str, Any]]]) -> int:
    """After COMMIT, admit pushes to bounded writers without waiting on client I/O.

    The message/read state is authoritative in PostgreSQL. A failed/full writer
    closes explicitly so reconnect can recover the committed history by cursor.
    """
    if not is_app_process():
        return await push_events(events)
    admitted = 0
    for identities, event in events or ():
        if not isinstance(event, dict) or not event.get("type"):
            continue
        text = json.dumps(event, ensure_ascii=False)
        size = len(text.encode("utf-8"))
        presence_key = _presence_key(event)
        for identity in set(identities):
            for conn_id in list(_connections.get(identity, {})):
                accepted, _ack = _enqueue(identity, conn_id, text, size, confirmed=False, presence_key=presence_key)
                admitted += int(accepted)
    return admitted


async def stop_outbound_writers() -> None:
    sockets = [socket for connections in _connections.values() for socket in connections.values()]
    tasks = list(_writer_tasks)
    for identity, connections in list(_connections.items()):
        for conn_id in list(connections):
            unregister(identity, conn_id)
    for socket in sockets:
        _track_close(socket, 1012)
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(*list(_closing_tasks), return_exceptions=True)


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
    per ≤ 250 pairs) instead of one request per pair. Group messages share a
    frame only among recipients with the same mine/muted view.
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
