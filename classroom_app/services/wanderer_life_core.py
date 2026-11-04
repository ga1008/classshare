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

# A self-initiated turn is short and grows by 100 chars per tier: 0→100, 10→1100.
# Direct questions/@-mentions (passive) are not capped by this; the model's own
# output limit is the only ceiling.
ACTIVE_SPEECH_BASE_CHARS = 100

# Knowledge ratio needed to apply for the next tier (design §4.2). The ratio
# rises with tier so the absolute target keeps growing even after the knowledge
# capacity cap, and the ladder never goes backwards.
PROMOTION_KNOWLEDGE_RATIO: tuple[float, ...] = (0.35, 0.38, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55)
# Absolute cultivation for each tier → next tier (2026-11-03 rebalance).
# Higher tiers need more, but their per-action gains scale as well, so a
# diligent life can climb from 0 to 10 within its lifespan.
TIER_CULTIVATION_TARGETS: tuple[int, ...] = (24, 48, 90, 160, 280, 460, 720, 1080, 1560, 2200)
# Income grows with the tier: reading and interacting at a higher realm is
# worth more per action (the user-facing "获得修为的能力越强").
TIER_KNOWLEDGE_GAIN_BONUS = 0.10
TIER_INTERACTION_GAIN_BONUS = 0.08
PROMOTION_COOLDOWN_DAYS = 7
PROMOTION_MIN_INTERACTIONS = 5
SCORE_CHARS_PER_POINT = 100.0
KNOWLEDGE_CAPACITY_MAX = 360_000

# Real users are the richest source: a reply can reach several times a normal
# interaction, so the daily cap must leave room to actually accumulate it.
INTERACTION_DAILY_CULTIVATION_CAP = 60.0
INTERACTION_POINTS: dict[str, float] = {
    "comment": 0.8, "reply": 0.8, "dm_reply": 0.8, "classroom_message": 0.5,
    "social_message": 0.3, "write_post": 1.2,
}
# A real user's reply is worth at least 2× and at most 5× the same action aimed
# at a wanderer; their tier and the reply length move it inside that band.
REAL_USER_INTERACTION_MULTIPLIER = 2.0
REAL_USER_INTERACTION_MAX = 5.0
REAL_USER_TIER_BONUS = 0.20
REAL_USER_LENGTH_CHARS = 200.0
# Base daily active-interaction allowance and per-tier growth (2026-11-01).
ACTIVE_INTERACTION_BASE = 20
ACTIVE_INTERACTION_TIER_BONUS = 0.20
# Base daily web-search allowance and per-tier growth.
WANDERER_SEARCH_BASE = 12
WANDERER_SEARCH_TIER_BONUS = 0.20
# Each successful web search adds cultivation; higher tiers learn more from it.
SEARCH_CULTIVATION_BASE = 0.5
SEARCH_CULTIVATION_TIER_BONUS = 0.25

# Knowledge and reflection grow with the realm (2026-11-04). A tier-0 wanderer
# keeps a 1000-char note (the old write cap was 800); each tier adds 200,
# capped at 3000. Prompt excerpts grow more slowly so a high-tier prompt still
# fits the budget.
KNOWLEDGE_DIGEST_BASE_CHARS = 1000
KNOWLEDGE_DIGEST_TIER_STEP = 200
KNOWLEDGE_DIGEST_MAX_CHARS = 3000
KNOWLEDGE_PROMPT_CAP_BASE = 200
KNOWLEDGE_PROMPT_CAP_TIER_STEP = 60
KNOWLEDGE_PROMPT_CAP_MAX = 900
REFLECTION_SUMMARY_BASE_CHARS = 1200
REFLECTION_SUMMARY_TIER_STEP = 200
REFLECTION_SUMMARY_MAX_CHARS = 3200

# Personal to-do list capacity: 3 at tier 0, +3 per tier.
TODO_CAPACITY_PER_TIER = 3
# Lifespan gifts (tier ≥ 3): at most a quarter of what is left, and never the
# last week, so a gift can never be a disguised way to die early.
GIFT_MIN_TIER = 3
GIFT_MAX_RATIO = 0.25
GIFT_MIN_WEEKS = 0.5
GIFT_KEEP_WEEKS = 1.0

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
    gift_in_weeks: Any = 0,
    gift_out_weeks: Any = 0,
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
    try:
        gift_in = max(0.0, float(gift_in_weeks or 0))
        gift_out = max(0.0, float(gift_out_weeks or 0))
    except (TypeError, ValueError):
        gift_in, gift_out = 0.0, 0.0
    granted = round(weeks + extension_count * LIFE_EXTENSION_WEEKS + gift_in, 1)
    effective = max(0.0, granted - penalty - gift_out)
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
        "gift_in_weeks": round(gift_in, 1),
        "gift_out_weeks": round(gift_out, 1),
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


