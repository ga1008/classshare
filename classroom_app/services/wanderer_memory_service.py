"""散修 conversation memory (one thread per conversation).

docs/wanderer-ai-free-agents-2026-10-25.md §3 / §4 item 5. Thread keys look like
``blog_post:123`` / ``classroom:45`` / ``dm:student:7`` / ``dao``.

* DB (``wanderer_memory_threads``): long-term ``summary`` + the last
  ``HISTORY_DB_LIMIT`` turns, ``total_chars`` since the last compaction.
* Disk mirror: every turn is appended to
  ``DATA_DIR/wanderers/<slug>/threads/<safe_thread_key>.jsonl`` and kept even
  after compaction (the raw transcript is never lost).
* Process LRU of ``LRU_MAX_THREADS`` threads: read-only acceleration for
  prompt building (``load_thread``). Writes (``append_turn`` /
  ``compact_thread``) always re-read the row from the DB first — another
  process (app vs scheduler) may have written since — and invalidate the entry
  afterwards so the next read refreshes it.

Compaction is triggered when ``total_chars`` exceeds
``WANDERER_THREAD_COMPACT_CHARS`` (default 600k). The summarizer is injected so
tests never reach a model; Phase B passes an AI-backed one.
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config
from ..db.schema_wanderers import ensure_wanderer_schema
from ..time_utils import local_iso

HISTORY_DB_LIMIT = 40
HISTORY_AFTER_COMPACTION = 20
SUMMARY_MAX_CHARS = 3000
TURN_MAX_CHARS = 4000
DEFAULT_COMPACT_CHARS = 600_000
LRU_MAX_THREADS = 200
_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.-]+")

_LRU: "OrderedDict[tuple[int, str], dict[str, Any]]" = OrderedDict()
_LRU_LOCK = threading.Lock()


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _empty_thread(wanderer_id: int, thread_key: str) -> dict[str, Any]:
    return {
        "id": None, "wanderer_id": wanderer_id, "thread_key": thread_key, "counterpart_identity": "",
        "summary": "", "history": [], "total_chars": 0, "compacted_count": 0,
        "last_message_at": None, "updated_at": None,
    }


def _copy_thread(thread: dict[str, Any]) -> dict[str, Any]:
    return {**thread, "history": [dict(turn) for turn in thread.get("history") or []]}


def _parse_history(raw: Any) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
    except (TypeError, ValueError):
        value = []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def invalidate_thread_cache(wanderer_id: Optional[int] = None, thread_key: Optional[str] = None) -> None:
    with _LRU_LOCK:
        if wanderer_id is None:
            _LRU.clear()
            return
        if thread_key is not None:
            _LRU.pop((int(wanderer_id), str(thread_key)), None)
            return
        for key in [key for key in _LRU if key[0] == int(wanderer_id)]:
            _LRU.pop(key, None)


def _cache_put(thread: dict[str, Any]) -> None:
    key = (int(thread["wanderer_id"]), str(thread["thread_key"]))
    with _LRU_LOCK:
        _LRU[key] = _copy_thread(thread)
        _LRU.move_to_end(key)
        while len(_LRU) > LRU_MAX_THREADS:
            _LRU.popitem(last=False)


def _cache_get(wanderer_id: int, thread_key: str) -> Optional[dict[str, Any]]:
    key = (wanderer_id, thread_key)
    with _LRU_LOCK:
        cached = _LRU.get(key)
        if cached is None:
            return None
        _LRU.move_to_end(key)
        return _copy_thread(cached)


def _read_thread(conn: Any, wanderer_id: int, thread_key: str) -> dict[str, Any]:
    """The current DB row (or an unsaved empty thread); never consults the cache."""
    ensure_wanderer_schema(conn)
    row = conn.execute(
        "SELECT id, counterpart_identity, summary, history_json, total_chars, compacted_count, "
        "last_message_at, updated_at FROM wanderer_memory_threads WHERE wanderer_id = ? AND thread_key = ? LIMIT 1",
        (wanderer_id, thread_key),
    ).fetchone()
    if row is None:
        return _empty_thread(wanderer_id, thread_key)
    return {
        **_empty_thread(wanderer_id, thread_key),
        "id": int(_row_value(row, "id")),
        "counterpart_identity": str(_row_value(row, "counterpart_identity", 1) or ""),
        "summary": str(_row_value(row, "summary", 2) or ""),
        "history": _parse_history(_row_value(row, "history_json", 3)),
        "total_chars": int(_row_value(row, "total_chars", 4) or 0),
        "compacted_count": int(_row_value(row, "compacted_count", 5) or 0),
        "last_message_at": _row_value(row, "last_message_at", 6),
        "updated_at": _row_value(row, "updated_at", 7),
    }


def load_thread(conn: Any, wanderer_id: int, thread_key: str) -> dict[str, Any]:
    """The thread for prompt building (cached; may lag another process briefly). Returned dicts are copies."""
    wanderer_id = int(wanderer_id)
    thread_key = str(thread_key)
    cached = _cache_get(wanderer_id, thread_key)
    if cached is not None:
        return cached
    thread = _read_thread(conn, wanderer_id, thread_key)
    if thread["id"] is not None:
        _cache_put(thread)
    return _copy_thread(thread)


def _save_thread(conn: Any, thread: dict[str, Any]) -> None:
    now = local_iso()
    conn.execute(
        """
        INSERT INTO wanderer_memory_threads (wanderer_id, thread_key, counterpart_identity, summary, history_json,
                                             total_chars, compacted_count, last_message_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(wanderer_id, thread_key) DO UPDATE SET
            counterpart_identity = excluded.counterpart_identity,
            summary = excluded.summary,
            history_json = excluded.history_json,
            total_chars = excluded.total_chars,
            compacted_count = excluded.compacted_count,
            last_message_at = excluded.last_message_at,
            updated_at = excluded.updated_at
        """,
        (int(thread["wanderer_id"]), str(thread["thread_key"]), str(thread.get("counterpart_identity") or ""),
         str(thread.get("summary") or ""), json.dumps(thread.get("history") or [], ensure_ascii=False),
         int(thread.get("total_chars") or 0), int(thread.get("compacted_count") or 0),
         thread.get("last_message_at"), now),
    )
    invalidate_thread_cache(int(thread["wanderer_id"]), str(thread["thread_key"]))


def safe_thread_key(thread_key: str) -> str:
    cleaned = _SAFE_KEY.sub("_", str(thread_key or "")).strip("._")
    return (cleaned or "thread")[:120]


def _safe_slug(slug: Any) -> str:
    return safe_thread_key(str(slug or "unknown"))


def thread_mirror_path(wanderer: dict[str, Any], thread_key: str) -> Path:
    base = Path(getattr(config, "DATA_DIR"))
    return base / "wanderers" / _safe_slug(wanderer.get("slug")) / "threads" / f"{safe_thread_key(thread_key)}.jsonl"


def _append_mirror(wanderer: dict[str, Any], thread_key: str, turn: dict[str, Any]) -> bool:
    path = thread_mirror_path(wanderer, thread_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(turn, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        # The DB row is the working memory; the mirror is an archive. Never fail the turn.
        print(f"[WANDERER] memory mirror write failed for {path.name}: {exc}")
        return False


def append_turn(
    conn: Any,
    wanderer: dict[str, Any],
    thread_key: str,
    *,
    who: str,
    text: str,
    counterpart_identity: str = "",
) -> dict[str, Any]:
    """Append one turn (DB keeps the last 40; disk keeps everything). Caller commits."""
    from .social_common import serialize_on
    serialize_on(conn, "wanderer-memory", [f"{wanderer['id']}:{thread_key}"])
    clean_text = str(text or "").strip()[:TURN_MAX_CHARS]
    thread = _read_thread(conn, int(wanderer["id"]), str(thread_key))  # writes never trust the cache
    turn = {"at": local_iso(), "who": str(who or "")[:80], "text": clean_text}
    updated = {
        **thread,
        "history": [*thread["history"], turn][-HISTORY_DB_LIMIT:],
        "total_chars": int(thread["total_chars"]) + len(clean_text),
        "counterpart_identity": str(counterpart_identity or thread.get("counterpart_identity") or ""),
        "last_message_at": turn["at"],
    }
    _save_thread(conn, updated)
    _append_mirror(wanderer, thread_key, turn)
    return _copy_thread(updated)


def compact_threshold_chars() -> int:
    try:
        return max(1, int(str(os.getenv("WANDERER_THREAD_COMPACT_CHARS", "")).strip() or DEFAULT_COMPACT_CHARS))
    except ValueError:
        return DEFAULT_COMPACT_CHARS


def needs_compaction(thread: dict[str, Any]) -> bool:
    # Compact before the 40-turn working window starts evicting short messages.
    return (len(thread.get("history") or []) >= HISTORY_DB_LIMIT - 6
            or int(thread.get("total_chars") or 0) > compact_threshold_chars())


def _format_turn(turn: dict[str, Any]) -> str:
    return f"[{turn.get('at', '')}] {turn.get('who', '')}：{turn.get('text', '')}"


def thread_history_text(thread: dict[str, Any]) -> str:
    """Summary + full DB history as plain text (input for a summarizer)."""
    parts = []
    if thread.get("summary"):
        parts.append(f"【既有摘要】\n{thread['summary']}")
    parts.append("【对话记录】\n" + "\n".join(_format_turn(turn) for turn in thread.get("history") or []))
    return "\n\n".join(parts)


def compact_thread(
    conn: Any,
    wanderer: dict[str, Any],
    thread_key: str,
    summarizer: Callable[[str], str],
    *, expected_thread: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Fold the history into a <=3k summary, keep the last 20 turns. Caller commits.

    A summarizer failure leaves the thread untouched and re-raises, so a broken
    model call can never erase memory.
    """
    from .social_common import serialize_on
    serialize_on(conn, "wanderer-memory", [f"{wanderer['id']}:{thread_key}"])
    thread = _read_thread(conn, int(wanderer["id"]), str(thread_key))  # writes never trust the cache
    if expected_thread is not None and (thread.get("summary") != expected_thread.get("summary")
            or thread.get("history") != expected_thread.get("history")):
        return thread  # concurrent arrivals are not covered by the generated summary
    summary = str(summarizer(thread_history_text(thread)) or "").strip()[:SUMMARY_MAX_CHARS]
    if not summary:
        raise ValueError("记忆压缩结果为空，保留原记忆")
    kept = thread["history"][-HISTORY_AFTER_COMPACTION:]
    updated = {
        **thread,
        "summary": summary,
        "history": kept,
        "total_chars": len(summary) + sum(len(str(turn.get("text") or "")) for turn in kept),
        "compacted_count": int(thread["compacted_count"]) + 1,
    }
    _save_thread(conn, updated)
    return _copy_thread(updated)


def thread_context_block(thread: dict[str, Any], limit_turns: int = 20) -> str:
    """Memory block for the system prompt (design §4 item 5)."""
    lines = []
    if thread.get("summary"):
        lines.append(f"【你对这段交流的长期记忆】\n{thread['summary']}")
    recent = (thread.get("history") or [])[-max(0, int(limit_turns)):] if limit_turns else []
    if recent:
        lines.append("【最近的对话】\n" + "\n".join(_format_turn(turn) for turn in recent))
    if not lines:
        return "【记忆】这是你们第一次在这里交流。"
    return "\n\n".join(lines)
