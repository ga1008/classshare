"""Conversation attention: durable inbox, per-viewer quiet mode and bounded @ mail.

Uses the existing notification preferences, aggregation and mail outbox. No
SMTP/model call happens in the message transaction. Callers commit and push.
"""
from __future__ import annotations

import json
from datetime import timedelta, timezone
from typing import Any
from urllib.parse import quote

from .social_common import (SocialError, add_event, as_int, blocked_by, blocked_either,
                            ensure_schema, in_clause, now_iso, row_dict, serialize_on,
                            split_identity, utc_now, user_identity)

CHINA_TIME = timezone(timedelta(hours=8))
PREFERENCE_PREFIX = "social-popup:"
AGGREGATE_PREFIX = "social-chat:"


def mention_quota(conn: Any, user: dict) -> dict:
    from .message_center_service import is_super_admin_teacher

    ensure_schema(conn)
    me = user_identity(user)
    role, pk = split_identity(me)
    limit = None if role == "teacher" and is_super_admin_teacher(conn, pk) else (20 if role == "teacher" else 3)
    today = utc_now().astimezone(CHINA_TIME)
    day = today.date().isoformat()
    row = conn.execute("SELECT value FROM social_daily_counters WHERE counter_key = ? AND counter_day = ?",
                       (f"mention:{me}", day)).fetchone()
    used = as_int(row["value"]) if row else 0
    reset = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"limit": limit, "used": used, "remaining": None if limit is None else max(0, limit - used),
            "date": day, "reset_at": reset.isoformat()}


def consume_mention(conn: Any, user: dict) -> dict:
    # Same sender lock as ordinary sending; count and message commit atomically.
    me = user_identity(user)
    serialize_on(conn, "social-send", [me])
    quota = mention_quota(conn, user)
    if quota["remaining"] == 0:
        raise SocialError(f"今天的 @ 提醒已用完（每天 {quota['limit']} 次，北京时间零点重置）。"
                          "可以去掉 @ 提醒后发送普通消息，输入内容已保留。", 429)
    conn.execute("INSERT INTO social_daily_counters (counter_key, counter_day, value) VALUES (?, ?, 1) "
                 "ON CONFLICT(counter_key, counter_day) DO UPDATE SET value = social_daily_counters.value + 1",
                 (f"mention:{me}", quota["date"]))
    return {**quota, "used": quota["used"] + 1,
            "remaining": None if quota["limit"] is None else quota["remaining"] - 1}


def muted_map(conn: Any, parsed: dict, viewers: list[str]) -> dict[str, bool]:
    if not viewers:
        return {}
    marks, params = in_clause(viewers)
    if parsed["type"] == "group":
        rows = conn.execute(f"SELECT member_identity AS identity, muted FROM social_group_members "
                            f"WHERE group_id = ? AND member_identity IN ({marks}) AND left_at IS NULL",
                            (parsed["group_id"], *params)).fetchall()
        return {str(r["identity"]): bool(r["muted"]) for r in rows}
    rows = conn.execute(f"SELECT identity, enabled FROM user_notification_preferences "
                        f"WHERE event_key = ? AND identity IN ({marks})",
                        (PREFERENCE_PREFIX + parsed["key"], *params)).fetchall()
    return {str(r["identity"]): not bool(r["enabled"]) for r in rows}


def preferences(conn: Any, user: dict, key: str) -> dict:
    from .social_conversation_service import require_access

    me = user_identity(user)
    parsed = require_access(conn, me, key)
    return {"conversation_key": parsed["key"], "muted": muted_map(conn, parsed, [me]).get(me, False)}


def set_preferences(conn: Any, user: dict, key: str, muted: Any, events=None) -> dict:
    from .notification_delivery import set_event_enabled
    from .social_conversation_service import require_access

    if not isinstance(muted, bool):
        raise SocialError("免打扰设置必须是开启或关闭。", 400)
    me = user_identity(user)
    parsed = require_access(conn, me, key)
    if parsed["type"] == "group":
        conn.execute("UPDATE social_group_members SET muted = ? WHERE id = ?",
                     (int(muted), parsed["membership"]["id"]))
    else:
        set_event_enabled(conn, me, PREFERENCE_PREFIX + parsed["key"], not muted)
    result = {"conversation_key": parsed["key"], "muted": muted}
    add_event(events, [me], {"type": "conversation_preferences", **result})
    return result


