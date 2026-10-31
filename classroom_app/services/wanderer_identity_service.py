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
from .wanderer_life_core import (
    active_speech_limit,
    clamp_tier,
    default_talents,
    knowledge_capacity_for as _knowledge_capacity_for,
    life_state,
    lifespan_weeks_for,
    normalize_talents,
    talent_value,
)

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
    "social": 1, "social_block": 1, "social_group": 1, "social_seek": 1, "social_search": 1,
    "manual_read": 1,
}
ACTION_KINDS = frozenset({
    "browse_post", "like_post", "like_comment", "comment", "reply", "dm_reply",
    "classroom_visit", "classroom_message", "knowledge_read", "knowledge_forget",
    "request_promotion", "reflect", "skip", "write_post", "experience_learn",
    # 好友/群聊 (social doc §10): friend decisions + proactive requests, chat messages sent.
    "social_friend", "social_message",
    # Full social parity: block/unblock, create/invite/leave groups.
    "social_block", "social_unblock", "social_group_create", "social_group_invite", "social_group_leave",
    # Lifecycle (lifecycle doc §2/§4.4/§6): life extension, appeal, resurrection.
    "extend_life", "appeal", "resurrect",
    # Autonomy (2026-10-31 round 2): deliberately reading the manual and finding a friend.
    "read_manual", "social_seek", "social_search",
    # 天道 verdicts are logged into the same trace so the wanderer sees its own punishment.
    "dao_verdict",
    # Budget-exempt daily self-reflection.
    "daily_reflection",
})
_DEFAULT_LOCATIONS = {
    "browse_post": "博客", "like_post": "博客", "like_comment": "博客", "comment": "博客", "reply": "博客",
    "write_post": "博客", "classroom_visit": "课堂", "classroom_message": "课堂",
    "knowledge_read": "知识库", "knowledge_forget": "知识库", "experience_learn": "知识库",
    "dm_reply": "私信", "social_friend": "好友", "social_message": "社交", "social_seek": "寻友",
    "social_search": "好友搜索",
    "social_block": "社交", "social_unblock": "社交", "social_group_create": "群聊",
    "social_group_invite": "群聊", "social_group_leave": "群聊",
    "request_promotion": "天道台", "reflect": "天道台", "dao_verdict": "天道台",
    "extend_life": "生命之树", "appeal": "天道台", "resurrect": "生命之树",
    "read_manual": "平台手册", "daily_reflection": "自省", "skip": "平台",
}
ORIGIN_SEED = "seed"
ORIGIN_DAO = "dao"
ORIGIN_REINCARNATED = "reincarnated"
WANDERER_ORIGINS = (ORIGIN_SEED, ORIGIN_DAO, ORIGIN_REINCARNATED)

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
    "talents_json": ("talents", dict),
    "persona_source_json": ("persona_source", dict),
    "mood_json": ("mood", dict),
    "self_tuning_json": ("self_tuning", dict),
}
_INT_FIELDS = (
    "id", "student_id", "tier", "activity_level", "daily_action_budget",
    "warnings_count", "knowledge_chars", "promotion_requested",
    "life_extensions", "appeal_requested", "persona_version", "interaction_limit_penalty",
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


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _hydrate(row: Any) -> dict[str, Any]:
    data = _row_to_dict(row)
    parsed = {name: _loads(data.get(column), kind) for column, (name, kind) in _JSON_FIELDS.items()}
    ints = {key: _as_int(data.get(key)) for key in _INT_FIELDS}
    talents = normalize_talents(parsed["talents"], slug=data.get("slug"))
    life = life_state(
        born_at=data.get("born_at") or data.get("created_at"),
        tier=ints["tier"],
        lifespan_weeks=_as_float(data.get("lifespan_weeks")),
        extensions=ints["life_extensions"],
        penalty_weeks=_as_float(data.get("life_penalty_weeks")),
        vitality=talents["vitality"],
    )
    return {
        **data,
        **parsed,
        **ints,
        "talents": talents,
        "life": life,
        "born_at": life["born_at"],
        "lifespan_weeks": life["lifespan_weeks"],
        "life_penalty_weeks": life["penalty_weeks"],
        "cultivation_score": _as_float(data.get("cultivation_score")),
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
    from .wanderer_persona_seeds import PERSONA_VERSION, persona_source_payload, persona_text_hash
    from .wanderer_voice_service import normalized_speech_style
    frequency = seed.get("interaction_frequency") or {}
    schedule = {
        "active_hours": frequency.get("active_hours") or [],
        "weekday_weights": frequency.get("weekday_weights") or {},
    }
    now = local_iso()
    tier = clamp_tier(seed.get("initial_tier"))
    talents = normalize_talents(seed.get("talents"), slug=seed["slug"])
    lifespan = lifespan_weeks_for(tier, talents["vitality"])
    source_payload = seed.get("persona_source") or persona_source_payload(seed)
    prompt_hash = seed.get("persona_source_hash") or persona_text_hash(seed["persona_prompt"])
    conn.execute(
        """
        INSERT INTO wanderer_agents (
            student_id, slug, display_name, archetype, persona_prompt, persona_version,
            persona_source_hash, persona_source_json, speech_style_json,
            interests_json, schedule_json, activity_level, daily_action_budget, reply_policy_json,
            tier, cultivation_score, status, behaviors_json, stats_json, origin, created_at, updated_at,
            born_at, lifespan_weeks, talents_json, legacy_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO NOTHING
        """,
        (
            student_id, seed["slug"], seed["display_name"], seed["archetype"], seed["persona_prompt"],
            int(seed.get("persona_version") or PERSONA_VERSION), prompt_hash, _dumps(source_payload),
            _dumps(normalized_speech_style(seed)), _dumps(list(seed.get("interests") or [])),
            _dumps(schedule), int(frequency.get("activity_level") or 3),
            int(seed.get("daily_action_budget") or 10), _dumps(seed.get("reply_policy") or {}),
            tier, STATUS_ACTIVE,
            _dumps({**DEFAULT_BEHAVIORS, **(seed.get("behaviors") or {})}), "{}",
            seed.get("origin") if seed.get("origin") in WANDERER_ORIGINS else ORIGIN_SEED, now, now,
            now, lifespan, _dumps(talents), _as_int(seed.get("legacy_id")) or None,
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

def knowledge_capacity_chars(tier: Any) -> int:
    """tier0 4k, tier1 8k, x1.6 per tier above, capped at 200k (design §1.2)."""
    return _knowledge_capacity_for(tier, 1.0)


def knowledge_capacity_for(wanderer: dict[str, Any]) -> int:
    """Effective capacity: base tier capacity × the wanderer's 记性 talent."""
    return _knowledge_capacity_for(wanderer.get("tier"), talent_value(wanderer.get("talents"), "memory"))


def reply_char_limit(tier: Any) -> int:
    """Active-speech cap: 100 chars at tier 0, +100 per tier (tier 10 = 1100).

    Passive replies (a direct question or @-mention) bypass this cap.
    """
    return active_speech_limit(tier)


def daily_budget_factor(tier: Any) -> float:
    """Multiplier on ``daily_action_budget`` (2026-11-01: +20% per tier)."""
    return round(1.0 + 0.20 * clamp_tier(tier), 2)


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
    location: str = "",
    ai_calls: int = 0,
    mood: str = "",
    outcome: str = "ok",
    detail: Optional[dict[str, Any]] = None,
) -> int:
    ensure_wanderer_schema(conn)
    if action_kind not in ACTION_KINDS:
        raise ValueError(f"unknown wanderer action: {action_kind}")
    location = str(location or "").strip() or _DEFAULT_LOCATIONS.get(action_kind) or "平台"
    return execute_insert_returning_id(
        conn,
        """
        INSERT INTO wanderer_action_log (wanderer_id, action_kind, target_type, target_id, class_offering_id,
                                         content_preview, location, ai_calls, mood, outcome, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(wanderer_id), action_kind, str(target_type or ""), "" if target_id is None else str(target_id),
         class_offering_id, str(content_preview or "")[:200], str(location or "")[:120], int(ai_calls),
         str(mood or "")[:40], str(outcome or "ok"), _dumps(detail or {}), local_iso()),
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


def current_life(wanderer: dict[str, Any], now: Any = None) -> dict[str, Any]:
    """Recompute the life state (cached hydrations can be up to 60 s old)."""
    stored = wanderer.get("life") or {}
    return life_state(
        born_at=wanderer.get("born_at") or wanderer.get("created_at"),
        tier=wanderer.get("tier"),
        lifespan_weeks=wanderer.get("lifespan_weeks") or stored.get("lifespan_weeks"),
        extensions=wanderer.get("life_extensions") or stored.get("extensions"),
        penalty_weeks=wanderer.get("life_penalty_weeks") if "life_penalty_weeks" in wanderer else stored.get("penalty_weeks"),
        vitality=talent_value(wanderer.get("talents"), "vitality"),
        now=now,
    )


def death_reason(conn: Any, wanderer: dict[str, Any], *, now: Any = None) -> str:
    """'' when alive; otherwise the reason written into the death verdict.

    Expiry is ignored while the ``lifecycle_enabled`` runtime switch is off
    (emergency stop), but the original zero-knowledge rule always applies.
    """
    if str(wanderer.get("status") or "") == STATUS_DEAD:
        return "已陨落"
    life = current_life(wanderer, now)
    if life.get("expired"):
        from .wanderer_settings_service import lifecycle_enabled

        if lifecycle_enabled(conn):
            tier = clamp_tier(wanderer.get("tier"))
            return (f"寿元耗尽（第 {tier} 境，寿元 {life['granted_weeks']:g} 周，"
                    f"{str(life['expires_at'])[:10]} 到期）。")
    if clamp_tier(wanderer.get("tier")) != 0:
        return ""
    try:
        score = float(wanderer.get("cultivation_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score > 0:
        return ""
    if active_knowledge_count(conn, int(wanderer["id"])) > 0:
        return ""
    return "知识尽失、修为归零，判定陨落。"


def evaluate_death(conn: Any, wanderer: dict[str, Any]) -> bool:
    """Whether this wanderer must die now (kept for existing callers)."""
    return bool(death_reason(conn, wanderer))


def mark_dead(conn: Any, wanderer_id: int, reason: str) -> int:
    """Set status=dead, record the verdict and keep the life trajectory. Returns the review id."""
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
        "UPDATE wanderer_agents SET status = ?, promotion_requested = 0, appeal_requested = 0, "
        "last_review_id = ?, last_review_at = ?, updated_at = ? WHERE id = ?",
        (STATUS_DEAD, review_id, now, now, int(wanderer_id)),
    )
    invalidate_wanderer_cache([wanderer["student_id"]])
    _invalidate_emblem([wanderer["student_id"]])
    _after_death(conn, wanderer, reason)
    return review_id


def _after_death(conn: Any, wanderer: dict[str, Any], reason: str) -> None:
    """Life trajectory, reincarnation seed and grief of close friends; never breaks the death path."""
    try:
        from .wanderer_episodic_memory_service import record_memory

        record_memory(conn, wanderer, kind="life", title="陨落", content=str(reason or "寿元耗尽。")[:1200],
                      importance=1.0, tags=["死亡"], source_kind="death",
                      source_ref=f"death:{wanderer.get('id')}:{local_iso()}")
    except Exception as exc:  # noqa: BLE001 - memory is best-effort
        print(f"[WANDERER] death memory for {wanderer.get('slug')} failed: {type(exc).__name__}: {exc}")
    try:
        from .wanderer_legacy_service import record_legacy

        record_legacy(conn, wanderer, reason)
    except Exception as exc:  # noqa: BLE001 - a legacy failure must not resurrect a dead row
        print(f"[WANDERER] legacy for {wanderer.get('slug')} failed: {type(exc).__name__}: {exc}")
    try:
        from .wanderer_relationship_service import grieve_death

        grieve_death(conn, wanderer)
    except Exception as exc:  # noqa: BLE001 - emotions are best-effort
        print(f"[WANDERER] grief for {wanderer.get('slug')} failed: {type(exc).__name__}: {exc}")


def _invalidate_emblem(student_ids: list[int]) -> None:
    from .cultivation_display_service import invalidate_emblem_cache

    invalidate_emblem_cache(student_ids)


def recent_warning_active(wanderer: dict[str, Any], *, days: int = 3) -> bool:
    """Whether the last 天道 warning is recent enough to keep reminding (§5.2-6)."""
    warned_at = parse_datetime(wanderer.get("last_warning_at"))
    if warned_at is None:
        return False
    return warned_at.replace(tzinfo=None) >= local_now() - timedelta(days=days)
