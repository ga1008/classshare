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
from collections import OrderedDict
from dataclasses import dataclass
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable

from .social_common import as_int, ensure_schema, in_clause, now_iso, split_identity

SOURCES = ("social_ws", "classroom_ws")
OFFLINE_GRACE_SECONDS = 30.0
LAST_SEEN_WRITE_INTERVAL_SECONDS = 60.0
MAILBOX_CAPACITY = 1024
PERSIST_WORKERS = 2
NOTIFY_WORKERS = 2
PERSIST_BATCH_SIZE = 64
SHUTDOWN_TIMEOUT_SECONDS = 15.0

Listener = Callable[[str, bool, str], Awaitable[None]]

_registry: dict[str, dict[str, Any]] = {}
_grace_tasks: dict[str, asyncio.Task] = {}
_last_written: OrderedDict[str, float] = OrderedDict()
_listeners: list[Listener] = []
_runtime: _PresenceRuntime | None = None


@dataclass
class _Slot:
    stamp: str = ""
    version: int = 0
    confirmed: int = 0
    writing: bool = False
    retry_at: float = 0.0
    failures: int = 0
    notice: tuple[bool, str] | None = None
    notice_version: int = 0
    notifying: bool = False
    notice_retry_at: float = 0.0
    notice_failures: int = 0
    queued_at: float = 0.0


