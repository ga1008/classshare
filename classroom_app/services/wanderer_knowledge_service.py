"""散修 knowledge base: what a wanderer has learned and remembered.

docs/wanderer-ai-free-agents-2026-10-25.md §1.2 / §5.2-4. Entries live in
``wanderer_knowledge_entries`` (UNIQUE per wanderer + source). Capacity is
bounded by tier (``knowledge_capacity_chars``); when exceeded, the oldest and
least-used entries are *forgotten* (``status='forgotten'``, row kept) and a
``knowledge_forget`` action is logged. New knowledge adds ``chars / 100`` to
``cultivation_score``.
"""
from __future__ import annotations

import json
import random
from typing import Any, Callable, Iterable, Optional

from ..db.connection import execute_insert_returning_id
from ..db.schema_wanderers import ensure_wanderer_schema
from ..time_utils import local_iso
from .wanderer_identity_service import invalidate_wanderer_cache, log_wanderer_action
from .wanderer_life_core import SCORE_CHARS_PER_POINT, knowledge_capacity_for, knowledge_gain, talent_value

SOURCE_KINDS = frozenset({"material_page", "material", "blog_post", "classroom", "seed", "reflection", "legacy"})
KNOWLEDGE_DIGEST_MAX_CHARS = 1200
KNOWLEDGE_TITLE_MAX_CHARS = 120
_MAX_TAGS = 12


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _clean_tags(tags: Iterable[Any] | None) -> list[str]:
    cleaned: list[str] = []
    for tag in tags or []:
        text = str(tag or "").strip()[:30]
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:_MAX_TAGS]


def _load_tags(raw: Any) -> list[str]:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
    except (TypeError, ValueError):
        value = []
    return _clean_tags(value if isinstance(value, list) else [])


def _active_chars(conn: Any, wanderer_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(chars), 0) AS total FROM wanderer_knowledge_entries "
        "WHERE wanderer_id = ? AND status = 'active'",
        (wanderer_id,),
    ).fetchone()
    return int(_row_value(row, "total") or 0)


def _existing_entry(conn: Any, wanderer_id: int, source_kind: str, source_ref: str) -> Any:
    return conn.execute(
        "SELECT id, chars, status FROM wanderer_knowledge_entries "
        "WHERE wanderer_id = ? AND source_kind = ? AND source_ref = ? LIMIT 1",
        (wanderer_id, source_kind, source_ref),
    ).fetchone()


