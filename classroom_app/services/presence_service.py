"""统一在线 — the single in-process truth of "is this person online" (social doc §8).

Registry ``identity -> {connections: {conn_id: source}, last_seen_at}``; sources
are ``social_ws`` (the global /ws/social socket) and ``classroom_ws`` (the
classroom room sockets, reported by ``MultiRoomConnectionManager``). Online
means at least one connection; after the last one closes a 30 s grace passes
before the offline change is announced, so a page reload never flickers.
``last_seen_at`` is written to ``user_presence`` at most once per 60 s per
identity. 散修 are simulated: online while ``status='active'`` and inside one
of their ``active_hours``.

Single-worker assumption: production runs one app worker, so this module-level
registry is complete. Other processes (scheduler) only read ``user_presence``.
Change listeners (``add_listener``) receive ``(identity, online, last_seen_at)``;
``social_push_service`` fans that out to friends and co-group members.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable

from .social_common import as_int, ensure_schema, in_clause, now_iso, split_identity

SOURCES = ("social_ws", "classroom_ws")
OFFLINE_GRACE_SECONDS = 30.0
LAST_SEEN_WRITE_INTERVAL_SECONDS = 60.0

Listener = Callable[[str, bool, str], Awaitable[None]]

_registry: dict[str, dict[str, Any]] = {}
_grace_tasks: dict[str, asyncio.Task] = {}
_last_written: dict[str, float] = {}
_listeners: list[Listener] = []
_background: set[asyncio.Task] = set()


def add_listener(listener: Listener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def reset_for_tests() -> None:
    for task in list(_grace_tasks.values()):
        task.cancel()
    _registry.clear()
    _grace_tasks.clear()
    _last_written.clear()


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _spawn(coro: Awaitable[Any]) -> None:
    loop = _running_loop()
    if loop is None:
        close = getattr(coro, "close", None)
        if close:
            close()
        return
    task = loop.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _notify(identity: str, online: bool, last_seen_at: str) -> None:
    for listener in list(_listeners):
        try:
            await listener(identity, online, last_seen_at)
        except Exception as exc:  # noqa: BLE001 - one failing listener must not stop the others
            print(f"[PRESENCE] listener failed for {identity}: {exc!r}")


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def connection_opened(identity: str, conn_id: str, source: str) -> bool:
    """Register a connection; True when the identity just came online (announced)."""
    if not identity or not conn_id:
        return False
    entry = _registry.setdefault(identity, {"connections": {}, "last_seen_at": now_iso()})
    in_grace = identity in _grace_tasks
    if in_grace:
        _grace_tasks.pop(identity).cancel()
    became_online = not entry["connections"] and not in_grace
    entry["connections"][conn_id] = source if source in SOURCES else "social_ws"
    touch(identity)
    if became_online:
        _spawn(_notify(identity, True, entry["last_seen_at"]))
    return became_online


def connection_closed(identity: str, conn_id: str) -> None:
    """Drop a connection; the offline change is announced after the grace period."""
    entry = _registry.get(identity)
    if not entry or conn_id not in entry["connections"]:
        return
    entry["connections"].pop(conn_id, None)
    if entry["connections"]:
        touch(identity)
        return
    touch(identity, force=True)
    loop = _running_loop()
    if loop is None:
        _registry.pop(identity, None)
        return
    if identity not in _grace_tasks:
        _grace_tasks[identity] = loop.create_task(_offline_after_grace(identity))


async def _offline_after_grace(identity: str) -> None:
    try:
        await asyncio.sleep(OFFLINE_GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    _grace_tasks.pop(identity, None)
    entry = _registry.get(identity)
    if entry and entry["connections"]:
        return
    last_seen = entry["last_seen_at"] if entry else now_iso()
    _registry.pop(identity, None)
    await _notify(identity, False, last_seen)


def is_connected(identity: str) -> bool:
    """Has a live connection, or is inside the offline grace window."""
    entry = _registry.get(identity)
    return bool(entry and entry["connections"]) or identity in _grace_tasks


def connected_identities() -> list[str]:
    return sorted(identity for identity in _registry if is_connected(identity))


def snapshot() -> dict[str, Any]:
    """Read-only view for the super-admin monitor."""
    by_source = {source: 0 for source in SOURCES}
    for entry in _registry.values():
        for source in entry["connections"].values():
            by_source[source] = by_source.get(source, 0) + 1
    online = connected_identities()
    return {
        "online_count": len(online),
        "connections": sum(len(entry["connections"]) for entry in _registry.values()),
        "by_source": by_source,
        "in_grace": len(_grace_tasks),
        "identities": online[:500],
    }


# ---------------------------------------------------------------------------
# last_seen_at persistence (throttled)
# ---------------------------------------------------------------------------

def touch(identity: str, *, force: bool = False) -> None:
    """Note activity; persists ``user_presence`` at most once per interval per identity."""
    entry = _registry.get(identity)
    stamp = now_iso()
    if entry is not None:
        entry["last_seen_at"] = stamp
    now = time.monotonic()
    previous = _last_written.get(identity)
    if not force and previous is not None and now - previous < LAST_SEEN_WRITE_INTERVAL_SECONDS:
        return
    _last_written[identity] = now
    loop = _running_loop()
    if loop is None:
        persist_last_seen(identity, stamp)
        return
    from .social_common import social_executor

    loop.run_in_executor(social_executor(), persist_last_seen, identity, stamp)


def persist_last_seen(identity: str, stamp: str) -> None:
    from ..database import get_db_connection

    try:
        with get_db_connection() as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO user_presence (identity, last_seen_at) VALUES (?, ?) "
                "ON CONFLICT (identity) DO UPDATE SET last_seen_at = excluded.last_seen_at",
                (identity, stamp),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - presence bookkeeping never breaks a request
        print(f"[PRESENCE] last_seen write failed for {identity}: {exc!r}")


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _in_active_hours(hours: Iterable[Any], now: datetime) -> bool:
    from .wanderer_behavior_service import _in_active_hours as wanderer_in_active_hours

    return wanderer_in_active_hours(hours, now)


def simulated_wanderer_online(conn: Any, student_ids: Iterable[int]) -> set[int]:
    """Student ids of 散修 that count as online right now (active + inside active_hours)."""
    from .wanderer_identity_service import STATUS_ACTIVE, load_wanderers_by_student_ids

    from ..time_utils import local_now

    wanderers = load_wanderers_by_student_ids(conn, list(student_ids))
    now = local_now()
    online: set[int] = set()
    for student_id, wanderer in wanderers.items():
        hours = (wanderer.get("schedule") or {}).get("active_hours") or []
        if str(wanderer.get("status") or "") == STATUS_ACTIVE and _in_active_hours(hours, now):
            online.add(int(student_id))
    return online


def _stored_last_seen(conn: Any, identities: list[str]) -> dict[str, str]:
    if not identities:
        return {}
    ensure_schema(conn)
    marks, params = in_clause(identities)
    rows = conn.execute(
        f"SELECT identity, last_seen_at FROM user_presence WHERE identity IN ({marks})", params,
    ).fetchall()
    return {str(row["identity"]): str(row["last_seen_at"] or "") for row in rows}


def presence_map(conn: Any, identities: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{identity: {online, last_seen_at}}`` from the registry, 散修 simulation and ``user_presence``."""
    wanted = sorted({str(item) for item in identities if item})
    result: dict[str, dict[str, Any]] = {}
    student_ids = [split_identity(item)[1] for item in wanted if item.startswith("student:")]
    simulated = simulated_wanderer_online(conn, student_ids) if student_ids else set()
    stored = _stored_last_seen(conn, [item for item in wanted if not is_connected(item)])
    for identity in wanted:
        if is_connected(identity):
            seen = _registry.get(identity, {}).get("last_seen_at") or now_iso()
            result[identity] = {"online": True, "last_seen_at": seen}
            continue
        role, pk = split_identity(identity)
        if role == "student" and as_int(pk) in simulated:
            result[identity] = {"online": True, "last_seen_at": now_iso()}
            continue
        result[identity] = {"online": False, "last_seen_at": stored.get(identity) or None}
    return result


def is_online(conn: Any, identity: str) -> bool:
    return bool(presence_map(conn, [identity]).get(identity, {}).get("online"))