def _contexts(conn: Any, parsed: dict, targets: list[str], message_id: int) -> dict[str, list[dict]]:
    from .social_identity_service import load_people

    if not targets:
        return {}
    marks, params = in_clause(targets)
    joined = {}
    if parsed["type"] == "group":
        joined = {str(r["member_identity"]): str(r["joined_at"]) for r in conn.execute(
            f"SELECT member_identity, joined_at FROM social_group_members WHERE group_id = ? "
            f"AND member_identity IN ({marks}) AND left_at IS NULL", (parsed["group_id"], *params)).fetchall()}
    rows = [row_dict(r) for r in conn.execute(
        "SELECT sender_identity, body, created_at, message_type FROM social_messages "
        "WHERE conversation_key = ? AND id <= ? AND recalled_at IS NULL AND message_type <> 'system'"
        " ORDER BY id DESC LIMIT 4", (parsed["key"], message_id)).fetchall()]
    people = load_people(conn, [r["sender_identity"] for r in rows])
    blocked = {target: set() for target in targets}
    for table in ("private_message_blocks", "social_blocks"):
        for row in conn.execute(f"SELECT owner_identity, blocked_identity FROM {table} "
                                f"WHERE owner_identity IN ({marks})", params).fetchall():
            blocked[str(row["owner_identity"])].add(str(row["blocked_identity"]))
    return {target: [{"name": str(people.get(r["sender_identity"], {}).get("name") or "好友"),
                      "text": str(r["body"] or "[附件]")[:600], "at": r["created_at"]}
                     for r in reversed(rows) if r["sender_identity"] not in blocked[target]
                     and (parsed["type"] == "dm" or (target in joined and r["created_at"] >= joined[target]))]
            for target in targets}


def notify_message(conn: Any, me: str, parsed: dict, message: dict) -> None:
    from .message_center_service import _build_notification_payload
    from .notification_delivery import deliver, deliver_aggregate_many
    from .social_conversation_service import participants

    # Serializes the absent-row aggregation case and read cursor with sends.
    serialize_on(conn, "social-attention", [parsed["key"]])
    targets = [i for i in participants(conn, parsed) if i != me]
    if not targets:
        return
    actor_role, actor_pk = split_identity(me)
    sender_name = message["sender"]["name"]
    group_name = str(parsed.get("group", {}).get("name") or "群聊")
    mentions = message.get("mentions") or []
    marks, params = in_clause(targets)
    blocked = set()
    for table in ("private_message_blocks", "social_blocks"):
        blocked.update(str(r["owner_identity"]) for r in conn.execute(
            f"SELECT owner_identity FROM {table} WHERE blocked_identity = ? AND owner_identity IN ({marks})",
            (me, *params)).fetchall())
    mentioned_targets = [t for t in targets if t not in blocked and message.get("message_type") != "nudge"
                         and (t in mentions or "all" in mentions)]
    contexts = _contexts(conn, parsed, mentioned_targets, message["id"])
    ordinary: list[dict[str, Any]] = []
    for target in targets:
        if target in blocked:
            continue
        role, pk = split_identity(target)
        mentioned = message.get("message_type") != "nudge" and (target in mentions or "all" in mentions)
        title = (f"{sender_name} 在「{group_name}」中 @ 了你" if parsed["type"] == "group" else f"{sender_name} @ 了你") if mentioned else (
            f"「{group_name}」有 {{count}} 条新消息" if parsed["type"] == "group" else f"{sender_name} 发来 {{count}} 条消息")
        metadata = {"event_type": "social_mention" if mentioned else "social_message",
                    "conversation_key": parsed["key"], "message_id": message["id"],
                    "conversation_title": group_name if parsed["type"] == "group" else sender_name,
                    "email_authorized": mentioned}
        if mentioned:
            metadata["context"] = contexts.get(target, [])
        payload = _build_notification_payload(
            recipient_role=role, recipient_user_pk=pk, category="social", title=title,
            body_preview=str(message["body"] or "[附件]")[:140], actor_role=actor_role,
            actor_user_pk=actor_pk, actor_display_name=sender_name,
            link_url="/profile?social_conversation=" + quote(parsed["key"], safe=""),
            ref_type="social_message", ref_id=f"mention:{message['id']}:{target}" if mentioned else str(message["id"]),
            metadata=metadata)
        if not mentioned:
            payload["email_notification_allowed"] = False
            ordinary.append(payload)
            continue
        # A mention carries per-recipient context, so it keeps its own card.
        deliver(conn, payload)
    # Every ordinary recipient gets the identical aggregated event, so one batch
    # replaces two statements per member (419 for a 200-member send;
    # docs/social-group-performance-2026-10-30.md §3 R1).
    deliver_aggregate_many(conn, ordinary, aggregate_key=AGGREGATE_PREFIX + parsed["key"])


def settle_read(conn: Any, me: str, key: str, through: int) -> None:
    # The bounded unread card set avoids engine-specific JSON SQL and does not
    # mark a newer aggregated message read when an older cursor is acknowledged.
    rows = conn.execute("SELECT id, metadata_json FROM message_center_notifications "
                        "WHERE recipient_identity = ? AND category = 'social' "
                        "AND ref_type = 'social_message' AND read_at IS NULL", (me,)).fetchall()
    ids = []
    for row in rows:
        data = json.loads(row["metadata_json"] or "{}")
        if data.get("conversation_key") == key and as_int(data.get("message_id")) <= through:
            ids.append(row["id"])
    if ids:
        marks, params = in_clause(ids)
        conn.execute(f"UPDATE message_center_notifications SET read_at = ? WHERE id IN ({marks})", (now_iso(), *params))


