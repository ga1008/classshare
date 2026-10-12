"""Generic message-center delivery rules (docs/blog-closed-loop-plan-2026-10-12.md §6.2).

Not blog specific: any feature can aggregate repeated events into one unread
card, honour a user's mute switch for an event, mark everything about one
post read at once, or mark notifications whose target disappeared.

* Aggregation: an event with ``aggregate_key`` merges into the recipient's
  *unread* row with the same key (optionally only while the card was touched
  within a time window), so ten likes are one card "10 人赞了你的帖子". Once
  read, the next event starts a new unread card. ``aggregate_member`` counts
  each person once per key — also across cards already read — so liking,
  unliking and liking again neither inflates a count nor re-notifies.
  Members are stored as keyed hashes, never as names.
* Preferences: ``user_notification_preferences(identity, event_key, enabled)``.
  Missing rows mean the event's default. Forced events ignore the switch.
* Seen markers: the same table keeps "last seen" times under ``seen:<key>``
  (e.g. the blog following feed). They are never shown as switches.
* Stale targets: ``target_state`` is ``gone_post`` (the post is not visible to
  the recipient) or ``gone_comment`` (the comment was deleted or handled). The
  two are kept apart so a post coming back never revives a deleted comment.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from ..db.connection import get_configured_db_engine
from .message_center_service import _insert_notification, _insert_notification_if_allowed, _now_iso

TARGET_STATE_GONE_POST = "gone_post"
TARGET_STATE_GONE_COMMENT = "gone_comment"
SEEN_PREFIX = "seen:"
MAX_AGGREGATE_MEMBERS = 500
MAX_AGGREGATE_RECENT = 4
_MEMBERS_KEY = "aggregate_members"
_RECENT_KEY = "aggregate_recent"


def is_gone(target_state: Any) -> bool:
    return str(target_state or "").startswith("gone")


def member_token(value: Any) -> str:
    """A stable short keyed hash so a row can count people without naming them.

    Keyed with the server secret: identities are a small space (role:id), so a
    plain hash would be reversible by anyone who saw it.
    """
    from ..config import SECRET_KEY

    digest = hmac.new(str(SECRET_KEY).encode("utf-8"), str(value or "").encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:16]


# --- preferences ------------------------------------------------------------

def load_preferences(conn, identity: str) -> dict[str, bool]:
    rows = conn.execute(
        "SELECT event_key, enabled FROM user_notification_preferences WHERE identity = ?",
        (str(identity or ""),),
    ).fetchall()
    return {
        str(row["event_key"]): bool(row["enabled"])
        for row in rows
        if not str(row["event_key"]).startswith(SEEN_PREFIX)
    }


def is_event_enabled(conn, identity: str, event_key: str, *, default: bool = True) -> bool:
    row = conn.execute(
        "SELECT enabled FROM user_notification_preferences WHERE identity = ? AND event_key = ? LIMIT 1",
        (str(identity or ""), str(event_key or "")),
    ).fetchone()
    return default if row is None else bool(row["enabled"])


def _upsert_preference(conn, identity: str, event_key: str, enabled: bool, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO user_notification_preferences (identity, event_key, enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(identity, event_key) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at
        """,
        (str(identity), str(event_key), 1 if enabled else 0, updated_at),
    )


def set_event_enabled(conn, identity: str, event_key: str, enabled: bool) -> None:
    if str(event_key or "").startswith(SEEN_PREFIX):
        raise ValueError("无效的通知事件")
    _upsert_preference(conn, identity, event_key, bool(enabled), _now_iso())


def get_seen_at(conn, identity: str, key: str) -> str:
    row = conn.execute(
        "SELECT updated_at FROM user_notification_preferences WHERE identity = ? AND event_key = ? LIMIT 1",
        (str(identity or ""), f"{SEEN_PREFIX}{key}"),
    ).fetchone()
    return str(row["updated_at"] or "") if row else ""


def mark_seen(conn, identity: str, key: str, *, at: Optional[str] = None) -> str:
    seen_at = at or _now_iso()
    _upsert_preference(conn, identity, f"{SEEN_PREFIX}{key}", True, seen_at)
    return seen_at