class _PresenceRuntime:
    """Bounded, best-effort display bookkeeping; never an attendance ledger.

    One identity owns one slot including its in-flight work. Actual concurrent
    futures are retained until the DB thread returns, even on shutdown timeout.
    Notification listeners may themselves use the social executor: at most two
    listeners plus two persistence jobs can induce DB work here.
    """
    def __init__(self, loop):
        self.loop = loop
        self.slots: OrderedDict[str, _Slot] = OrderedDict()
        self.writes: dict[Any, list[tuple[str, int, str]]] = {}
        self.notices: dict[asyncio.Task, tuple[str, int]] = {}
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.closing = False
        self.deadline = float("inf")
        self.clean = None
        self.last_log = float("-inf")
        self.stats = {key: 0 for key in (
            "coalesced", "overflow_count", "unpersisted_final_close_count",
            "write_failures", "notify_failures", "unknown_commit_count", "retries",
            "shutdown_timeouts", "reconciled", "shutdown_notices_discarded")}
        self.last_error = None
        self.last_success_at = None
        self.last_overflow_at = None
        self.reconcile_after: str | None = None

    def warn(self, reason):
        self.last_error = reason
        if time.monotonic() - self.last_log >= 30:
            self.last_log = time.monotonic()
            print(f"[PRESENCE] degraded: {reason}; pending_identities={len(self.slots)}", flush=True)

    def slot(self, identity):
        slot = self.slots.get(identity)
        if slot is None:
            if len(self.slots) >= MAILBOX_CAPACITY:
                self.stats["overflow_count"] += 1
                self.last_overflow_at = now_iso()
                self.warn("mailbox_full")
                return None
            slot = self.slots[identity] = _Slot(queued_at=time.monotonic())
        return slot

    def write(self, identity, stamp, *, final=False):
        if self.closing:
            # Existing slots can still retain a final close while draining.
            if identity not in self.slots:
                self.stats["unpersisted_final_close_count"] += int(final)
                self.warn("write_after_shutdown_admission_closed")
                return False
        slot = self.slot(identity)
        if slot is None:
            self.stats["unpersisted_final_close_count"] += int(final)
            return False
        self.stats["coalesced"] += int(slot.version > slot.confirmed)
        slot.stamp = max(slot.stamp, stamp)
        slot.version += 1
        self.kick()
        return True

    def notify(self, identity, online, stamp):
        if self.closing:
            return
        slot = self.slot(identity)
        if slot is not None:
            self.stats["coalesced"] += int(slot.notice is not None)
            slot.notice = bool(online), stamp
            slot.notice_version += 1
            self.kick()

    def kick(self):
        self.wake.set()
        if self.task is None or self.task.done():
            self.task = self.loop.create_task(self.run(), name="presence-mailbox")

    def completed(self, _future):
        # Called by an executor thread. Never clear a slot from that thread.
        try:
            self.loop.call_soon_threadsafe(self.wake.set)
        except RuntimeError:  # Loop teardown is not DB completion/cancellation.
            pass

    def reap(self):
        for future, batch in list(self.writes.items()):
            if not future.done():
                continue
            del self.writes[future]
            try:
                outcome = future.result()
            except Exception as exc:
                outcome = {"ok": False, "error": type(exc).__name__, "unknown_commit": False}
            for identity, version, _stamp in batch:
                slot = self.slots[identity]
                slot.writing = False
                self.slots.move_to_end(identity)
                if outcome["ok"]:
                    slot.confirmed = version
                    slot.failures = 0
                    slot.retry_at = 0.0
                    _remember_write_confirmation(identity)
                else:
                    slot.failures += 1
                    slot.retry_at = time.monotonic() + min(30.0, 2 ** min(slot.failures - 1, 5))
            if outcome["ok"]:
                self.last_success_at = now_iso()
            else:
                self.stats["write_failures"] += 1
                self.stats["unknown_commit_count"] += int(outcome.get("unknown_commit", False))
                self.warn("last_seen_" + str(outcome.get("error", "failed")))
        for task, (identity, version) in list(self.notices.items()):
            if not task.done():
                continue
            del self.notices[task]
            slot = self.slots[identity]
            slot.notifying = False
            self.slots.move_to_end(identity)
            # We never cancel these tasks: a listener may own a real DB future.
            ok = not task.cancelled() and task.exception() is None and task.result()
            if ok:
                if slot.notice_version == version:
                    slot.notice = None
                slot.notice_failures = 0
                slot.notice_retry_at = 0.0
            else:
                self.stats["notify_failures"] += 1
                slot.notice_failures += 1
                slot.notice_retry_at = time.monotonic() + min(30.0, 2 ** min(slot.notice_failures - 1, 5))
                self.warn("listener_failed")
            if self.closing:
                slot.notice = None
        for identity, slot in list(self.slots.items()):
            if slot.confirmed == slot.version and slot.notice is None and not slot.writing and not slot.notifying:
                del self.slots[identity]
                if identity not in _registry and identity not in _grace_tasks:
                    _last_written.pop(identity, None)

    def reconcile(self):
        """Give still-connected overflow identities a bounded, round-robin retry."""
        if self.closing or len(self.slots) >= MAILBOX_CAPACITY or not _registry:
            return
        identities = list(_registry)
        start = identities.index(self.reconcile_after) + 1 if self.reconcile_after in _registry else 0
        for offset in range(min(len(identities), PERSIST_BATCH_SIZE)):
            identity = identities[(start + offset) % len(identities)]
            self.reconcile_after = identity
            entry = _registry[identity]
            if not entry.pop("presence_write_rejected", False):
                continue
            if not self.write(identity, entry["last_seen_at"]):
                entry["presence_write_rejected"] = True
                break
            self.notify(identity, is_connected(identity), entry["last_seen_at"])
            self.stats["reconciled"] += 1

    async def run(self):
        from .social_common import social_executor
        while True:
            self.wake.clear()
            self.reap()
            self.reconcile()
            now = time.monotonic()
            if now < self.deadline:
                while len(self.writes) < PERSIST_WORKERS:
                    batch = [(identity, slot.version, slot.stamp) for identity, slot in self.slots.items()
                             if not slot.writing and slot.version > slot.confirmed and slot.retry_at <= now][:PERSIST_BATCH_SIZE]
                    if not batch:
                        break
                    for identity, _version, _stamp in batch:
                        slot = self.slots[identity]
                        slot.writing = True
                        self.stats["retries"] += int(slot.failures > 0)
                    try:
                        future = social_executor().submit(_persist_last_seen_batch, [(key, stamp) for key, _, stamp in batch])
                    except Exception as exc:
                        for identity, _version, _stamp in batch:
                            self.slots[identity].writing = False
                            self.slots[identity].failures += 1
                            self.slots[identity].retry_at = now + 1.0
                        self.stats["write_failures"] += 1
                        self.warn("executor_admission_" + type(exc).__name__)
                        break
                    self.writes[future] = batch
                    future.add_done_callback(self.completed)
            if not self.closing:
                for identity, slot in self.slots.items():
                    if len(self.notices) >= NOTIFY_WORKERS:
                        break
                    if slot.notice is not None and not slot.notifying and slot.notice_retry_at <= now:
                        slot.notifying = True
                        task = self.loop.create_task(_notify(identity, *slot.notice), name="presence-notify")
                        self.notices[task] = identity, slot.notice_version
                        task.add_done_callback(self.completed)
            reconcile_pending = not self.closing and any(entry.get("presence_write_rejected") for entry in _registry.values())
            if not self.writes and not self.notices and ((not self.slots and not reconcile_pending) or (self.closing and now >= self.deadline)):
                return
            try:
                await asyncio.wait_for(self.wake.wait(), .25)
            except asyncio.TimeoutError:
                pass

    def snapshot(self):
        dirty = sum(slot.version > slot.confirmed for slot in self.slots.values())
        return {**self.stats, "mailbox_capacity": MAILBOX_CAPACITY, "pending_identities": len(self.slots),
                "pending_writes": dirty, "inflight_db": len(self.writes), "inflight_notify": len(self.notices),
                "pending_notifications": sum(slot.notice is not None for slot in self.slots.values()),
                "oldest_pending_seconds": round(max((time.monotonic() - s.queued_at for s in self.slots.values()), default=0), 3),
                "last_error": self.last_error, "last_success_at": self.last_success_at,
                "last_overflow_at": self.last_overflow_at, "shutdown_clean": self.clean,
                "degraded": bool(self.stats["overflow_count"] or self.stats["unpersisted_final_close_count"]
                                 or self.stats["shutdown_timeouts"] or any(s.failures or s.notice_failures for s in self.slots.values()))}