def knowledge_digest_limit(tier: Any) -> int:
    """Stored digest chars per knowledge note: 1000 at tier 0, +200 per tier."""
    return min(KNOWLEDGE_DIGEST_MAX_CHARS,
               KNOWLEDGE_DIGEST_BASE_CHARS + KNOWLEDGE_DIGEST_TIER_STEP * clamp_tier(tier))


def knowledge_prompt_cap(tier: Any) -> int:
    """How much of one note the prompt shows: 200 at tier 0, +60 per tier."""
    return min(KNOWLEDGE_PROMPT_CAP_MAX,
               KNOWLEDGE_PROMPT_CAP_BASE + KNOWLEDGE_PROMPT_CAP_TIER_STEP * clamp_tier(tier))


def reflection_summary_limit(tier: Any) -> int:
    """Stored daily-reflection summary chars: 1200 at tier 0, +200 per tier."""
    return min(REFLECTION_SUMMARY_MAX_CHARS,
               REFLECTION_SUMMARY_BASE_CHARS + REFLECTION_SUMMARY_TIER_STEP * clamp_tier(tier))


def todo_capacity(tier: Any) -> int:
    """Open to-do slots: 3 at tier 0, +3 per tier (tier 10 → 33)."""
    return TODO_CAPACITY_PER_TIER * (clamp_tier(tier) + 1)


def max_gift_weeks(remaining_weeks: Any) -> float:
    """Largest lifespan gift this wanderer may offer right now (0 when ineligible)."""
    try:
        left = max(0.0, float(remaining_weeks or 0))
    except (TypeError, ValueError):
        left = 0.0
    room = min(left * GIFT_MAX_RATIO, left - GIFT_KEEP_WEEKS)
    if room < GIFT_MIN_WEEKS:
        return 0.0
    return round(room, 1)


def cultivation_target(tier: Any) -> int:
    """Absolute cultivation needed to apply from ``tier`` to ``tier+1``."""
    level = clamp_tier(tier)
    if level >= MAX_TIER:
        return 0
    return TIER_CULTIVATION_TARGETS[level]


def tier_income_multiplier(tier: Any, *, knowledge: bool = True) -> float:
    """Per-action cultivation multiplier: 1.0 at tier 0, +10% (reading) / +8% (interaction) per tier."""
    bonus = TIER_KNOWLEDGE_GAIN_BONUS if knowledge else TIER_INTERACTION_GAIN_BONUS
    return round(1.0 + bonus * clamp_tier(tier), 2)


def promotion_feasibility(tier: Any, *, studies_per_day: float = 3.0,
                          insight: float = 1.0, memory: float = 1.0) -> dict[str, Any]:
    """How many active days a diligent wanderer needs for the next tier.

    ``studies_per_day`` is deliberate reading; each reading fixes at most 800
    digest chars. Returns the two binding budgets (knowledge chars, cultivation)
    and whether both fit inside the tier's lifespan increment.
    """
    level = clamp_tier(tier)
    if level >= MAX_TIER:
        return {"tier": level, "max_tier": True, "fits": True, "days_needed": 0, "days_available": 0}
    capacity = knowledge_capacity_for(level, memory)
    knowledge_needed = int(round(promotion_ratio(level) * capacity))
    knowledge_per_study = 800.0
    cultivation_per_study = knowledge_per_study / SCORE_CHARS_PER_POINT * max(0.2, insight) * tier_income_multiplier(level)
    cultivation_needed = cultivation_target(level)
    studies = max(knowledge_needed / knowledge_per_study, cultivation_needed / max(0.01, cultivation_per_study))
    days_needed = int(math.ceil(studies / max(0.1, studies_per_day)))
    days_available = int(round((BASE_LIFESPAN_WEEKS[level + 1] - BASE_LIFESPAN_WEEKS[level]) * 7))
    return {
        "tier": level,
        "knowledge_needed": knowledge_needed,
        "cultivation_needed": cultivation_needed,
        "days_needed": days_needed,
        "days_available": days_available,
        "fits": days_needed <= max(1, int(days_available * 0.6)),
        "max_tier": False,
    }


