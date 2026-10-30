"""Pure math for 散修 lifespan, talents, cultivation targets and tier perks.

No database, no I/O: every function is deterministic (except the RNG passed in),
so the balance tests can simulate lives without a model or a clock.

Source of truth for the numbers: docs/wanderer-lifecycle-2026-10-30.md §2–§4.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from ..time_utils import to_local_datetime

MAX_TIER = 10
TIER_COUNT = MAX_TIER + 1

# Week 2 at tier 0 rising to 100 weeks at tier 10 (design §2.1).
BASE_LIFESPAN_WEEKS: tuple[float, ...] = (2.0, 4.0, 6.0, 9.0, 13.0, 18.0, 25.0, 35.0, 48.0, 70.0, 100.0)
LIFESPAN_MIN_WEEKS = 1.0
LIFESPAN_MAX_WEEKS = 150.0
# Life cannot be extended past this many extra weeks (two one-week extensions).
MAX_LIFE_EXTENSIONS = 2
LIFE_EXTENSION_WEEKS = 1.0
LIFE_EXTENSION_COST_RATIO = 0.25
LIFE_EXTENSION_MIN_SCORE = 10.0
URGENT_REMAINING_DAYS = 14
CRITICAL_REMAINING_DAYS = 7

# Knowledge ratio needed to apply for the next tier (design §4.2).
PROMOTION_KNOWLEDGE_RATIO: tuple[float, ...] = (0.60, 0.60, 0.60, 0.55, 0.55, 0.50, 0.50, 0.45, 0.45, 0.40)
PROMOTION_COOLDOWN_DAYS = 7
PROMOTION_MIN_INTERACTIONS = 5
SCORE_CHARS_PER_POINT = 100.0
KNOWLEDGE_CAPACITY_MAX = 360_000

INTERACTION_DAILY_CULTIVATION_CAP = 6.0
INTERACTION_POINTS: dict[str, float] = {
    "comment": 0.8, "reply": 0.8, "dm_reply": 0.8, "classroom_message": 0.5,
    "social_message": 0.3, "write_post": 1.2,
}

APPEAL_MIN_TIER = 5
APPEAL_COOLDOWN_DAYS = 30
APPEAL_REJECTED_PENALTY_WEEKS = 0.5
RESURRECT_MIN_TIER = 10
RESURRECT_COST_WEEKS = 8.0
RESURRECT_COOLDOWN_DAYS = 30

TALENT_ORDER: tuple[str, ...] = ("insight", "memory", "vitality", "eloquence", "charm", "luck")
TALENTS: dict[str, dict[str, Any]] = {
    "insight": {"label": "悟性", "min": 0.60, "max": 1.80, "effect": "研读所得修为 ×悟性"},
    "memory": {"label": "记性", "min": 0.60, "max": 1.80, "effect": "知识容量 ×记性"},
    "vitality": {"label": "根骨", "min": 0.70, "max": 1.40, "effect": "寿元 ×根骨"},
    "eloquence": {"label": "口才", "min": 0.60, "max": 1.80, "effect": "互动所得修为 ×口才，升阶互动次数 ÷口才"},
    "charm": {"label": "亲和", "min": 0.60, "max": 1.80, "effect": "好感变化 ×亲和（负面 ÷亲和）"},
    "luck": {"label": "气运", "min": 0.60, "max": 1.60, "effect": "顿悟概率与天罚减免"},
}
DAO_TALENT_DELTA_LIMIT = 0.2
DAO_TALENT_CHANGES_PER_REVIEW = 2


def clamp_tier(tier: Any) -> int:
    try:
        return max(0, min(MAX_TIER, int(tier or 0)))
    except (TypeError, ValueError):
        return 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Talents
# ---------------------------------------------------------------------------

def talent_bounds(key: str) -> tuple[float, float]:
    spec = TALENTS.get(str(key))
    return (float(spec["min"]), float(spec["max"])) if spec else (0.6, 1.8)


def default_talents(slug: Any) -> dict[str, float]:
    """Stable per-slug talent vector: 0.75–1.30, plus one gift and one flaw."""
    digest = hashlib.sha256(str(slug or "wanderer").encode("utf-8")).digest()
    values: dict[str, float] = {}
    for index, key in enumerate(TALENT_ORDER):
        chunk = digest[index * 4:(index + 1) * 4]
        ratio = int.from_bytes(chunk, "big") / 0xFFFFFFFF
        values[key] = 0.75 + 0.55 * ratio
    gift = digest[24] % len(TALENT_ORDER)
    flaw = digest[25] % len(TALENT_ORDER)
    if flaw == gift:
        flaw = (flaw + 1) % len(TALENT_ORDER)
    values[TALENT_ORDER[gift]] += 0.22
    values[TALENT_ORDER[flaw]] -= 0.16
    return {key: round(_clamp(value, *talent_bounds(key)), 2) for key, value in values.items()}


def normalize_talents(raw: Any, *, slug: Any = "") -> dict[str, float]:
    """Full vector: present values clamped to bounds, missing ones from ``default_talents``."""
    base = default_talents(slug)
    source = raw if isinstance(raw, dict) else {}
    result: dict[str, float] = {}
    for key in TALENT_ORDER:
        try:
            value = float(source.get(key)) if key in source else base[key]
        except (TypeError, ValueError):
            value = base[key]
        result[key] = round(_clamp(value, *talent_bounds(key)), 2)
    return result


def talent_value(talents: Any, key: str) -> float:
    try:
        return float((talents or {}).get(key) or 1.0)
    except (TypeError, ValueError):
        return 1.0


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

def lifespan_weeks_for(tier: Any, vitality: Any = 1.0) -> float:
    base = BASE_LIFESPAN_WEEKS[clamp_tier(tier)]
    try:
        factor = float(vitality or 1.0)
    except (TypeError, ValueError):
        factor = 1.0
    return round(_clamp(base * factor, LIFESPAN_MIN_WEEKS, LIFESPAN_MAX_WEEKS), 1)


def life_state(
    *,
    born_at: Any,
    tier: Any,
    lifespan_weeks: Any,
    extensions: Any = 0,
    penalty_weeks: Any = 0,
    vitality: Any = 1.0,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Everything a prompt, page or test needs to know about one life.

    ``expires_at = born_at + (lifespan_weeks + extensions − penalty_weeks) weeks``.
    ``lifespan_weeks`` of 0/None falls back to the tier/vitality formula.
    """
    moment = now or datetime.now()
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    born = to_local_datetime(born_at) or moment
    try:
        weeks = float(lifespan_weeks or 0)
    except (TypeError, ValueError):
        weeks = 0.0
    if weeks <= 0:
        weeks = lifespan_weeks_for(tier, vitality)
    try:
        extension_count = max(0, min(MAX_LIFE_EXTENSIONS, int(extensions or 0)))
    except (TypeError, ValueError):
        extension_count = 0
    try:
        penalty = max(0.0, float(penalty_weeks or 0))
    except (TypeError, ValueError):
        penalty = 0.0
    granted = round(weeks + extension_count * LIFE_EXTENSION_WEEKS, 1)
    effective = max(0.0, granted - penalty)
    expires = born + timedelta(weeks=effective)
    remaining_seconds = (expires - moment).total_seconds()
    remaining_days = max(0, int(math.ceil(remaining_seconds / 86400.0)))
    lived_seconds = (moment - born).total_seconds()
    return {
        "born_at": born.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds"),
        "lifespan_weeks": round(weeks, 1),
        "granted_weeks": granted,
        "extensions": extension_count,
        "penalty_weeks": round(penalty, 1),
        "weeks_lived": round(max(0.0, lived_seconds) / 604800.0, 1),
        "remaining_weeks": round(max(0, remaining_days) / 7.0, 1),
        "remaining_days": remaining_days,
        "expired": remaining_days <= 0,
        "urgent": 0 < remaining_days <= URGENT_REMAINING_DAYS,
        "critical": 0 < remaining_days <= CRITICAL_REMAINING_DAYS,
    }