# --- delivery ---------------------------------------------------------------

def _with_loop_fields(payload: dict[str, Any], *, aggregate_key: str, ref_post_id: Optional[int]) -> dict[str, Any]:
    fields = dict(payload)
    if aggregate_key:
        fields["aggregate_key"] = aggregate_key
        fields["aggregate_count"] = 1
    if ref_post_id is not None:
        fields["ref_post_id"] = int(ref_post_id)
    return fields


def _push_recent(existing: Any, value: Any) -> list[Any]:
    """The last few actors/items of an aggregated card (for an avatar stack)."""
    items = [item for item in (existing or []) if item != value] if isinstance(existing, list) else []
    if value is not None and value != "":
        items.append(value)
    return items[-MAX_AGGREGATE_RECENT:]


def _render_title(template: str, count: int) -> str:
    return str(template or "").replace("{count}", str(int(count)))


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _member_already_counted(conn, recipient_identity: str, aggregate_key: str, member: str) -> bool:
    """Whether this person is already part of any card with this key (read or not)."""
    rows = conn.execute(
        "SELECT metadata_json FROM message_center_notifications WHERE recipient_identity = ? AND aggregate_key = ?",
        (recipient_identity, aggregate_key),
    ).fetchall()
    return any(member in (_loads(row["metadata_json"]).get(_MEMBERS_KEY) or []) for row in rows)


def _merge_into_unread(
    conn,
    payload: dict[str, Any],
    *,
    aggregate_key: str,
    window_seconds: Optional[int],
    member: str,
    recent: Any,
    title_template: str,
) -> Optional[int]:
    """Fold the event into an open card; None when there is no open card."""
    conditions = ["recipient_identity = ?", "aggregate_key = ?", "read_at IS NULL"]
    params: list[Any] = [payload["recipient_identity"], aggregate_key]
    if window_seconds:
        conditions.append("created_at >= ?")
        params.append((datetime.now() - timedelta(seconds=int(window_seconds))).isoformat(timespec="seconds"))
    # Two events for the same card at once must not both read count N.
    lock = " FOR UPDATE" if get_configured_db_engine() == "postgres" else ""
    row = conn.execute(
        f"SELECT id, aggregate_count, metadata_json FROM message_center_notifications "
        f"WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT 1{lock}",
        params,
    ).fetchone()
    if row is None:
        return None
    metadata = _loads(row["metadata_json"])
    members = list(metadata.get(_MEMBERS_KEY) or [])
    if member:
        members = [*members, member][-MAX_AGGREGATE_MEMBERS:]
    merged = {**metadata, **_loads(payload.get("metadata_json")), _MEMBERS_KEY: members,
              _RECENT_KEY: _push_recent(metadata.get(_RECENT_KEY), recent)}
    count = int(row["aggregate_count"] or 1) + 1
    cursor = conn.execute(
        """
        UPDATE message_center_notifications
        SET aggregate_count = aggregate_count + 1, title = ?, body_preview = ?, actor_identity = ?,
            actor_role = ?, actor_user_pk = ?, actor_display_name = ?, link_url = ?, metadata_json = ?,
            created_at = ?
        WHERE id = ? AND read_at IS NULL
        """,
        (
            _render_title(title_template, count),
            payload["body_preview"],
            payload["actor_identity"],
            payload["actor_role"],
            payload["actor_user_pk"],
            payload["actor_display_name"],
            payload["link_url"],
            json.dumps(merged, ensure_ascii=False),
            payload["created_at"],
            int(row["id"]),
        ),
    )
    # Read in the meantime: the event starts a new card instead of vanishing.
    return int(row["id"]) if int(cursor.rowcount or 0) == 1 else None


