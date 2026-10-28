"""散修 identity: placeholder class, student rows, agent rows, tier helpers.

docs/wanderer-ai-free-agents-2026-10-25.md §1. A wanderer is a real
``students`` row (``enrollment_status='wanderer'``, no password, hidden
placeholder class) bound 1:1 to a ``wanderer_agents`` row, which is the runtime
truth. Every roster/grade/attendance query that filters on
``enrollment_status='active'`` therefore excludes wanderers naturally.

Lookups by student id are cached in-process for 60s (negative results too, so
the blog/emblem hot paths pay one indexed query per minute per id at most).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from typing import Any, Iterable, Optional

from ..db.connection import execute_insert_returning_id
from ..db.schema_wanderers import ensure_wanderer_schema
from ..time_utils import local_iso, local_now, parse_datetime

WANDERER_ENROLLMENT_STATUS = "wanderer"
WANDERER_CLASS_NAME = "散修·无宗门"
WANDERER_CLASS_ACADEMIC_SOURCE = "wanderer"
WANDERER_ID_PREFIX = "WANDERER-"

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DEAD = "dead"
WANDERER_STATUSES = (STATUS_ACTIVE, STATUS_PAUSED, STATUS_DEAD)
MAX_TIER = 10

DEFAULT_BEHAVIORS = {
    "blog_browse": 1, "blog_like": 1, "blog_comment": 1, "reply": 1, "dm_reply": 1,
    "classroom_visit": 1, "classroom_message": 1, "knowledge_read": 1, "promotion": 1, "blog_post": 1,
    "social": 1,
}
ACTION_KINDS = frozenset({
    "browse_post", "like_post", "like_comment", "comment", "reply", "dm_reply",
    "classroom_visit", "classroom_message", "knowledge_read", "knowledge_forget",
    "request_promotion", "reflect", "skip", "write_post", "experience_learn",
    # 好友/群聊 (social doc §10): friend decisions + proactive requests, chat messages sent.
    "social_friend", "social_message",
})
ORIGIN_SEED = "seed"
ORIGIN_DAO = "dao"
WANDERER_ORIGINS = (ORIGIN_SEED, ORIGIN_DAO)

CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 5000
_CACHE: dict[int, tuple[float, Optional[dict[str, Any]]]] = {}
_CACHE_LOCK = threading.Lock()

_JSON_FIELDS = {
    "speech_style_json": ("speech_style", dict),
    "interests_json": ("interests", list),
    "schedule_json": ("schedule", dict),
    "reply_policy_json": ("reply_policy", dict),
    "behaviors_json": ("behaviors", dict),
    "stats_json": ("stats", dict),
}
_INT_FIELDS = (
    "id", "student_id", "tier", "activity_level", "daily_action_budget",
    "warnings_count", "knowledge_chars", "promotion_requested",
)

_SELECT_WANDERER = """
    SELECT w.*, s.student_id_number AS student_id_number, s.class_id AS class_id,
           s.description AS public_intro, s.avatar_file_hash AS avatar_file_hash,
           s.avatar_mime_type AS avatar_mime_type,
           s.gender AS gender
    FROM wanderer_agents w
    JOIN students s ON s.id = w.student_id