def can_extend_life(life: dict[str, Any], cultivation_score: Any) -> bool:
    try:
        score = float(cultivation_score or 0)
    except (TypeError, ValueError):
        score = 0.0
    return bool(
        int(life.get("extensions") or 0) < MAX_LIFE_EXTENSIONS
        and int(life.get("remaining_days") or 0) <= URGENT_REMAINING_DAYS
        and score >= LIFE_EXTENSION_MIN_SCORE
    )


def life_extension_cost(cultivation_score: Any) -> float:
    try:
        score = max(0.0, float(cultivation_score or 0))
    except (TypeError, ValueError):
        score = 0.0
    return round(score * LIFE_EXTENSION_COST_RATIO, 1)


# ---------------------------------------------------------------------------
# Cultivation + promotion
# ---------------------------------------------------------------------------

def base_knowledge_capacity(tier: Any) -> int:
    """tier0 4k, tier1 8k, ×1.6 per tier above, capped at 200k (base design §1.2)."""
    level = clamp_tier(tier)
    if level == 0:
        return 4000
    return int(min(200_000, round(8000 * (1.6 ** (level - 1)))))


def knowledge_capacity_for(tier: Any, memory: Any = 1.0) -> int:
    try:
        factor = max(0.2, float(memory or 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    return int(min(KNOWLEDGE_CAPACITY_MAX, round(base_knowledge_capacity(tier) * factor)))


def promotion_ratio(tier: Any) -> float:
    return PROMOTION_KNOWLEDGE_RATIO[clamp_tier(tier)]


def cultivation_target(tier: Any) -> int:
    """Absolute cultivation needed to apply from ``tier`` to ``tier+1``."""
    level = clamp_tier(tier)
    if level >= MAX_TIER:
        return 0
    value = 0.8 * promotion_ratio(level) * base_knowledge_capacity(level) / SCORE_CHARS_PER_POINT
    return max(10, int(round(value)))


def interaction_requirement(tier: Any, eloquence: Any = 1.0) -> int:
    try:
        factor = max(0.2, float(eloquence or 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    return max(3, int(round(PROMOTION_MIN_INTERACTIONS / factor)))


def knowledge_gain(chars: Any, insight: Any, luck: Any, rng: Optional[Callable[[], float]] = None) -> tuple[float, bool]:
    """(cultivation points, 顿悟 whether doubled)."""
    try:
        size = max(0.0, float(chars or 0))
        insight_factor = max(0.2, float(insight or 1.0))
        luck_factor = max(0.2, float(luck or 1.0))
    except (TypeError, ValueError):
        size, insight_factor, luck_factor = max(0.0, float(chars or 0)), 1.0, 1.0
    crit = rng is not None and rng() < min(0.30, 0.10 * luck_factor)
    points = size / SCORE_CHARS_PER_POINT * insight_factor * (2.0 if crit else 1.0)
    return round(points, 2), crit


def interaction_gain(action_kind: Any, eloquence: Any = 1.0) -> float:
    base = INTERACTION_POINTS.get(str(action_kind or ""), 0.0)
    try:
        factor = max(0.2, float(eloquence or 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    return round(base * factor, 2)


def tier_perks(tier: Any, daily_action_budget: Any = 10) -> dict[str, Any]:
    level = clamp_tier(tier)
    try:
        budget = max(0, int(daily_action_budget or 0))
    except (TypeError, ValueError):
        budget = 10
    return {
        "tier": level,
        "budget_factor": round(1.0 + 0.10 * level, 2),
        "daily_budget": int(round(budget * (1.0 + 0.10 * level))),
        "reply_char_limit": 80 + 30 * level,
        "knowledge_capacity": base_knowledge_capacity(level),
        "appeal_unlocked": level >= APPEAL_MIN_TIER,
        "resurrect_unlocked": level >= RESURRECT_MIN_TIER,
        "lifespan_weeks": BASE_LIFESPAN_WEEKS[level],
    }


def talent_effects(talents: Any) -> list[dict[str, Any]]:
    values = normalize_talents(talents)
    return [{"key": key, "label": TALENTS[key]["label"], "value": values[key],
             "effect": TALENTS[key]["effect"]} for key in TALENT_ORDER]


def effective_penalty_weeks(weeks: Any, luck: Any = 1.0) -> float:
    """天罚 deduction after 气运 mitigation (half-week granularity, minimum 0.5)."""
    try:
        raw = max(0.5, float(weeks or 0.5))
        factor = max(0.2, float(luck or 1.0))
    except (TypeError, ValueError):
        raw, factor = max(0.5, float(weeks or 0.5)), 1.0
    return max(0.5, math.floor(raw / factor * 2) / 2)
