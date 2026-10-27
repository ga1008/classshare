"""Shared helpers for the social domain (docs/social-friends-groups-2026-10-27.md).

Identities are ``role:pk`` (``student:12``, ``teacher:3``); 散修 are students.
Conversation keys are ``dm:<a>|<b>`` (sorted identities) or ``group:<id>``.
Every service takes an open connection and never commits; callers own the
transaction. Push events are collected into ``events`` (a list of
``(identities, event)``) and delivered by the caller after commit.

Single-worker assumption: production runs one app worker, so in-process
registries (presence, websocket connections, upload throttles) are the truth.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from ..db.schema_social import ensure_social_schema
from .message_center_service import build_user_identity, parse_identity

SYSTEM_SENDER = "system"
DM_PREFIX = "dm:"
GROUP_PREFIX = "group:"


class SocialError(Exception):
    """A user-facing refusal; ``status_code`` maps to the HTTP response."""

    def __init__(self, detail: str, status_code: int = 400, *, retry_after: int = 0):
        super().__init__(detail)
        self.detail = detail
        self.status_code = int(status_code)
        self.retry_after = int(retry_after or 0)


# Social DB work runs on its own small pool so a burst of chat traffic cannot
# starve the default executor that classroom chat and other routes share.
SOCIAL_DB_WORKERS = 4
_DB_EXECUTOR: Optional[ThreadPoolExecutor] = None


def social_executor() -> ThreadPoolExecutor:
    global _DB_EXECUTOR
    if _DB_EXECUTOR is None:
        _DB_EXECUTOR = ThreadPoolExecutor(max_workers=SOCIAL_DB_WORKERS, thread_name_prefix="social-db")
    return _DB_EXECUTOR


def drain_social_executor() -> None:
    """Wait for queued social DB work and drop the pool (tests; a fresh pool is created on demand)."""
    global _DB_EXECUTOR
    executor, _DB_EXECUTOR = _DB_EXECUTOR, None
    if executor is not None:
        executor.shutdown(wait=True)


async def run_db(fn: Callable[..., Any], *args: Any) -> Any:
    """Run blocking social DB work on the dedicated executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(social_executor(), functools.partial(fn, *args))


def serialize_on(conn: Any, namespace: str, values: Iterable[str]) -> None:
    """Transaction-scoped mutual exclusion on ``namespace:value`` keys (sorted, taken at once).

    PostgreSQL: ``pg_advisory_xact_lock`` per key (released at commit/rollback).
    SQLite: a write transaction (``BEGIN IMMEDIATE``) serialises all writers.
    """
    from ..db.connection import get_configured_db_engine

    keys = sorted({f"{namespace}:{value}" for value in values if value})
    if not keys:
        return
    if get_configured_db_engine() == "postgres":
        for key in keys:
            lock_key = int.from_bytes(hashlib.sha256(("lanshare:" + key).encode("utf-8")).digest()[:8], "big",
                                      signed=True)
            conn.execute("SELECT pg_advisory_xact_lock(?)", (lock_key,))
        return
    if not getattr(conn, "in_transaction", True):
        conn.execute("BEGIN IMMEDIATE")


def env_int(name: str, default: int, *, low: int = 0, high: int = 10**12) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def env_float(name: str, default: float, *, low: float = 0.0, high: float = 1e12) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """UTC ISO-8601 with fixed microsecond precision, so stored stamps compare correctly as text."""
    return utc_now().isoformat(timespec="microseconds")


def iso_after(**delta: float) -> str:
    return (utc_now() + timedelta(**delta)).isoformat(timespec="microseconds")


def iso_before(**delta: float) -> str:
    return (utc_now() - timedelta(**delta)).isoformat(timespec="microseconds")


def utc_date() -> str:
    return utc_now().date().isoformat()


def utc_day_start_iso() -> str:
    today = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today.isoformat(timespec="microseconds")


def parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def loads_list(raw: Any) -> list[Any]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_schema(conn: Any) -> None:
    ensure_social_schema(conn)


# ---------------------------------------------------------------------------
# Identities and conversation keys
# ---------------------------------------------------------------------------

def user_identity(user: dict) -> str:
    """``role:pk`` of the authenticated user dict; 401 when it is not a person."""
    try:
        return build_user_identity(str(user.get("role") or ""), user.get("id"))
    except ValueError as exc:
        raise SocialError("当前登录身份无效，请重新登录。", 401) from exc


