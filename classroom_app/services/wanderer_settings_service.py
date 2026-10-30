"""Runtime settings and daily counters for the 散修 system (one place).

Backed by ``wanderer_runtime_settings(key, value, updated_at)``. Values are
stored as TEXT; callers convert. The daily AI-call counter lives under
``ai_calls:<YYYY-MM-DD>`` and is incremented atomically with an upsert so
concurrent workers never lose a count.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Optional

from ..db.schema_wanderers import ensure_wanderer_schema
from ..time_utils import local_iso, local_now

DEFAULT_DAILY_AI_CALL_CAP = 400
DAILY_AI_CALLS_PREFIX = "ai_calls:"
SETTING_DAILY_AI_CALL_CAP = "daily_ai_call_cap"
SETTING_ENABLED = "enabled"
SETTING_TICK_BATCH_SIZE = "tick_batch_size"
SETTING_CLASSROOM_VISITS_ENABLED = "classroom_visits_enabled"
SETTING_DAO_REVIEW_HOUR = "dao_review_hour"
SETTING_LAST_TICK_AT = "last_tick_at"
SETTING_LAST_DAO_REVIEW_AT = "last_dao_review_at"
SETTING_DAO_MENTION_DAILY_CAP = "dao_mention_daily_cap"
DAO_MENTION_CALLS_PREFIX = "dao_mention_calls:"
DEFAULT_DAO_MENTION_DAILY_CAP = 60
SETTING_TICK_MAX_SECONDS = "tick_max_seconds"
DEFAULT_TICK_MAX_SECONDS = 150
SETTING_LIFECYCLE_ENABLED = "lifecycle_enabled"
SETTING_LAST_MANUAL_TICK_SUMMARY = "last_manual_tick_summary"
SETTING_LAST_MANUAL_REVIEW_SUMMARY = "last_manual_review_summary"
SETTING_DAO_REVIEW_HOUR_ARMED = "dao_review_hour_armed"
DEFAULT_TICK_BATCH_SIZE = 6
DEFAULT_DAO_REVIEW_HOUR = "03:30"
INTERNAL_TOKEN_HEADER = "X-Wanderer-Internal-Token"
_INTERNAL_TOKEN_CONTEXT = b"wanderer-internal"


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def wanderer_runtime_setting_get(conn: Any, key: str, default: Optional[str] = None) -> Optional[str]:
    ensure_wanderer_schema(conn)
    row = conn.execute(
        "SELECT value FROM wanderer_runtime_settings WHERE key = ? LIMIT 1", (str(key),)
    ).fetchone()
    if row is None:
        return default
    value = _row_value(row, "value")
    return default if value is None else str(value)


def wanderer_runtime_setting_set(conn: Any, key: str, value: Any) -> str:
    ensure_wanderer_schema(conn)
    text = "" if value is None else str(value)
    conn.execute(
        """
        INSERT INTO wanderer_runtime_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (str(key), text, local_iso()),
    )
    return text


def daily_ai_calls_key() -> str:
    return f"{DAILY_AI_CALLS_PREFIX}{local_now().date().isoformat()}"


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def daily_cap(conn: Any) -> int:
    """Runtime setting ``daily_ai_call_cap`` wins; else env; else 400."""
    raw = wanderer_runtime_setting_get(conn, SETTING_DAILY_AI_CALL_CAP)
    if raw not in (None, ""):
        try:
            return max(0, int(float(raw)))
        except ValueError:
            pass
    return max(0, _env_int("WANDERER_DAILY_AI_CALL_CAP", DEFAULT_DAILY_AI_CALL_CAP))


def counter_value(conn: Any, key: str) -> int:
    raw = wanderer_runtime_setting_get(conn, key, "0")
    try:
        return int(float(raw or 0))
    except ValueError:
        return 0


def increment_counter(conn: Any, key: str, amount: int = 1) -> int:
    """Atomically add ``amount`` to the counter ``key`` and return the new total."""
    ensure_wanderer_schema(conn)
    step = max(0, int(amount))
    conn.execute(
        """
        INSERT INTO wanderer_runtime_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = CAST(CAST(wanderer_runtime_settings.value AS INTEGER) + ? AS TEXT),
            updated_at = excluded.updated_at
        """,
        (str(key), str(step), local_iso(), step),
    )
    return counter_value(conn, key)


def daily_ai_calls(conn: Any) -> int:
    return counter_value(conn, daily_ai_calls_key())


def increment_daily_ai_calls(conn: Any, amount: int = 1) -> int:
    """Atomically add ``amount`` to today's counter and return the new total."""
    return increment_counter(conn, daily_ai_calls_key(), amount)


def dao_mention_calls_key() -> str:
    return f"{DAO_MENTION_CALLS_PREFIX}{local_now().date().isoformat()}"


def dao_mention_daily_cap(conn: Any) -> int:
    """Max @天道 replies (model calls) per day; runtime setting, default 60."""
    return _setting_int(conn, SETTING_DAO_MENTION_DAILY_CAP, DEFAULT_DAO_MENTION_DAILY_CAP, low=0, high=10_000)


def dao_mention_cap_reached(conn: Any) -> bool:
    return counter_value(conn, dao_mention_calls_key()) >= dao_mention_daily_cap(conn)


def tick_max_seconds(conn: Any) -> int:
    """Wall-clock limit of one tick (runtime setting, default 150s, 10..3600)."""
    return _setting_int(conn, SETTING_TICK_MAX_SECONDS, DEFAULT_TICK_MAX_SECONDS, low=10, high=3600)