def _refresh_unread_card(conn: Any, row: dict, metadata: dict) -> None:
    """A recall must not erase the earlier unread messages in an aggregate."""
    from .social_conversation_service import dm_read_cursor, require_access
    from .social_identity_service import load_people

    target, key = row["recipient_identity"], metadata["conversation_key"]
    try:
        parsed = require_access(conn, target, key)
    except SocialError:
        conn.execute("UPDATE message_center_notifications SET read_at = ?, target_state = 'gone_message' WHERE id = ?",
                     (now_iso(), row["id"]))
        return
    cursor = dm_read_cursor(conn, key, target) if parsed["type"] == "dm" else as_int(parsed["membership"]["last_read_message_id"])
    where = ("conversation_key = ? AND id >= ? AND id > ? AND id <= ? AND sender_identity <> ? "
             "AND recalled_at IS NULL AND message_type <> 'system' "
             "AND (message_type = 'nudge' OR (mentions_json NOT LIKE ? AND mentions_json NOT LIKE ?))")
    args: list[Any] = [key, as_int(row["ref_id"]), cursor, as_int(metadata["message_id"]), target,
                       '%"' + target + '"%', '%"all"%']
    if parsed["type"] == "group":
        where += " AND created_at >= ?"
        args.append(parsed["membership"]["joined_at"])
    blocked = blocked_by(conn, target)
    if blocked:
        marks, params = in_clause(blocked)
        where += f" AND sender_identity NOT IN ({marks})"
        args.extend(params)
    remaining = conn.execute(f"SELECT COUNT(*) AS n, MAX(id) AS last_id FROM social_messages WHERE {where}", args).fetchone()
    if not remaining or not remaining["last_id"]:
        conn.execute("UPDATE message_center_notifications SET body_preview = ?, read_at = ?, "
                     "target_state = 'gone_message' WHERE id = ?", ("消息已撤回", now_iso(), row["id"]))
        return
    message = row_dict(conn.execute("SELECT * FROM social_messages WHERE id = ?", (remaining["last_id"],)).fetchone())
    actor = message["sender_identity"]
    person = load_people(conn, [actor]).get(actor) or {}
    name = person.get("name") or "好友"
    count = as_int(remaining["n"])
    title = f"「{parsed['group']['name']}」有 {count} 条新消息" if parsed["type"] == "group" else f"{name} 发来 {count} 条消息"
    role, pk = split_identity(actor)
    metadata["message_id"] = int(message["id"])
    conn.execute("UPDATE message_center_notifications SET aggregate_count = ?, title = ?, body_preview = ?, "
                 "metadata_json = ?, actor_identity = ?, actor_role = ?, actor_user_pk = ?, actor_display_name = ? WHERE id = ?",
                 (count, title, str(message["body"] or "[附件]")[:140], json.dumps(metadata, ensure_ascii=False),
                  actor, role, pk, name, row["id"]))


def recall_notifications(conn: Any, message_id: int, key: str) -> None:
    rows = conn.execute("SELECT * FROM message_center_notifications "
                        "WHERE category = 'social' AND ref_type = 'social_message' AND "
                        "(ref_id = ? OR ref_id LIKE ? OR aggregate_key = ?)",
                        (str(message_id), f"mention:{message_id}:%", AGGREGATE_PREFIX + key)).fetchall()
    for row in rows:
        data = json.loads(row["metadata_json"] or "{}")
        if row["aggregate_key"] and row["read_at"] is None:
            _refresh_unread_card(conn, row_dict(row), data)
        elif as_int(data.get("message_id")) == message_id:
            conn.execute("UPDATE message_center_notifications SET body_preview = ?, read_at = ?, "
                         "target_state = 'gone_message' WHERE id = ?", ("消息已撤回", now_iso(), row["id"]))


def refresh_email_payload(conn: Any, payload: dict) -> dict | None:
    """Revalidate audience and rebuild context immediately before SMTP sending."""
    from .social_conversation_service import load_message_row, require_access
    from .social_relations import are_friends

    try:
        metadata = json.loads(payload.get("metadata_json") or "{}")
        target = str(payload["recipient_identity"])
        row = load_message_row(conn, as_int(metadata.get("message_id")))
        if not row or row.get("recalled_at"):
            return None
        parsed = require_access(conn, target, row["conversation_key"])
        sender = row["sender_identity"]
        if blocked_either(conn, target, sender):
            return None
        if parsed["type"] == "dm" and not are_friends(conn, target, sender):
            return None
        if parsed["type"] == "group" and (parsed["group"].get("dissolved_at") or
                row["created_at"] < parsed["membership"]["joined_at"]):
            return None
        metadata["context"] = _contexts(conn, parsed, [target], int(row["id"])).get(target, [])
        return {**payload, "metadata_json": json.dumps(metadata, ensure_ascii=False)}
    except (SocialError, ValueError, TypeError, KeyError):
        return None