def _upsert_entry(conn: Any, wanderer_id: int, fields: dict[str, Any], existing: Any) -> int:
    now = local_iso()
    if existing is not None:
        entry_id = int(_row_value(existing, "id"))
        conn.execute(
            "UPDATE wanderer_knowledge_entries SET title = ?, digest = ?, chars = ?, tags_json = ?, "
            "status = 'active', updated_at = ? WHERE id = ?",
            (fields["title"], fields["digest"], fields["chars"], fields["tags_json"], now, entry_id),
        )
        return entry_id
    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO wanderer_knowledge_entries (wanderer_id, source_kind, source_ref, title, digest, chars,
                                                tags_json, status, use_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?)
        """,
        (wanderer_id, fields["source_kind"], fields["source_ref"], fields["title"], fields["digest"],
         fields["chars"], fields["tags_json"], now, now),
    )


def _forget_until_within(conn: Any, wanderer_id: int, capacity: int, keep_entry_id: int) -> list[int]:
    """Forget oldest least-used entries (never the one just written) until within capacity."""
    forgotten: list[int] = []
    total = _active_chars(conn, wanderer_id)
    while total > capacity:
        victim = conn.execute(
            "SELECT id, title, chars FROM wanderer_knowledge_entries "
            "WHERE wanderer_id = ? AND status = 'active' AND id <> ? "
            "ORDER BY use_count ASC, created_at ASC, id ASC LIMIT 1",
            (wanderer_id, keep_entry_id),
        ).fetchone()
        if victim is None:
            break
        victim_id = int(_row_value(victim, "id"))
        conn.execute(
            "UPDATE wanderer_knowledge_entries SET status = 'forgotten', updated_at = ? WHERE id = ?",
            (local_iso(), victim_id),
        )
        log_wanderer_action(
            conn, wanderer_id, "knowledge_forget", target_type="knowledge", target_id=victim_id,
            content_preview=str(_row_value(victim, "title", 1) or ""),
            detail={"reason": "capacity", "capacity": capacity, "chars": int(_row_value(victim, "chars", 2) or 0)},
        )
        forgotten.append(victim_id)
        total = _active_chars(conn, wanderer_id)
    return forgotten


def add_knowledge(
    conn: Any,
    wanderer: dict[str, Any],
    *,
    source_kind: str,
    source_ref: str,
    title: str,
    digest: str,
    tags: Iterable[Any] | None = None,
    rng: Optional[Callable[[], float]] = None,
) -> dict[str, Any]:
    """Upsert one entry, enforce capacity, bump cultivation (悟性/气运). Caller commits."""
    ensure_wanderer_schema(conn)
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown knowledge source_kind: {source_kind}")
    clean_digest = str(digest or "").strip()[:KNOWLEDGE_DIGEST_MAX_CHARS]
    if not clean_digest:
        raise ValueError("知识摘要不能为空")
    wanderer_id = int(wanderer["id"])
    fields = {
        "source_kind": source_kind,
        "source_ref": str(source_ref or "")[:200],
        "title": str(title or "").strip()[:KNOWLEDGE_TITLE_MAX_CHARS],
        "digest": clean_digest,
        "chars": len(clean_digest),
        "tags_json": json.dumps(_clean_tags(tags), ensure_ascii=False),
    }
    existing = _existing_entry(conn, wanderer_id, source_kind, fields["source_ref"])
    previous_active_chars = (
        int(_row_value(existing, "chars", 1) or 0)
        if existing is not None and str(_row_value(existing, "status", 2)) == "active" else 0
    )
    entry_id = _upsert_entry(conn, wanderer_id, fields, existing)
    capacity = knowledge_capacity_for(wanderer.get("tier"), talent_value(wanderer.get("talents"), "memory"))
    forgotten = _forget_until_within(conn, wanderer_id, capacity, entry_id)
    new_chars = max(0, fields["chars"] - previous_active_chars)
    crit = False
    score_delta = 0.0
    if new_chars:
        score_delta, crit = knowledge_gain(
            new_chars, talent_value(wanderer.get("talents"), "insight"),
            talent_value(wanderer.get("talents"), "luck"), rng or random.random,
        )
    knowledge_chars = _active_chars(conn, wanderer_id)
    conn.execute(
        "UPDATE wanderer_agents SET knowledge_chars = ?, cultivation_score = cultivation_score + ?, "
        "updated_at = ? WHERE id = ?",
        (knowledge_chars, score_delta, local_iso(), wanderer_id),
    )
    invalidate_wanderer_cache([wanderer.get("student_id")])
    return {
        "entry_id": entry_id,
        "knowledge_chars": knowledge_chars,
        "capacity": capacity,
        "forgotten": forgotten,
        "score_delta": score_delta,
        "crit": crit,
    }


def _entry_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(_row_value(row, "id")),
        "source_kind": str(_row_value(row, "source_kind", 1) or ""),
        "source_ref": str(_row_value(row, "source_ref", 2) or ""),
        "title": str(_row_value(row, "title", 3) or ""),
        "digest": str(_row_value(row, "digest", 4) or ""),
        "tags": _load_tags(_row_value(row, "tags_json", 5)),
        "use_count": int(_row_value(row, "use_count", 6) or 0),
        "chars": int(_row_value(row, "chars", 7) or 0),
    }


def select_knowledge_for_scene(
    conn: Any, wanderer: dict[str, Any], scene_tags: Iterable[Any] | None, limit: int = 5
) -> list[dict[str, Any]]:
    """Most relevant active entries (tag overlap, then use_count); bumps use_count of picks."""
    ensure_wanderer_schema(conn)
    wanted = {tag.lower() for tag in _clean_tags(scene_tags)}
    rows = conn.execute(
        "SELECT id, source_kind, source_ref, title, digest, tags_json, use_count, chars "
        "FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND status = 'active'",
        (int(wanderer["id"]),),
    ).fetchall()
    entries = [_entry_payload(row) for row in rows]
    ranked = sorted(
        entries,
        key=lambda item: (-len(wanted & {tag.lower() for tag in item["tags"]}), -item["use_count"], -item["id"]),
    )
    picked = ranked[: max(0, int(limit))]
    if picked:
        placeholders = ",".join("?" for _ in picked)
        conn.execute(
            f"UPDATE wanderer_knowledge_entries SET use_count = use_count + 1 WHERE id IN ({placeholders})",
            tuple(item["id"] for item in picked),
        )
    return [{**item, "use_count": item["use_count"] + 1} for item in picked]


def knowledge_summary(conn: Any, wanderer: dict[str, Any]) -> dict[str, Any]:
    ensure_wanderer_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(chars), 0) AS total FROM wanderer_knowledge_entries "
        "WHERE wanderer_id = ? AND status = 'active'",
        (int(wanderer["id"]),),
    ).fetchone()
    count = int(_row_value(row, "n") or 0)
    chars = int(_row_value(row, "total", 1) or 0)
    capacity = knowledge_capacity_for(wanderer.get("tier"), talent_value(wanderer.get("talents"), "memory"))
    return {
        "active_count": count,
        "chars": chars,
        "capacity": capacity,
        "ratio": round(chars / capacity, 4) if capacity else 0.0,
    }


def forget_all(conn: Any, wanderer_id: int) -> int:
    """Admin wipe: forget every active entry and zero the score. Returns entries forgotten."""
    ensure_wanderer_schema(conn)
    wanderer_id = int(wanderer_id)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND status = 'active'",
        (wanderer_id,),
    ).fetchone()
    count = int(_row_value(row, "n") or 0)
    now = local_iso()
    conn.execute(
        "UPDATE wanderer_knowledge_entries SET status = 'forgotten', updated_at = ? "
        "WHERE wanderer_id = ? AND status = 'active'",
        (now, wanderer_id),
    )
    conn.execute(
        "UPDATE wanderer_agents SET knowledge_chars = 0, cultivation_score = 0, updated_at = ? WHERE id = ?",
        (now, wanderer_id),
    )
    log_wanderer_action(
        conn, wanderer_id, "knowledge_forget", target_type="knowledge", target_id="*",
        content_preview="管理员抹除全部知识", detail={"wipe": True, "count": count},
    )
    student = conn.execute("SELECT student_id FROM wanderer_agents WHERE id = ?", (wanderer_id,)).fetchone()
    if student is not None:
        invalidate_wanderer_cache([_row_value(student, "student_id")])
    return count