def deliver(
    conn,
    payload: dict[str, Any],
    *,
    event_key: str = "",
    default_enabled: bool = True,
    forced: bool = False,
    ref_post_id: Optional[int] = None,
    aggregate_key: str = "",
    aggregate_window_seconds: Optional[int] = None,
    aggregate_member: str = "",
    aggregate_recent: Any = None,
    dedupe: bool = True,
) -> int:
    """Write one event for one recipient; returns the notification id or 0.

    ``payload`` comes from ``_build_notification_payload``. For aggregated
    events its title may contain ``{count}``; ``aggregate_recent`` keeps the
    last few values (e.g. comment ids) for the card's avatar stack.
    """
    if event_key and not forced and not is_event_enabled(
        conn, payload["recipient_identity"], event_key, default=default_enabled,
    ):
        return 0
    title_template = str(payload.get("title") or "")
    if aggregate_key:
        member = member_token(aggregate_member) if aggregate_member else ""
        if member and _member_already_counted(conn, payload["recipient_identity"], aggregate_key, member):
            return 0
        merged = _merge_into_unread(
            conn, payload, aggregate_key=aggregate_key, window_seconds=aggregate_window_seconds,
            member=member, recent=aggregate_recent, title_template=title_template,
        )
        if merged is not None:
            return merged
        metadata = _loads(payload.get("metadata_json"))
        if member:
            metadata[_MEMBERS_KEY] = [member]
        metadata[_RECENT_KEY] = _push_recent([], aggregate_recent)
        fresh = {
            **payload,
            "title": _render_title(title_template, 1),
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
        }
        return _insert_notification(conn, _with_loop_fields(fresh, aggregate_key=aggregate_key, ref_post_id=ref_post_id))
    fields = _with_loop_fields(payload, aggregate_key="", ref_post_id=ref_post_id)
    if dedupe:
        return _insert_notification_if_allowed(conn, fields)
    return _insert_notification(conn, fields)


# --- read and stale state ---------------------------------------------------

def mark_read_by_post(conn, recipient_identity: str, post_id: int, *, categories: Iterable[str]) -> int:
    """Reading a post settles every unread notification about it (plan §6.3)."""
    wanted = sorted({str(item) for item in categories if item})
    if not wanted:
        return 0
    placeholders = ", ".join("?" for _ in wanted)
    cursor = conn.execute(
        f"""
        UPDATE message_center_notifications SET read_at = ?
        WHERE recipient_identity = ? AND ref_post_id = ? AND read_at IS NULL AND category IN ({placeholders})
        """,
        (_now_iso(), str(recipient_identity), int(post_id), *wanted),
    )
    return int(cursor.rowcount or 0)


def set_target_state(
    conn,
    state: str,
    *,
    ref_post_id: Optional[int] = None,
    ref_type: str = "",
    ref_ids: Iterable[Any] = (),
    exclude_recipient: str = "",
    exclude_categories: Iterable[str] = (),
    only_states: Optional[Iterable[str]] = None,
) -> int:
    """Mark (or clear, with ``state=''``) notifications whose target changed.

    ``only_states`` limits the update to rows currently in one of those states.
    """
    conditions: list[str] = []
    params: list[Any] = [str(state or "")]
    if ref_post_id is not None:
        conditions.append("ref_post_id = ?")
        params.append(int(ref_post_id))
    ids = [str(item) for item in ref_ids if str(item or "")]
    if ref_type and ids:
        conditions.append(f"ref_type = ? AND ref_id IN ({', '.join('?' for _ in ids)})")
        params.extend([ref_type, *ids])
    if not conditions:
        return 0
    if exclude_recipient:
        conditions.append("recipient_identity != ?")
        params.append(str(exclude_recipient))
    skipped = sorted({str(item) for item in exclude_categories if item})
    if skipped:
        conditions.append(f"category NOT IN ({', '.join('?' for _ in skipped)})")
        params.extend(skipped)
    if only_states is not None:
        allowed = sorted({str(item) for item in only_states})
        conditions.append(f"COALESCE(target_state, '') IN ({', '.join('?' for _ in allowed)})")
        params.extend(allowed)
    cursor = conn.execute(
        f"UPDATE message_center_notifications SET target_state = ? WHERE {' AND '.join(conditions)}",
        params,
    )
    return int(cursor.rowcount or 0)