def is_wanderer_runtime_enabled(conn: Any) -> bool:
    """Both the env switch and the runtime switch must be on (design §5.5)."""
    env_on = str(os.getenv("WANDERER_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if not env_on:
        return False
    return str(wanderer_runtime_setting_get(conn, SETTING_ENABLED, "1")).strip() in {"1", "true"}


def _setting_int(conn: Any, key: str, default: int, *, low: int, high: int) -> int:
    raw = wanderer_runtime_setting_get(conn, key)
    try:
        value = int(float(raw)) if raw not in (None, "") else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def tick_batch_size(conn: Any) -> int:
    """Wanderers handled per tick (runtime setting, default 6, 1..20)."""
    return _setting_int(conn, SETTING_TICK_BATCH_SIZE, DEFAULT_TICK_BATCH_SIZE, low=1, high=20)


def classroom_visits_enabled(conn: Any) -> bool:
    return str(wanderer_runtime_setting_get(conn, SETTING_CLASSROOM_VISITS_ENABLED, "1")).strip() in {"1", "true"}


def lifecycle_enabled(conn: Any) -> bool:
    """Emergency stop for lifespan deaths only (lifecycle doc §11, default on)."""
    return str(wanderer_runtime_setting_get(conn, SETTING_LIFECYCLE_ENABLED, "1")).strip().lower() in {"1", "true"}


def parse_review_hour(raw: Any) -> tuple[int, int]:
    """``"03:30"`` / ``"3"`` / ``"3.5"`` -> (hour, minute); anything invalid -> 03:30."""
    text = str(raw or "").strip()
    try:
        if ":" in text:
            hour_text, minute_text = text.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
        else:
            number = float(text)
            hour, minute = int(number), int(round((number - int(number)) * 60))
    except ValueError:
        return 3, 30
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 3, 30
    return hour, minute


def dao_review_hour(conn: Any) -> tuple[int, int]:
    return parse_review_hour(wanderer_runtime_setting_get(conn, SETTING_DAO_REVIEW_HOUR, DEFAULT_DAO_REVIEW_HOUR))


def wanderer_internal_token() -> str:
    """Shared secret for /api/internal/wanderers/* (scheduler -> app).

    ``WANDERER_INTERNAL_TOKEN`` when set; otherwise an HMAC-SHA256 derivation of
    ``SECRET_KEY`` so both containers agree without extra configuration.
    """
    explicit = str(os.getenv("WANDERER_INTERNAL_TOKEN", "")).strip()
    if explicit:
        return explicit
    from .. import config

    secret = str(getattr(config, "SECRET_KEY", "") or "").encode("utf-8")
    return hmac.new(secret, _INTERNAL_TOKEN_CONTEXT, hashlib.sha256).hexdigest()


def internal_token_matches(candidate: Any) -> bool:
    expected = wanderer_internal_token()
    return bool(expected) and hmac.compare_digest(str(candidate or ""), expected)


def main_app_internal_url() -> str:
    """Base URL of the main app as seen from the scheduler container."""
    explicit = str(os.getenv("MAIN_APP_INTERNAL_URL", "")).strip().rstrip("/")
    if explicit:
        return explicit
    from .. import config

    return f"http://127.0.0.1:{getattr(config, 'PORT', 8000)}"


# ---------------------------------------------------------------------------
# Round 2 (design §10): 天道造人 and wanderer blog posts
# ---------------------------------------------------------------------------

SETTING_GENESIS_ENABLED = "genesis_enabled"
SETTING_GENESIS_MIN_REAL_ACTIONS_24H = "genesis_min_real_actions_24h"
SETTING_GENESIS_MAX_TOTAL = "genesis_max_total"
SETTING_GENESIS_MAX_PER_DAY = "genesis_max_per_day"
SETTING_GENESIS_MAX_PER_WEEK = "genesis_max_per_week"
SETTING_BLOG_POSTS_DAILY_CAP = "blog_posts_daily_cap"
SETTING_LAST_MANUAL_GENESIS_SUMMARY = "last_manual_genesis_summary"
# key -> (default, low, high); the admin form validates against the same bounds.
GENESIS_INT_SETTINGS = {
    SETTING_GENESIS_MIN_REAL_ACTIONS_24H: (20, 0, 100_000),
    SETTING_GENESIS_MAX_TOTAL: (50, 0, 500),
    SETTING_GENESIS_MAX_PER_DAY: (2, 0, 20),
    SETTING_GENESIS_MAX_PER_WEEK: (5, 0, 100),
}
BLOG_POSTS_DAILY_CAP_BOUNDS = (6, 0, 200)


def genesis_enabled(conn: Any) -> bool:
    return str(wanderer_runtime_setting_get(conn, SETTING_GENESIS_ENABLED, "1")).strip().lower() in {"1", "true"}


def genesis_setting(conn: Any, key: str) -> int:
    default, low, high = GENESIS_INT_SETTINGS[key]
    return _setting_int(conn, key, default, low=low, high=high)


def genesis_settings(conn: Any) -> dict[str, Any]:
    return {"genesis_enabled": genesis_enabled(conn),
            **{key: genesis_setting(conn, key) for key in GENESIS_INT_SETTINGS}}


def blog_posts_daily_cap(conn: Any) -> int:
    """Wanderer blog posts per day across all wanderers (runtime setting, default 6)."""
    default, low, high = BLOG_POSTS_DAILY_CAP_BOUNDS
    return _setting_int(conn, SETTING_BLOG_POSTS_DAILY_CAP, default, low=low, high=high)
