"""Public cultivation identity: the level emblem shown beside student avatars.

* A student may hide their cultivation (个人设置 → 隐藏修为). Hidden students get
  no emblem anywhere, are never named as 本宗天骄, and their score is not exposed
  to classmates (collaboration member cards).
* ``load_student_emblems`` returns, per visible student, the highest level reached
  across *all* their classrooms (certificate tier) plus the score of that
  classroom, cheap enough to batch for every avatar on a page.

The setting lives in a runtime-owned table (kept out of ``classroom_app/db``):
``cultivation_display_settings(student_id PK, hide_cultivation 0/1, updated_at ISO)``.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any, Iterable

from ..db.connection import get_configured_db_engine
from ..db.savepoint import best_effort

EMBLEM_CACHE_TTL_SECONDS = 60.0
EMBLEM_BATCH_LIMIT = 200
EMBLEM_CACHE_MAX_ENTRIES = 5000
_ID_PATTERN = re.compile(r"[0-9]{1,12}")

_READY_ENGINES: set[str] = set()
_CACHE: dict[int, tuple[float, dict[str, Any] | None]] = {}
_CACHE_LOCK = threading.Lock()


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _table_exists(conn: Any, engine: str) -> bool:
    if engine == "postgres":
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'cultivation_display_settings'"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'cultivation_display_settings'"
        ).fetchone()
    try:
        return row is not None and int(_row_value(row, "n")) >= 1
    except (TypeError, ValueError):
        return False


def ensure_cultivation_display_schema(conn: Any) -> None:
    engine = get_configured_db_engine()
    if engine in _READY_ENGINES and _table_exists(conn, engine):
        return
    # Savepoint-guarded: a concurrent first call that loses the CREATE race must
    # not abort the caller's transaction (the table exists either way).
    with best_effort(conn, "cultivation_display_ddl"):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cultivation_display_settings (
                student_id INTEGER PRIMARY KEY,
                hide_cultivation INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
    if _table_exists(conn, engine):
        _READY_ENGINES.add(engine)


def _clean_ids(ids: Iterable[Any]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in ids:
        text = str(value).strip()
        if not _ID_PATTERN.fullmatch(text):
            continue
        number = int(text)
        if number > 0 and number not in seen:
            seen.add(number)
            result.append(number)
    return result


def hidden_student_ids(conn: Any, student_ids: Iterable[Any]) -> set[int]:
    ids = _clean_ids(student_ids)
    if not ids:
        return set()
    ensure_cultivation_display_schema(conn)
    hidden: set[int] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT student_id FROM cultivation_display_settings WHERE hide_cultivation = 1 AND student_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        hidden.update(int(_row_value(row, "student_id")) for row in rows)
    return hidden


def is_cultivation_hidden(conn: Any, student_id: Any) -> bool:
    return bool(hidden_student_ids(conn, [student_id]))


def set_cultivation_hidden(conn: Any, student_id: int, hidden: bool) -> bool:
    ensure_cultivation_display_schema(conn)
    conn.execute(
        """
        INSERT INTO cultivation_display_settings (student_id, hide_cultivation, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET
            hide_cultivation = excluded.hide_cultivation,
            updated_at = excluded.updated_at
        """,
        (int(student_id), 1 if hidden else 0, datetime.now().isoformat(timespec="seconds")),
    )
    invalidate_emblem_cache([student_id])
    return bool(hidden)


def invalidate_emblem_cache(student_ids: Iterable[Any] | None = None) -> None:
    with _CACHE_LOCK:
        if student_ids is None:
            _CACHE.clear()
            return
        for student_id in _clean_ids(student_ids):
            _CACHE.pop(student_id, None)


def emblem_for_level(level: dict[str, Any] | None, score: float | None = None) -> dict[str, Any] | None:
    """Public emblem payload for a level dict (tier >= 1), or None for 未入道."""
    if not level:
        return None
    try:
        tier = int(level.get("tier") or 0)
    except (TypeError, ValueError):
        tier = 0
    key = str(level.get("key") or level.get("level_key") or "")
    if tier < 1 or not key:
        return None
    payload = {
        "key": key,
        "tier": tier,
        "name": level.get("name") or level.get("level_name") or "",
        "short_name": level.get("short_name") or "",
        "certificate_title": level.get("certificate_title") or "",
        "emblem_url": f"/static/img/cultivation/emblems/{key}.svg",
    }
    if score is not None:
        payload["score"] = round(float(score), 1)
    return payload


def _emblem_payload(tier: int, score: float, curved: bool) -> dict[str, Any] | None:
    from .cultivation_growth_service import apply_growth_curve
    from .learning_progress_service import LEARNING_LEVELS

    level = next((item for item in LEARNING_LEVELS if int(item["tier"]) == int(tier)), None)
    shown = score if curved else apply_growth_curve(score)
    return emblem_for_level(level, shown)


def _wanderer_emblems(conn: Any, ids: list[int]) -> dict[int, dict[str, Any] | None]:
    """散修 tiers come from ``wanderer_agents.tier``, not classroom certificates."""
    from .learning_progress_service import LEARNING_LEVELS
    from .wanderer_identity_service import load_wanderers_by_student_ids

    emblems: dict[int, dict[str, Any] | None] = {}
    for student_id, wanderer in load_wanderers_by_student_ids(conn, ids).items():
        tier = int(wanderer.get("tier") or 0)
        level = next((item for item in LEARNING_LEVELS if int(item["tier"]) == tier), None)
        payload = emblem_for_level(level, float(wanderer.get("cultivation_score") or 0))
        emblems[student_id] = {**payload, "wanderer": True, "sect": None} if payload else None
    return emblems


def _query_emblems(conn: Any, ids: list[int]) -> dict[int, dict[str, Any] | None]:
    wanderer_emblems: dict[int, dict[str, Any] | None] = {}
    with best_effort(conn, "cultivation_wanderer_emblems"):
        wanderer_emblems = _wanderer_emblems(conn, ids)
    ids = [student_id for student_id in ids if student_id not in wanderer_emblems]
    if not ids:
        return wanderer_emblems
    return {**_query_certificate_emblems(conn, ids), **wanderer_emblems}


def _query_certificate_emblems(conn: Any, ids: list[int]) -> dict[int, dict[str, Any] | None]:
    placeholders = ",".join("?" for _ in ids)
    tiers: dict[tuple[int, int], int] = {}
    for row in conn.execute(
        f"""
        SELECT student_id, class_offering_id, MAX(tier) AS tier
        FROM learning_certificates
        WHERE student_id IN ({placeholders})
        GROUP BY student_id, class_offering_id
        """,
        tuple(ids),
    ).fetchall():
        key = (int(_row_value(row, "student_id")), int(_row_value(row, "class_offering_id", 1)))
        tiers[key] = int(_row_value(row, "tier", 2) or 0)
    if not tiers:
        return {student_id: None for student_id in ids}
    scores: dict[tuple[int, int], tuple[float, bool]] = {}
    for row in conn.execute(
        f"""
        SELECT student_id, class_offering_id, score,
               CASE WHEN metrics_json LIKE '%growth_exponent%' THEN 1 ELSE 0 END AS curved
        FROM learning_progress_snapshots
        WHERE student_id IN ({placeholders})
        """,
        tuple(ids),
    ).fetchall():
        key = (int(_row_value(row, "student_id")), int(_row_value(row, "class_offering_id", 1)))
        scores[key] = (float(_row_value(row, "score", 2) or 0), bool(int(_row_value(row, "curved", 3) or 0)))
    best: dict[int, tuple[int, float, bool]] = {}
    for (student_id, offering_id), tier in tiers.items():
        score, curved = scores.get((student_id, offering_id), (0.0, True))
        current = best.get(student_id)
        if current is None or (tier, score) > (current[0], current[1]):
            best[student_id] = (tier, score, curved)
    return {student_id: (_emblem_payload(*best[student_id]) if student_id in best else None) for student_id in ids}


def load_student_emblems(conn: Any, student_ids: Iterable[Any]) -> dict[int, dict[str, Any]]:
    """Emblem per visible student with at least one passed stage; hidden/unranked omitted."""
    ids = _clean_ids(student_ids)[:EMBLEM_BATCH_LIMIT]
    if not ids:
        return {}
    # The hide flag is read on every call (one indexed query), never cached, so a
    # student who hides disappears on the next request in every worker process.
    hidden = hidden_student_ids(conn, ids)
    visible_ids = [student_id for student_id in ids if student_id not in hidden]
    now = time.monotonic()
    result: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    with _CACHE_LOCK:
        for student_id in visible_ids:
            cached = _CACHE.get(student_id)
            if cached and cached[0] > now:
                if cached[1]:
                    result[student_id] = cached[1]
            else:
                missing.append(student_id)
    if missing:
        fresh = _query_emblems(conn, missing)
        expires = now + EMBLEM_CACHE_TTL_SECONDS
        with _CACHE_LOCK:
            if len(_CACHE) + len(missing) > EMBLEM_CACHE_MAX_ENTRIES:
                for key in [key for key, value in _CACHE.items() if value[0] <= now]:
                    _CACHE.pop(key, None)
                if len(_CACHE) + len(missing) > EMBLEM_CACHE_MAX_ENTRIES:
                    _CACHE.clear()
            for student_id in missing:
                payload = fresh.get(student_id)
                _CACHE[student_id] = (expires, payload)
                if payload:
                    result[student_id] = payload
    return result