def start_presence_runtime() -> None:
    global _runtime
    loop = asyncio.get_running_loop()
    if _runtime is not None:
        _runtime.reap()
        if _runtime.loop is loop and not _runtime.closing:
            return
        if _runtime.writes or _runtime.notices or _runtime.slots:
            raise RuntimeError("Previous presence runtime has unconfirmed work")
        # Connections belong to the previous event loop/lifecycle. A clean
        # restart must not resurrect its sockets as currently online.
        for task in _grace_tasks.values():
            task.cancel()
        _grace_tasks.clear()
        _registry.clear()
        _last_written.clear()
    _runtime = _PresenceRuntime(loop)


def _current_runtime():
    if _runtime is None or _runtime.loop is not asyncio.get_running_loop():
        start_presence_runtime()
    return _runtime


def runtime_snapshot() -> dict[str, Any]:
    return {"grace_tasks": len(_grace_tasks), "recent_confirmations": len(_last_written), **(_runtime.snapshot() if _runtime else {
        "pending_identities": 0, "inflight_db": 0, "inflight_notify": 0, "degraded": False, "shutdown_clean": None})}


async def stop_presence_runtime(timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Return unclean on deadline; never cancel a DB-owning future or close its pool."""
    runtime = _runtime
    grace = list(_grace_tasks.values())
    _grace_tasks.clear()
    for task in grace:
        task.cancel()
    await asyncio.gather(*grace, return_exceptions=True)
    if runtime is None:
        return {"clean": True, **runtime_snapshot()}
    if not runtime.closing:
        # Preserve the last observed activity, never invent activity at shutdown.
        for identity, entry in _registry.items():
            runtime.write(identity, entry["last_seen_at"], final=True)
    runtime.closing = True
    runtime.deadline = time.monotonic() + max(0.0, timeout)
    for slot in runtime.slots.values():
        if slot.notice is not None and not slot.notifying:
            runtime.stats["shutdown_notices_discarded"] += 1
            slot.notice = None
    runtime.kick()
    # asyncio.wait does not cancel pending tasks on timeout. The pump retains
    # actual executor Futures and continues observing already-started work.
    await asyncio.wait({runtime.task}, timeout=max(0.0, timeout))
    runtime.reap()
    runtime.clean = not runtime.writes and not runtime.notices and not runtime.slots
    if not runtime.clean:
        runtime.stats["shutdown_timeouts"] += 1
        runtime.warn("shutdown_incomplete_pool_must_remain_open")
        print(f"[PRESENCE] shutdown FAILED: {runtime.snapshot()}", flush=True)
    return {"clean": runtime.clean, **runtime_snapshot()}


def add_listener(listener: Listener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def reset_for_tests() -> None:
    global _runtime
    if _runtime is not None:
        _runtime.reap()
        if _runtime.writes or _runtime.notices:
            raise RuntimeError("Drain real presence work before resetting tests")
        if _runtime.task is not None and not _runtime.task.done():
            _runtime.task.cancel()  # Only the coordinator; no DB-owning work remains.
        _runtime = None
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


async def _notify(identity: str, online: bool, last_seen_at: str) -> bool:
    successful = True
    for listener in list(_listeners):
        try:
            await listener(identity, online, last_seen_at)
        except Exception as exc:  # noqa: BLE001 - one failing listener must not stop the others
            successful = False
            print(f"[PRESENCE] listener failed for {identity}: {exc!r}")
    return successful


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
    if became_online and _running_loop() is not None:
        _current_runtime().notify(identity, True, entry["last_seen_at"])
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
        _last_written.pop(identity, None)
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
    _last_written.pop(identity, None)
    _current_runtime().notify(identity, False, last_seen)


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

def _remember_write_confirmation(identity: str) -> None:
    _last_written[identity] = time.monotonic()
    _last_written.move_to_end(identity)
    while len(_last_written) > MAILBOX_CAPACITY:
        _last_written.popitem(last=False)


def touch(identity: str, *, force: bool = False) -> None:
    """Note activity; persists ``user_presence`` at most once per interval per identity."""
    entry = _registry.get(identity)
    stamp = now_iso()
    if entry is not None:
        entry["last_seen_at"] = stamp
    now = time.monotonic()
    previous = _last_written.get(identity)
    slot = _runtime.slots.get(identity) if _runtime is not None else None
    pending = slot is not None and slot.version > slot.confirmed
    if not force and not pending and previous is not None and now - previous < LAST_SEEN_WRITE_INTERVAL_SECONDS:
        return
    loop = _running_loop()
    if loop is None:
        if persist_last_seen(identity, stamp):
            _remember_write_confirmation(identity)
        return
    accepted = _current_runtime().write(identity, stamp, final=force)
    if entry is not None:
        entry["presence_write_rejected"] = not accepted


def _persist_last_seen_batch(items: list[tuple[str, str]]) -> dict[str, Any]:
    """One bounded SQL batch; an ambiguous commit is safe to replay monotonically.

    SQL/checkout budgets bound ordinary contention, not an unresponsive driver.
    Shutdown consequently tracks the real Future instead of promising that
    cancelling an asyncio waiter can terminate a database thread.
    """
    from ..database import get_db_connection
    from ..db.connection import get_configured_db_engine
    from ..db.postgres import connect_postgres
    conn = None
    committing = False
    outcome = {"ok": False, "unknown_commit": False, "error": "not_started"}
    try:
        postgres = get_configured_db_engine() == "postgres"
        conn = connect_postgres(checkout_timeout=1.0) if postgres else get_db_connection()
        if postgres:
            # Schema is initialized at startup; do not run DDL in this hot path.
            conn.execute("""SELECT
                set_config('lock_timeout', CASE WHEN current_setting('lock_timeout')::interval
                    BETWEEN interval '1 millisecond' AND interval '250 milliseconds'
                    THEN current_setting('lock_timeout') ELSE '250ms' END, true),
                set_config('statement_timeout', CASE WHEN current_setting('statement_timeout')::interval
                    BETWEEN interval '1 millisecond' AND interval '1000 milliseconds'
                    THEN current_setting('statement_timeout') ELSE '1000ms' END, true)""")
        else:
            ensure_schema(conn)
        marks = ",".join("(?, ?)" for _ in items)
        conn.execute("INSERT INTO user_presence (identity, last_seen_at) VALUES " + marks +
                     " ON CONFLICT (identity) DO UPDATE SET last_seen_at = CASE "
                     "WHEN excluded.last_seen_at > user_presence.last_seen_at "
                     "THEN excluded.last_seen_at ELSE user_presence.last_seen_at END",
                     tuple(value for item in items for value in item))
        committing = True
        conn.commit()
        outcome = {"ok": True}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        outcome = {"ok": False, "unknown_commit": committing, "error": type(exc).__name__}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                outcome = {"ok": False, "unknown_commit": committing, "error": type(exc).__name__}
    return outcome


def persist_last_seen(identity: str, stamp: str) -> bool:
    outcome = _persist_last_seen_batch([(identity, stamp)])
    if not outcome["ok"]:
        print(f"[PRESENCE] last_seen write failed: {outcome['error']}")
    return bool(outcome["ok"])


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