"""


class WandererSeedError(RuntimeError):
    """Seeding cannot proceed (e.g. no teacher row to own the placeholder class)."""


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except AttributeError:
        return dict(row)


def _loads(raw: Any, fallback_type: type) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except (TypeError, ValueError):
        value = None
    return value if isinstance(value, fallback_type) else fallback_type()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _hydrate(row: Any) -> dict[str, Any]:
    data = _row_to_dict(row)
    parsed = {name: _loads(data.get(column), kind) for column, (name, kind) in _JSON_FIELDS.items()}
    ints = {key: _as_int(data.get(key)) for key in _INT_FIELDS}
    try:
        score = float(data.get("cultivation_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        **data,
        **parsed,
        **ints,
        "cultivation_score": score,
        "behaviors": {**DEFAULT_BEHAVIORS, **parsed["behaviors"]},
        "is_dead": str(data.get("status") or "") == STATUS_DEAD,
    }


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Seeding: placeholder class + student rows + agent rows
# ---------------------------------------------------------------------------

def _insert_tolerating_race(conn: Any, name: str, sql: str, params: tuple) -> None:
    """Plain INSERT inside a savepoint: a concurrent seeder winning the race (or any
    integrity error) is undone and logged, and the caller re-selects by natural key.
    No ``ON CONFLICT`` — export-provisioned PostgreSQL may lack those unique constraints.
    """
    from ..db.savepoint import best_effort

    with best_effort(conn, name, log_prefix="[WANDERER]"):
        conn.execute(sql, params)


def _class_id(conn: Any) -> Optional[int]:
    row = conn.execute("SELECT id FROM classes WHERE name = ? ORDER BY id LIMIT 1", (WANDERER_CLASS_NAME,)).fetchone()
    return None if row is None else int(_row_value(row, "id"))


def ensure_wanderer_placeholder_class(conn: Any) -> int:
    """The hidden 散修·无宗门 class (created once; never bound to any classroom)."""
    existing = _class_id(conn)
    if existing is not None:
        return existing
    teacher = conn.execute("SELECT id FROM teachers ORDER BY id LIMIT 1").fetchone()
    if teacher is None:
        # classes.created_by_teacher_id is NOT NULL with a FK to teachers(id).
        raise WandererSeedError("尚无教师账号，散修占位班暂不创建")
    _insert_tolerating_race(
        conn, "wanderer_seed_class",
        "INSERT INTO classes (name, created_by_teacher_id, description, academic_source, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (WANDERER_CLASS_NAME, int(_row_value(teacher, "id")),
         "散修（AI 电子学生）占位班：无宗门，不绑定任何课堂。", WANDERER_CLASS_ACADEMIC_SOURCE, local_iso()),
    )
    created = _class_id(conn)
    if created is None:
        raise WandererSeedError("散修占位班创建失败")
    return created


def wanderer_student_number(slug: str) -> str:
    return f"{WANDERER_ID_PREFIX}{slug}"


def _student_pk(conn: Any, number: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM students WHERE student_id_number = ? ORDER BY id LIMIT 1", (number,)).fetchone()
    return None if row is None else int(_row_value(row, "id"))


def _ensure_student_row(conn: Any, seed: dict[str, Any], class_id: int) -> int:
    number = wanderer_student_number(seed["slug"])
    existing = _student_pk(conn, number)
    if existing is not None:
        return existing
    now = local_iso()
    _insert_tolerating_race(
        conn, "wanderer_seed_student",
        """
        INSERT INTO students (student_id_number, name, class_id, gender, hashed_password,
                              description, enrollment_status, enrollment_status_updated_at,
                              enrollment_note, created_at)
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (number, seed["display_name"], class_id, seed.get("gender") or "", seed.get("public_intro") or "",
         WANDERER_ENROLLMENT_STATUS, now, "散修（AI 电子学生）", now),
    )
    created = _student_pk(conn, number)
    if created is None:
        raise WandererSeedError(f"散修学生行创建失败：{number}")
    return created


def _insert_agent_row(conn: Any, seed: dict[str, Any], student_id: int) -> bool:
    """Insert the agent row when missing. Returns True when this call created it."""
    if conn.execute("SELECT 1 FROM wanderer_agents WHERE slug = ? LIMIT 1", (seed["slug"],)).fetchone():
        return False
    from .wanderer_voice_service import normalized_speech_style
    frequency = seed.get("interaction_frequency") or {}
    schedule = {
        "active_hours": frequency.get("active_hours") or [],
        "weekday_weights": frequency.get("weekday_weights") or {},
    }
    now = local_iso()
    conn.execute(
        """
        INSERT INTO wanderer_agents (
            student_id, slug, display_name, archetype, persona_prompt, speech_style_json,
            interests_json, schedule_json, activity_level, daily_action_budget, reply_policy_json,
            tier, cultivation_score, status, behaviors_json, stats_json, origin, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO NOTHING
        """,
        (
            student_id, seed["slug"], seed["display_name"], seed["archetype"], seed["persona_prompt"],
            _dumps(normalized_speech_style(seed)), _dumps(list(seed.get("interests") or [])),
            _dumps(schedule), int(frequency.get("activity_level") or 3),
            int(seed.get("daily_action_budget") or 10), _dumps(seed.get("reply_policy") or {}),
            clamp_tier(seed.get("initial_tier")), STATUS_ACTIVE,
            _dumps({**DEFAULT_BEHAVIORS, **(seed.get("behaviors") or {})}), "{}",
            seed.get("origin") if seed.get("origin") in WANDERER_ORIGINS else ORIGIN_SEED, now, now,
        ),
    )
    return True