def normalize_identity(identity: Any) -> str:
    """A validated ``student:<pk>`` / ``teacher:<pk>``; 400 for anything else (AI, garbage)."""
    try:
        parsed = parse_identity(str(identity or "").strip())
    except ValueError as exc:
        raise SocialError("用户标识无效。", 400) from exc
    if parsed["role"] not in {"student", "teacher"}:
        raise SocialError("该用户不能添加为好友。", 400)
    return f"{parsed['role']}:{int(parsed['user_pk'])}"


def split_identity(identity: str) -> tuple[str, int]:
    role, _, pk = str(identity).partition(":")
    return role, as_int(pk)


def pair(identity_a: str, identity_b: str) -> tuple[str, str]:
    first, second = sorted((identity_a, identity_b))
    return first, second


def dm_key(identity_a: str, identity_b: str) -> str:
    first, second = pair(identity_a, identity_b)
    return f"{DM_PREFIX}{first}|{second}"


def group_key(group_id: int) -> str:
    return f"{GROUP_PREFIX}{int(group_id)}"


def parse_conversation_key(key: Any) -> dict[str, Any]:
    """``{'type': 'dm', 'parties': (a, b)}`` or ``{'type': 'group', 'group_id': n}``; 404 otherwise."""
    text = str(key or "").strip()
    if text.startswith(DM_PREFIX):
        first, sep, second = text[len(DM_PREFIX):].partition("|")
        if sep and first and second:
            try:
                parties = (normalize_identity(first), normalize_identity(second))
            except SocialError as exc:
                raise SocialError("会话不存在。", 404) from exc
            if parties[0] < parties[1] and text == dm_key(*parties):
                return {"type": "dm", "key": text, "parties": parties}
    elif text.startswith(GROUP_PREFIX):
        group_id = as_int(text[len(GROUP_PREFIX):], 0)
        if group_id > 0 and text == group_key(group_id):
            return {"type": "group", "key": text, "group_id": group_id}
    raise SocialError("会话不存在。", 404)


def dm_peer(parsed_key: dict[str, Any], me: str) -> str:
    first, second = parsed_key["parties"]
    if me == first:
        return second
    if me == second:
        return first
    raise SocialError("无权访问该会话。", 403)


# ---------------------------------------------------------------------------
# Blacklist: private_message_blocks (scope 'all', shared with classroom private
# messages) + social_blocks (scope 'social': people the classroom list refuses,
# i.e. teachers). Social code always consults both; classroom code only the first.
# ---------------------------------------------------------------------------

_BLOCK_TABLES = ("private_message_blocks", "social_blocks")


def blocked_either(conn: Any, identity_a: str, identity_b: str) -> bool:
    ensure_schema(conn)
    for table in _BLOCK_TABLES:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE (owner_identity = ? AND blocked_identity = ?) "
            "OR (owner_identity = ? AND blocked_identity = ?) LIMIT 1",
            (identity_a, identity_b, identity_b, identity_a),
        ).fetchone()
        if row is not None:
            return True
    return False


def blocked_by(conn: Any, owner_identity: str) -> set[str]:
    ensure_schema(conn)
    found: set[str] = set()
    for table in _BLOCK_TABLES:
        rows = conn.execute(f"SELECT blocked_identity FROM {table} WHERE owner_identity = ?", (owner_identity,))
        found.update(str(row["blocked_identity"]) for row in rows.fetchall())
    return found


def blockers_of(conn: Any, blocked_identity: str) -> set[str]:
    ensure_schema(conn)
    found: set[str] = set()
    for table in _BLOCK_TABLES:
        rows = conn.execute(f"SELECT owner_identity FROM {table} WHERE blocked_identity = ?", (blocked_identity,))
        found.update(str(row["owner_identity"]) for row in rows.fetchall())
    return found


def utc_iso_from_any(value: Any) -> str:
    """Normalise a stored stamp to aware UTC ISO: naive 'T' stamps are local time
    (message_center ``datetime.now().isoformat()``), naive 'YYYY-MM-DD HH:MM:SS' ones
    are SQLite ``CURRENT_TIMESTAMP`` (UTC)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc) if "T" not in text else moment.astimezone()
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def add_event(events: Optional[list], identities: Iterable[str], event: dict[str, Any]) -> None:
    if events is None:
        return
    targets = sorted({str(item) for item in identities if item})
    if targets:
        events.append((targets, event))


def in_clause(values: Iterable[Any]) -> tuple[str, tuple]:
    items = tuple(values)
    return ",".join("?" for _ in items) or "NULL", items
