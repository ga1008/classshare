"""Blog content safety, synchronous half (plan §5.1–§5.2).

L1 local rules, posting rate limits, the initial moderation state of a new or
edited item, and enqueueing the L2 AI review in the caller's transaction. The
asynchronous review itself lives in ``blog_safety_review`` (scheduler side), so
request handlers never import the AI client.

Who is reviewed by AI (plan §5.1, D5): student posts and student comments.
Teacher and assistant content only passes L1.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import blog_moderation_states as states
from .rate_limit_service import (
    RateLimitExceededError,
    build_rate_limit_window_start,
    calculate_retry_after_seconds,
)
from .scheduled_task_service import schedule_task

REVIEW_TASK_KIND = "blog_content_review"
REVIEW_TIMEOUT_TASK_KIND = "blog_content_review_timeout"
REVIEW_LAYER_AI = "ai"
REVIEW_MAX_ATTEMPTS = 3
REVIEW_TASK_PRIORITY = 80
# D1: a post waits for review at most this long, then becomes visible as flagged.
REVIEW_TIMEOUT_MINUTES = 10
RATE_WINDOW_SECONDS = 60
POSTS_PER_WINDOW = 3
COMMENTS_PER_WINDOW = 10

LEVEL_CLEAN = "clean"
LEVEL_FLAG = "flag"
LEVEL_BLOCK = "block"

# Built-in terms; admins add or disable terms in blog_safety_terms (a row with
# the same term overrides the built-in one, enabled=0 switches it off).
BUILTIN_TERMS: tuple[tuple[str, str, str], ...] = (
    ("代开发票", LEVEL_BLOCK, "illegal"),
    ("办假证", LEVEL_BLOCK, "illegal"),
    ("出售身份证", LEVEL_BLOCK, "illegal"),
    ("贩卖银行卡", LEVEL_BLOCK, "illegal"),
    ("出售银行卡", LEVEL_BLOCK, "illegal"),
    ("枪支出售", LEVEL_BLOCK, "illegal"),
    ("赌博网站", LEVEL_BLOCK, "illegal"),
    ("博彩网址", LEVEL_BLOCK, "illegal"),
    ("裸聊", LEVEL_BLOCK, "illegal"),
    ("代写论文", LEVEL_BLOCK, "spam"),
    ("论文代写", LEVEL_BLOCK, "spam"),
    ("代考", LEVEL_BLOCK, "spam"),
    ("刷单返利", LEVEL_BLOCK, "spam"),
    ("兼职刷单", LEVEL_BLOCK, "spam"),
    ("加微信", LEVEL_FLAG, "spam"),
    ("加v信", LEVEL_FLAG, "spam"),
    ("加vx", LEVEL_FLAG, "spam"),
    ("私聊领取", LEVEL_FLAG, "spam"),
    ("扫码领取", LEVEL_FLAG, "spam"),
    ("日结兼职", LEVEL_FLAG, "spam"),
    ("脑残", LEVEL_FLAG, "abuse"),
    ("智障", LEVEL_FLAG, "abuse"),
    ("去死吧", LEVEL_FLAG, "abuse"),
    ("傻逼", LEVEL_FLAG, "abuse"),
)
VALID_LEVELS = {LEVEL_FLAG, LEVEL_BLOCK}

# Personal information (plan §5.1): flagged, never blocked. The frontend runs
# the same patterns before publishing and asks the author to confirm.
PRIVACY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<![\dXx])\d{17}[\dXx](?![\dXx])"),
    re.compile(r"(?:qq|扣扣)\s*[:：号]?\s*\d{5,11}", re.I),
    re.compile(r"(?:微信|vx|wx|v信)\s*[:：号]?\s*[a-z][-_a-z0-9]{5,19}", re.I),
)
_SPACING = re.compile(r"[\s\u200b-\u200f\u2060\ufeff]+")


class BlogSafetyBlocked(ValueError):
    """L1 refused the text; the message names categories, never the term."""

    def __init__(self, categories: list[str]):
        labels = "、".join(states.reason_label(code) or "其他" for code in categories) or "不允许发布的内容"
        super().__init__(f"内容包含不允许发布的信息（{labels}），请修改后再提交")
        self.categories = categories


@dataclass(frozen=True)
class LocalVerdict:
    level: str = LEVEL_CLEAN
    categories: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.level == LEVEL_FLAG


@dataclass(frozen=True)
class ReviewPlan:
    """Initial moderation fields for a write, and whether AI must look at it."""

    state: str
    moderated_by: str
    needs_ai: bool


def _now() -> datetime:
    return datetime.now()


def ai_review_enabled() -> bool:
    return str(os.getenv("BLOG_SAFETY_AI_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}


def daily_ai_limit() -> int:
    try:
        return max(int(os.getenv("BLOG_SAFETY_DAILY_LIMIT", "2000")), 0)
    except ValueError:
        return 2000


def _active_terms(conn) -> list[tuple[str, str, str]]:
    terms = {term: (level, category) for term, level, category in BUILTIN_TERMS}
    try:
        rows = conn.execute("SELECT term, level, category, enabled FROM blog_safety_terms").fetchall()
    except Exception:  # noqa: BLE001 - table absent in a minimal test schema
        rows = []
    for row in rows:
        term = _SPACING.sub("", str(row["term"] or "")).lower()
        if not term:
            continue
        if not int(row["enabled"] or 0):
            terms.pop(term, None)
            continue
        level = str(row["level"] or LEVEL_FLAG)
        terms[term] = (level if level in VALID_LEVELS else LEVEL_FLAG,
                       states.normalize_reason_code(row["category"]) or "other")
    return [(term, level, category) for term, (level, category) in terms.items()]


def check_text(conn, *parts: Any) -> LocalVerdict:
    """L1: block / flag / clean for the given text parts (title, body, tags)."""
    text = "\n".join(str(part or "") for part in parts)
    if not text.strip():
        return LocalVerdict()
    compact = _SPACING.sub("", text).lower()
    blocked: list[str] = []
    flagged: list[str] = []
    for term, level, category in _active_terms(conn):
        if term in compact:
            (blocked if level == LEVEL_BLOCK else flagged).append(category)
    if any(pattern.search(text) for pattern in PRIVACY_PATTERNS):
        flagged.append("privacy")
    if blocked:
        return LocalVerdict(LEVEL_BLOCK, sorted(set(blocked)))
    if flagged:
        return LocalVerdict(LEVEL_FLAG, sorted(set(flagged)))
    return LocalVerdict()


def enforce_local_rules(conn, *parts: Any) -> LocalVerdict:
    verdict = check_text(conn, *parts)
    if verdict.level == LEVEL_BLOCK:
        raise BlogSafetyBlocked(verdict.categories)
    return verdict


def _enforce_rate(conn, sql: str, identity: str, limit: int, message: str) -> None:
    now, window_start = build_rate_limit_window_start(window_seconds=RATE_WINDOW_SECONDS)
    row = conn.execute(sql, (identity, window_start)).fetchone()
    count = int((row["total"] if row else 0) or 0)
    if count >= limit:
        raise RateLimitExceededError(
            message,
            retry_after_seconds=calculate_retry_after_seconds(
                oldest_event_at=str(row["oldest"] or "") if row else "",
                window_seconds=RATE_WINDOW_SECONDS, now=now,
            ),
        )


def enforce_post_rate_limit(conn, identity: str) -> None:
    _enforce_rate(
        conn,
        "SELECT COUNT(*) AS total, MIN(created_at) AS oldest FROM blog_posts WHERE author_identity = ? AND created_at >= ?",
        identity, POSTS_PER_WINDOW, "发帖太频繁了，请稍后再试",
    )


def enforce_comment_rate_limit(conn, identity: str) -> None:
    _enforce_rate(
        conn,
        "SELECT COUNT(*) AS total, MIN(created_at) AS oldest FROM blog_comments WHERE author_identity = ? AND created_at >= ?",
        identity, COMMENTS_PER_WINDOW, "评论太频繁了，请稍后再试",
    )


def plan_review(*, target_type: str, role: str, verdict: LocalVerdict, already_public: bool = False) -> ReviewPlan:
    """D1: a new post waits for review (pending); comments, and edits of a post
    that is already public, stay visible while reviewed unless L1 flagged them."""
    by = states.BY_LOCAL_RULES if verdict.flagged else ""
    settled = states.STATE_PASSED if target_type == "post" else states.STATE_NONE
    if str(role or "").strip().lower() != "student" or not ai_review_enabled():
        # Teachers/assistants (D5), or AI switched off: local rules decide.
        return ReviewPlan(states.STATE_FLAGGED if verdict.flagged else settled, by, False)
    if verdict.flagged or (target_type == "post" and not already_public):
        return ReviewPlan(states.STATE_PENDING, by, True)
    return ReviewPlan(states.STATE_NONE, "", True)


def _table(target_type: str) -> str:
    if target_type not in {"post", "comment"}:
        raise ValueError("invalid review target")
    return "blog_posts" if target_type == "post" else "blog_comments"


def _ai_reviews_today(conn) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_content_reviews WHERE layer = ? AND created_at >= ?",
        (REVIEW_LAYER_AI, start),
    ).fetchone()
    return int((row["total"] if row else 0) or 0)


def enqueue_review(conn, *, target_type: str, target_id: int, revision: int, plan: ReviewPlan) -> str:
    """Queue the AI review of this exact revision (caller commits).

    Returns ``queued``, ``exists`` (same revision already queued), ``skipped``
    (no AI needed) or ``quota`` (daily limit reached: a waiting item is
    released as flagged instead of waiting for the timeout).
    """
    if not plan.needs_ai:
        return "skipped"
    table = _table(target_type)
    now = _now()
    existing = conn.execute(
        "SELECT id FROM blog_content_reviews WHERE target_type = ? AND target_id = ? AND content_revision = ? AND layer = ?",
        (target_type, int(target_id), int(revision), REVIEW_LAYER_AI),
    ).fetchone()
    if existing is not None:
        return "exists"
    if _ai_reviews_today(conn) >= daily_ai_limit():
        conn.execute(
            f"UPDATE {table} SET moderation_state = ?, moderated_by = ?, moderated_at = ? "
            "WHERE id = ? AND moderation_state = ? AND COALESCE(moderated_by, '') <> ?",
            (states.STATE_FLAGGED, states.BY_QUOTA, now.isoformat(), int(target_id), states.STATE_PENDING, states.BY_RESUBMIT),
        )
        return "quota"
    conn.execute(
        "INSERT INTO blog_content_reviews (target_type, target_id, content_revision, layer, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (target_type, int(target_id), int(revision), REVIEW_LAYER_AI, now.isoformat()),
    )
    key = f"{target_type}:{int(target_id)}:r{int(revision)}"
    payload = {"target_type": target_type, "target_id": int(target_id), "revision": int(revision)}
    schedule_task(
        conn, task_kind=REVIEW_TASK_KIND, run_at=now, payload=payload,
        dedupe_key=f"blog-safety:{key}", title="博客内容安全检查",
        priority=REVIEW_TASK_PRIORITY, max_attempts=REVIEW_MAX_ATTEMPTS, replace=False,
    )
    if plan.state == states.STATE_PENDING:
        schedule_task(
            conn, task_kind=REVIEW_TIMEOUT_TASK_KIND, run_at=now + timedelta(minutes=REVIEW_TIMEOUT_MINUTES),
            payload=payload, dedupe_key=f"blog-safety-timeout:{key}", title="博客内容检查超时兜底",
            priority=REVIEW_TASK_PRIORITY, max_attempts=3, replace=False,
        )
    return "queued"
