"""Moderation states shared by every blog reader (plan §4).

Lifecycle ``status`` (draft / published) belongs to the author; the
``moderation_state`` belongs to the review system and moderators. Any query
outside ``blog_service`` that lists posts or comments to other people must use
the SQL fragments here, so that a pending, limited or removed item never leaks
through a side door (search, portfolio, AI context, opportunities…).
"""
from __future__ import annotations

STATE_NONE = "none"
STATE_PENDING = "pending"
STATE_PASSED = "passed"
STATE_FLAGGED = "flagged"
STATE_LIMITED = "limited"
STATE_REMOVED = "removed"
STATE_APPEAL_PENDING = "appeal_pending"

ALL_STATES = (
    STATE_NONE, STATE_PENDING, STATE_PASSED, STATE_FLAGGED,
    STATE_LIMITED, STATE_REMOVED, STATE_APPEAL_PENDING,
)
# Visible to everyone the audience rules allow. "flagged" stays visible: it is
# a queue marker for moderators, not a sanction (the author is not told).
PUBLIC_STATES = (STATE_NONE, STATE_PASSED, STATE_FLAGGED)
# Withheld from others; the author still sees their own item with a reason.
SANCTIONED_STATES = (STATE_LIMITED, STATE_REMOVED, STATE_APPEAL_PENDING)

REASON_LABELS = {
    "spam": "广告或引流",
    "abuse": "攻击、骚扰或不友善",
    "privacy": "泄露个人隐私",
    "illegal": "违法违规",
    "misinformation": "不实信息",
    "off_topic": "与板块无关",
    "other": "其他",
}

STATE_LABELS = {
    STATE_NONE: "正常",
    STATE_PENDING: "审核中",
    STATE_PASSED: "已通过",
    STATE_FLAGGED: "待复核",
    STATE_LIMITED: "仅自己可见",
    STATE_REMOVED: "已移除",
    STATE_APPEAL_PENDING: "申诉中",
}

# moderated_by values written by the system (the review desk groups by them).
BY_AI = "system:ai"
BY_LOCAL_RULES = "system:l1"
BY_TIMEOUT = "system:timeout"
BY_QUOTA = "system:quota"
BY_REPORTS = "system:reports"
# A sanctioned post its author edited: only a person may release it (the AI
# verdict is advisory, and no timeout/quota/failure fallback may publish it).
BY_RESUBMIT = "system:resubmit"
EMERGENCY_PREFIX = "emergency:"


def _literal_list(values: tuple[str, ...]) -> str:
    # Constants only (never user input), so inlining them is safe.
    return ", ".join(f"'{value}'" for value in values)


def public_state_sql(alias: str = "") -> str:
    """SQL condition: the row's moderation state lets others see it."""
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}moderation_state, '{STATE_NONE}') IN ({_literal_list(PUBLIC_STATES)})"


def public_post_sql(alias: str = "") -> str:
    """Published and not withheld: the baseline for any cross-user post list."""
    prefix = f"{alias}." if alias else ""
    return f"{prefix}status = 'published' AND {public_state_sql(alias)}"


def is_public_state(state: object) -> bool:
    return str(state or STATE_NONE) in PUBLIC_STATES


def reason_label(code: object) -> str:
    return REASON_LABELS.get(str(code or ""), "")


def normalize_reason_code(code: object) -> str:
    """A known reason code, or "" when none/unknown was given."""
    normalized = str(code or "").strip().lower()
    return normalized if normalized in REASON_LABELS else ""
