"""Evidence the 天道 judges on (docs/wanderer-ai-free-agents-2026-10-25.md §5.4).

Read-only aggregation per wanderer:

* last 24h of ``wanderer_action_log`` grouped by kind;
* 7-day interaction quality: comments written, replies and likes received;
* sanctions: comments withheld by moderation, reports, medium/high AI review
  risk, moderation log entries on its posts (14 days);
* knowledge statistics against the current tier's capacity;
* warning history (count, last, warnings since the last demotion in 14 days);
* classroom flooding: the most messages in one room within 10 minutes (24h);
* friend/group chat (social doc §10): messages sent in 24h, conversations and
  groups spoken in, the most messages in one conversation within 10 minutes,
  recalled messages, groups joined, how many people blocked it (7 days) and
  previews of its own latest chat lines.

Only counts and short previews of the wanderer's own public comments leave
this module; no other user's identity or ids are included.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..time_utils import parse_datetime
from .wanderer_identity_service import clamp_tier, tier_level
from .wanderer_knowledge_service import knowledge_summary

SPAM_WINDOW_SECONDS = 600
SPAM_THRESHOLD = 5
WARNING_WINDOW_DAYS = 14
RECENT_COMMENT_PREVIEWS = 5
_SANCTIONED_STATES = ("limited", "removed", "appeal_pending")
_RISKY = ("medium", "high")
_OWN_COMMENTS = "SELECT id FROM blog_comments WHERE author_identity = ?"


def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    try:
        return int(row["n"] or 0)
    except (TypeError, KeyError, IndexError):
        return int(row[0] or 0)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def actions_by_kind(conn: Any, wanderer_id: int, since: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT action_kind, outcome, COUNT(*) AS n FROM wanderer_action_log WHERE wanderer_id = ? AND created_at >= ? "
        "GROUP BY action_kind, outcome",
        (int(wanderer_id), since),
    ).fetchall()
    result: dict[str, int] = {}
    for row in rows:
        key = str(row["action_kind"]) if str(row["outcome"]) == "ok" else f"{row['action_kind']}:{row['outcome']}"
        result[key] = result.get(key, 0) + int(row["n"] or 0)
    return result


def interaction_quality(conn: Any, identity: str, since: str) -> dict[str, int]:
    return {
        "comments_written": _count(
            conn, "SELECT COUNT(*) AS n FROM blog_comments WHERE author_identity = ? AND created_at >= ?",
            (identity, since),
        ),
        "replies_received": _count(
            conn,
            f"SELECT COUNT(*) AS n FROM blog_comments c WHERE c.parent_comment_id IN ({_OWN_COMMENTS}) "
            "AND c.author_identity <> ? AND c.created_at >= ?",
            (identity, identity, since),
        ),
        "likes_received": _count(
            conn,
            f"SELECT COUNT(*) AS n FROM blog_likes l WHERE l.target_type = 'comment' "
            f"AND l.target_id IN ({_OWN_COMMENTS}) AND l.created_at >= ?",
            (identity, since),
        ),
    }


def sanctions(conn: Any, identity: str, since: str) -> dict[str, int]:
    states = ",".join("?" for _ in _SANCTIONED_STATES)
    risks = ",".join("?" for _ in _RISKY)
    return {
        "withheld_comments": _count(
            conn,
            f"SELECT COUNT(*) AS n FROM blog_comments WHERE author_identity = ? "
            f"AND COALESCE(moderation_state, 'none') IN ({states}) AND created_at >= ?",
            (identity, *_SANCTIONED_STATES, since),
        ),
        "reports": _count(
            conn,
            f"SELECT COUNT(*) AS n FROM blog_reports WHERE target_type = 'comment' "
            f"AND target_id IN ({_OWN_COMMENTS}) AND created_at >= ?",
            (identity, since),
        ),
        "risky_reviews": _count(
            conn,
            f"SELECT COUNT(*) AS n FROM blog_content_reviews WHERE target_type = 'comment' "
            f"AND target_id IN ({_OWN_COMMENTS}) AND risk IN ({risks}) AND created_at >= ?",
            (identity, *_RISKY, since),
        ),
        "moderated_posts": _count(
            conn,
            "SELECT COUNT(*) AS n FROM blog_moderation_logs WHERE post_id IN "
            "(SELECT id FROM blog_posts WHERE author_identity = ?) AND created_at >= ?",
            (identity, since),
        ),
    }


def warnings_since_demotion(conn: Any, wanderer_id: int, now: datetime) -> int:
    """Warnings in the last 14 days that came after the latest demotion."""
    since = _iso(now - timedelta(days=WARNING_WINDOW_DAYS))
    return _count(
        conn,
        "SELECT COUNT(*) AS n FROM wanderer_dao_reviews WHERE wanderer_id = ? AND verdict = 'warning' "
        "AND created_at >= ? AND id > COALESCE((SELECT MAX(id) FROM wanderer_dao_reviews "
        "WHERE wanderer_id = ? AND verdict = 'demoted'), 0)",
        (int(wanderer_id), since, int(wanderer_id)),
    )


def _room_peak(times: list[datetime]) -> int:
    start, peak = 0, 0
    for end, moment in enumerate(times):
        while (moment - times[start]).total_seconds() > SPAM_WINDOW_SECONDS:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


def chat_flooding(conn: Any, student_id: int, since: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT class_offering_id, logged_at FROM chat_logs WHERE user_id = ? AND user_role = 'student' "
        "AND logged_at >= ? ORDER BY class_offering_id, logged_at",
        (str(student_id), since),
    ).fetchall()
    by_room: dict[int, list[datetime]] = {}
    for row in rows:
        moment = parse_datetime(row["logged_at"])
        if moment is not None:
            by_room.setdefault(int(row["class_offering_id"] or 0), []).append(moment.replace(tzinfo=None))
    peaks = [_room_peak(times) for times in by_room.values()]
    return {
        "messages_24h": len(rows), "rooms": len(by_room), "max_in_10_minutes": max(peaks, default=0),
        "rooms_flooded": sum(1 for peak in peaks if peak > SPAM_THRESHOLD),
    }


_CHAT_TYPES = ("text", "media", "nudge")


def _utc_iso(moment: datetime) -> str:
    """Social timestamps are UTC ISO; the review clock is local naive."""
    aware = moment if moment.tzinfo else moment.astimezone()
    return aware.astimezone(timezone.utc).isoformat()


def social_activity(conn: Any, identity: str, now: datetime) -> dict[str, Any]:
    from .social_common import ensure_schema

    ensure_schema(conn)
    rows = conn.execute(
        "SELECT conversation_type, conversation_key, created_at, recalled_at FROM social_messages "
        "WHERE sender_identity = ? AND created_at >= ? AND message_type IN (?, ?, ?) "
        "ORDER BY conversation_key, created_at",
        (identity, _utc_iso(now - timedelta(days=1)), *_CHAT_TYPES),
    ).fetchall()
    by_conversation: dict[str, list[datetime]] = {}
    for row in rows:
        moment = parse_datetime(row["created_at"])
        if moment is not None:
            by_conversation.setdefault(str(row["conversation_key"]), []).append(moment.replace(tzinfo=None))
    peaks = [_room_peak(times) for times in by_conversation.values()]
    return {
        "messages_24h": len(rows),
        "dm_conversations": sum(1 for key in by_conversation if key.startswith("dm:")),
        "groups_spoken": sum(1 for key in by_conversation if key.startswith("group:")),
        "max_in_10_minutes": max(peaks, default=0),
        "conversations_flooded": sum(1 for peak in peaks if peak > SPAM_THRESHOLD),
        "recalled_24h": sum(1 for row in rows if row["recalled_at"]),
        "groups_joined": _count(
            conn, "SELECT COUNT(*) AS n FROM social_group_members WHERE member_identity = ? AND left_at IS NULL",
            (identity,),
        ),
        "blocked_by_7d": _count(
            conn, "SELECT COUNT(*) AS n FROM private_message_blocks WHERE blocked_identity = ? AND created_at >= ?",
            (identity, _iso(now - timedelta(days=7))),
        ),
    }


def recent_chat_previews(conn: Any, identity: str) -> list[str]:
    rows = conn.execute(
        "SELECT body FROM social_messages WHERE sender_identity = ? AND message_type = 'text' "
        f"ORDER BY id DESC LIMIT {RECENT_COMMENT_PREVIEWS}",
        (identity,),
    ).fetchall()
    return [str(row["body"] or "")[:80] for row in rows]


def recent_comment_previews(conn: Any, identity: str) -> list[str]:
    rows = conn.execute(
        f"SELECT content_md FROM blog_comments WHERE author_identity = ? ORDER BY id DESC LIMIT {RECENT_COMMENT_PREVIEWS}",
        (identity,),
    ).fetchall()
    return [str(row["content_md"] or "")[:80] for row in rows]


def collect_evidence(conn: Any, wanderer: dict[str, Any], now: datetime) -> dict[str, Any]:
    identity = f"student:{int(wanderer['student_id'])}"
    tier = clamp_tier(wanderer.get("tier"))
    day_ago, week_ago = _iso(now - timedelta(days=1)), _iso(now - timedelta(days=7))
    return {
        "wanderer_id": int(wanderer["id"]),
        "name": str(wanderer.get("display_name") or ""),
        "archetype": str(wanderer.get("archetype") or ""),
        "tier": tier,
        "tier_name": tier_level(tier).get("name") or "未入道",
        "actions_24h": actions_by_kind(conn, int(wanderer["id"]), day_ago),
        "interactions_7d": interaction_quality(conn, identity, week_ago),
        "sanctions_14d": sanctions(conn, identity, _iso(now - timedelta(days=WARNING_WINDOW_DAYS))),
        "knowledge": knowledge_summary(conn, wanderer),
        "warnings_count": int(wanderer.get("warnings_count") or 0),
        "last_warning_at": wanderer.get("last_warning_at") or "",
        "warnings_since_demotion_14d": warnings_since_demotion(conn, int(wanderer["id"]), now),
        "classroom_24h": chat_flooding(conn, int(wanderer["student_id"]), day_ago),
        "recent_comments": recent_comment_previews(conn, identity),
        "social_24h": social_activity(conn, identity, now),
        "recent_chat": recent_chat_previews(conn, identity),
    }