def interaction_requirement(tier: Any, eloquence: Any = 1.0) -> int:
    try:
        factor = max(0.2, float(eloquence or 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    return max(3, int(round(PROMOTION_MIN_INTERACTIONS / factor)))


def knowledge_gain(chars: Any, insight: Any, luck: Any, rng: Optional[Callable[[], float]] = None,
                   *, tier: Any = 0) -> tuple[float, bool]:
    """(cultivation points, 顿悟 whether doubled); higher tiers learn faster."""
    try:
        size = max(0.0, float(chars or 0))
        insight_factor = max(0.2, float(insight or 1.0))
        luck_factor = max(0.2, float(luck or 1.0))
    except (TypeError, ValueError):
        size, insight_factor, luck_factor = max(0.0, float(chars or 0)), 1.0, 1.0
    crit = rng is not None and rng() < min(0.30, 0.10 * luck_factor)
    points = (size / SCORE_CHARS_PER_POINT * insight_factor * tier_income_multiplier(tier)
              * (2.0 if crit else 1.0))
    return round(points, 2), crit


def interaction_gain(action_kind: Any, eloquence: Any = 1.0, *, real_user: bool = False,
                     target_tier: Any = 0, chars: Any = 0, source_tier: Any = 0) -> float:
    """Cultivation for one interaction; a real user's reply is the richest channel.

    ``real_user`` applies the tier bonus (each real-user tier +20%) and a length
    bonus (longer replies are worth more), capped at 5× the normal base.
    ``source_tier`` is the acting wanderer's own tier: higher realms earn more.
    """
    base = INTERACTION_POINTS.get(str(action_kind or ""), 0.0)
    if base <= 0:
        return 0.0
    try:
        factor = max(0.2, float(eloquence or 1.0)) * tier_income_multiplier(source_tier, knowledge=False)
    except (TypeError, ValueError):
        factor = tier_income_multiplier(source_tier, knowledge=False)
    if real_user:
        try:
            length = max(0.0, float(chars or 0))
        except (TypeError, ValueError):
            length = 0.0
        tier_factor = 1.0 + REAL_USER_TIER_BONUS * clamp_tier(target_tier)
        length_factor = 1.0 + min(1.0, length / REAL_USER_LENGTH_CHARS)
        factor *= min(REAL_USER_INTERACTION_MAX,
                      REAL_USER_INTERACTION_MULTIPLIER * tier_factor * length_factor)
    return round(base * factor, 2)


def active_speech_limit(tier: Any) -> int:
    """0 阶 100 字，每升一阶 +100 字，10 阶 1100 字（只约束主动发言）。"""
    return ACTIVE_SPEECH_BASE_CHARS * (clamp_tier(tier) + 1)


def active_interaction_limit(tier: Any, configured: Any = 0) -> int:
    """Base 20 active interactions per day, +20% per tier; never below the configured floor."""
    level = clamp_tier(tier)
    base = int(round(ACTIVE_INTERACTION_BASE * (1.0 + ACTIVE_INTERACTION_TIER_BONUS * level)))
    try:
        configured_value = max(0, int(configured or 0))
    except (TypeError, ValueError):
        configured_value = 0
    return max(base, configured_value)


def wanderer_search_limit(tier: Any) -> int:
    """Base 12 web searches per day, +20% per tier."""
    return int(round(WANDERER_SEARCH_BASE * (1.0 + WANDERER_SEARCH_TIER_BONUS * clamp_tier(tier))))


def search_cultivation_gain(tier: Any) -> float:
    """Cultivation for one successful search: 0.5 at tier 0, +0.25 per tier."""
    return round(SEARCH_CULTIVATION_BASE + SEARCH_CULTIVATION_TIER_BONUS * clamp_tier(tier), 2)


def tier_perks(tier: Any, daily_action_budget: Any = 10) -> dict[str, Any]:
    level = clamp_tier(tier)
    try:
        budget = max(0, int(daily_action_budget or 0))
    except (TypeError, ValueError):
        budget = 10
    effective_budget = active_interaction_limit(level, budget)
    return {
        "tier": level,
        "budget_factor": round(1.0 + ACTIVE_INTERACTION_TIER_BONUS * level, 2),
        "daily_budget": effective_budget,
        "active_interaction_limit": effective_budget,
        "web_search_limit": wanderer_search_limit(level),
        "reply_char_limit": active_speech_limit(level),
        "active_speech_limit": active_speech_limit(level),
        "passive_speech_unlimited": True,
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