def ensure_wanderer_students_detailed(conn: Any, seeds: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Create missing rows; returns ``{"class_id", "students": {slug: sid}, "created": [slug]}``."""
    ensure_wanderer_schema(conn)
    class_id = ensure_wanderer_placeholder_class(conn)
    students: dict[str, int] = {}
    created: list[str] = []
    for seed in seeds:
        student_id = _ensure_student_row(conn, seed, class_id)
        students[seed["slug"]] = student_id
        if _insert_agent_row(conn, seed, student_id):
            created.append(seed["slug"])
    invalidate_wanderer_cache()
    return {"class_id": class_id, "students": students, "created": created}


def ensure_wanderer_students(conn: Any, seeds: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Create missing student + agent rows; an existing agent row is never overwritten."""
    return ensure_wanderer_students_detailed(conn, seeds)["students"]


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def invalidate_wanderer_cache(student_ids: Iterable[Any] | None = None) -> None:
    with _CACHE_LOCK:
        if student_ids is None:
            _CACHE.clear()
            return
        for student_id in _clean_ids(student_ids):
            _CACHE.pop(student_id, None)


def _clean_ids(ids: Iterable[Any]) -> list[int]:
    cleaned: list[int] = []
    for value in ids:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in cleaned:
            cleaned.append(number)
    return cleaned


def _store_cache(fresh: dict[int, Optional[dict[str, Any]]], now: float) -> None:
    expires = now + CACHE_TTL_SECONDS
    with _CACHE_LOCK:
        if len(_CACHE) + len(fresh) > _CACHE_MAX_ENTRIES:
            _CACHE.clear()
        for student_id, payload in fresh.items():
            _CACHE[student_id] = (expires, payload)


def _cached_lookup(ids: list[int], now: float) -> tuple[dict[int, dict[str, Any]], list[int]]:
    hits: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    with _CACHE_LOCK:
        for student_id in ids:
            cached = _CACHE.get(student_id)
            if cached and cached[0] > now:
                if cached[1]:
                    hits[student_id] = dict(cached[1])
            else:
                missing.append(student_id)
    return hits, missing


def load_wanderers_by_student_ids(conn: Any, ids: Iterable[Any]) -> dict[int, dict[str, Any]]:
    """``{student_id: wanderer}`` for the ids that are wanderers (60s cached)."""
    cleaned = _clean_ids(ids)
    if not cleaned:
        return {}
    now = time.monotonic()
    result, missing = _cached_lookup(cleaned, now)
    if not missing:
        return result
    ensure_wanderer_schema(conn)
    fresh: dict[int, Optional[dict[str, Any]]] = {student_id: None for student_id in missing}
    for start in range(0, len(missing), 500):
        chunk = missing[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(f"{_SELECT_WANDERER} WHERE w.student_id IN ({placeholders})", tuple(chunk)).fetchall()
        for row in rows:
            wanderer = _hydrate(row)
            fresh[wanderer["student_id"]] = wanderer
    _store_cache(fresh, now)
    return {**result, **{sid: dict(payload) for sid, payload in fresh.items() if payload}}


def is_wanderer(conn: Any, student_id: Any) -> bool:
    return bool(load_wanderers_by_student_ids(conn, [student_id]))


def _load_one(conn: Any, where: str, value: Any) -> Optional[dict[str, Any]]:
    ensure_wanderer_schema(conn)
    row = conn.execute(f"{_SELECT_WANDERER} WHERE {where} LIMIT 1", (value,)).fetchone()
    return _hydrate(row) if row is not None else None


def load_wanderer(conn: Any, wanderer_id: Any) -> Optional[dict[str, Any]]:
    """Fresh (uncached) load by ``wanderer_agents.id``."""
    return _load_one(conn, "w.id = ?", int(wanderer_id))


def load_wanderer_by_student_id(conn: Any, student_id: Any) -> Optional[dict[str, Any]]:
    """Fresh (uncached) load by ``students.id``."""
    return _load_one(conn, "w.student_id = ?", int(student_id))


def load_wanderer_by_slug(conn: Any, slug: str) -> Optional[dict[str, Any]]:
    return _load_one(conn, "w.slug = ?", str(slug))


def list_wanderers(conn: Any, *, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
    ensure_wanderer_schema(conn)
    wanted = [str(item) for item in (statuses or []) if str(item) in WANDERER_STATUSES]
    if wanted:
        placeholders = ",".join("?" for _ in wanted)
        rows = conn.execute(
            f"{_SELECT_WANDERER} WHERE w.status IN ({placeholders}) ORDER BY w.id", tuple(wanted)
        ).fetchall()
    else:
        rows = conn.execute(f"{_SELECT_WANDERER} ORDER BY w.id").fetchall()
    return [_hydrate(row) for row in rows]


def build_wanderer_user_dict(wanderer: dict[str, Any]) -> dict[str, Any]:
    """The same shape ``get_current_user`` yields for a student, for service calls."""
    number = str(wanderer.get("student_id_number") or wanderer_student_number(str(wanderer.get("slug") or "")))
    return {
        "id": int(wanderer["student_id"]),
        "role": "student",
        "name": str(wanderer.get("display_name") or ""),
        "username": number,
        "student_id_number": number,
        "class_id": wanderer.get("class_id"),
        "class_name": WANDERER_CLASS_NAME,
        "is_wanderer": True,
        "wanderer_id": int(wanderer["id"]),
    }


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------

def clamp_tier(tier: Any) -> int:
    try:
        return max(0, min(MAX_TIER, int(tier or 0)))
    except (TypeError, ValueError):
        return 0


def knowledge_capacity_chars(tier: Any) -> int:
    """tier0 4k, tier1 8k, x1.6 per tier above, capped at 200k (design §1.2)."""
    level = clamp_tier(tier)
    if level == 0:
        return 4000
    return int(min(200_000, round(8000 * (1.6 ** (level - 1)))))


def reply_char_limit(tier: Any) -> int:
    """Upper bound on one reply's length; grows with tier (design §1.2 d)."""
    return min(400, 80 + 30 * clamp_tier(tier))


def daily_budget_factor(tier: Any) -> float:
    """Multiplier on ``daily_action_budget`` (design §1.2 c)."""
    return round(1.0 + 0.05 * clamp_tier(tier), 2)


_WISDOM_DIRECTIVES = (
    (2, "你的境界尚浅：知识范围有限，不懂就坦白或求证，不装高深，也不故意犯错。已经学会的术语可以自然使用，语气仍由你自己的性格和当前情境决定。"),
    (5, "你已有些积累：能举具体例子说明观点，偶尔引用自己知识库里学过的内容，但不卖弄。"),
    (8, "你修为扎实：表达条理清楚，会追问对方的关键前提，看到明显错误会温和地纠正。"),
    (10, "你已近可为人师：言简意赅、点到为止，偶尔用典，不长篇说教，也不卖弄境界。"),
)


def wisdom_directive(tier: Any) -> str:
    """睿智程度指令 for the tier (design §4 item 2)."""
    level = clamp_tier(tier)
    return next(text for upper, text in _WISDOM_DIRECTIVES if level <= upper)


def tier_level(tier: Any) -> dict[str, Any]:
    """The LEARNING_LEVELS entry for ``tier`` (STARTER_LEVEL for 0)."""
    from .learning_progress_service import LEARNING_LEVELS, get_starter_level

    level = clamp_tier(tier)
    return next((dict(item) for item in LEARNING_LEVELS if int(item["tier"]) == level), get_starter_level())


# ---------------------------------------------------------------------------
# Action log, reviews, death
# ---------------------------------------------------------------------------

def log_wanderer_action(
    conn: Any,
    wanderer_id: int,
    action_kind: str,
    *,
    target_type: str = "",
    target_id: Any = "",
    class_offering_id: Optional[int] = None,
    content_preview: str = "",
    ai_calls: int = 0,
    mood: str = "",
    outcome: str = "ok",
    detail: Optional[dict[str, Any]] = None,
) -> int:
    ensure_wanderer_schema(conn)
    if action_kind not in ACTION_KINDS:
        raise ValueError(f"unknown wanderer action: {action_kind}")
    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO wanderer_action_log (wanderer_id, action_kind, target_type, target_id, class_offering_id,
                                         content_preview, ai_calls, mood, outcome, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(wanderer_id), action_kind, str(target_type or ""), "" if target_id is None else str(target_id),
         class_offering_id, str(content_preview or "")[:200], int(ai_calls), str(mood or "")[:40],
         str(outcome or "ok"), _dumps(detail or {}), local_iso()),
    )


def insert_dao_review(
    conn: Any,
    *,
    wanderer_id: Optional[int],
    review_kind: str,
    verdict: str,
    from_tier: Optional[int] = None,
    to_tier: Optional[int] = None,
    reason: str = "",
    advice: str = "",
    evidence: Optional[dict[str, Any]] = None,
    created_by: str = "dao",
) -> int:
    ensure_wanderer_schema(conn)
    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO wanderer_dao_reviews (wanderer_id, review_kind, verdict, from_tier, to_tier, reason,
                                          advice, evidence_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (wanderer_id, review_kind, verdict, from_tier, to_tier, str(reason or ""), str(advice or ""),
         _dumps(evidence or {}), str(created_by or "dao"), local_iso()),
    )


def active_knowledge_count(conn: Any, wanderer_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND status = 'active'",
        (int(wanderer_id),),
    ).fetchone()
    return int(_row_value(row, "n") or 0)


def evaluate_death(conn: Any, wanderer: dict[str, Any]) -> bool:
    """Design §1.3: no active knowledge, tier 0 and no cultivation left.

    An admin wipe (``forget_all``) forgets every entry and zeroes the score, so
    a wiped wanderer at tier 0 meets the same condition.
    """
    if str(wanderer.get("status") or "") == STATUS_DEAD:
        return True
    if clamp_tier(wanderer.get("tier")) != 0:
        return False
    try:
        score = float(wanderer.get("cultivation_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score > 0:
        return False
    return active_knowledge_count(conn, int(wanderer["id"])) == 0


def mark_dead(conn: Any, wanderer_id: int, reason: str) -> int:
    """Set status=dead and record the death verdict. Returns the review id."""
    wanderer = load_wanderer(conn, wanderer_id)
    if wanderer is None:
        raise ValueError("散修不存在")
    tier = clamp_tier(wanderer.get("tier"))
    review_id = insert_dao_review(
        conn, wanderer_id=int(wanderer_id), review_kind="death", verdict="died",
        from_tier=tier, to_tier=tier, reason=str(reason or "知识尽失、修为归零，判定陨落。"),
    )
    now = local_iso()
    conn.execute(
        "UPDATE wanderer_agents SET status = ?, promotion_requested = 0, last_review_id = ?, "
        "last_review_at = ?, updated_at = ? WHERE id = ?",
        (STATUS_DEAD, review_id, now, now, int(wanderer_id)),
    )
    invalidate_wanderer_cache([wanderer["student_id"]])
    _invalidate_emblem([wanderer["student_id"]])
    return review_id


def _invalidate_emblem(student_ids: list[int]) -> None:
    from .cultivation_display_service import invalidate_emblem_cache

    invalidate_emblem_cache(student_ids)


def recent_warning_active(wanderer: dict[str, Any], *, days: int = 3) -> bool:
    """Whether the last 天道 warning is recent enough to keep reminding (§5.2-6)."""
    warned_at = parse_datetime(wanderer.get("last_warning_at"))
    if warned_at is None:
        return False
    return warned_at.replace(tzinfo=None) >= local_now() - timedelta(days=days)
