"""发送、撤回、已读、拍一拍、@提醒 (social doc §5).

Send rules: a direct message needs an accepted friendship and no block in
either direction; a group message needs current membership in a live group.
Bodies ≤ 4000 characters (Markdown subset, rendered client-side), ≤ 9
attachments. Rate limit: ≤ 10 messages per 10 s and ≤ 30 per minute per person
(429). ``client_nonce`` makes a resend idempotent. Recall: the sender within
2 minutes; group owner/admins any time. Nudge: once per pair per minute.
@mentions (``all`` = @全体, owner/admins only) notify through the message
center (category ``social``).

Additive request field: ``lan_media: [{sha256,size,mime,filename}]`` (direct
messages only) announces LAN-delivered attachments that the sender backfills
later via ``POST /media/{id}/backfill``.
"""
from __future__ import annotations

from typing import Any, Optional

from .rate_limit_service import RateLimitExceededError, calculate_retry_after_seconds
from .social_common import (
    SocialError,
    add_event,
    as_int,
    blocked_either,
    dumps,
    ensure_schema,
    iso_before,
    normalize_identity,
    now_iso,
    parse_iso,
    row_dict,
    serialize_on,
    utc_now,
    user_identity,
)
from .social_conversation_service import (
    for_viewer,
    insert_message,
    load_message_row,
    participants,
    require_access,
    serialize_messages,
)
from .social_identity_service import load_people
from .social_relations import are_friends, group_member_identities

BODY_MAX = 4000
NONCE_MAX = 64
RATE_WINDOWS = ((10, 10), (60, 30))  # (seconds, max messages)
RECALL_WINDOW_SECONDS = 120
NUDGE_WINDOW_SECONDS = 60
MENTION_ALL = "all"
MENTION_LIMIT = 50
BODY_FORMATS = ("md", "text")
COUNTED_TYPES = ("text", "media", "nudge")


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def _enforce_rate_limit(conn: Any, me: str) -> None:
    # Serialise each sender's count-then-insert (PostgreSQL advisory lock on the identity;
    # SQLite takes the write lock), so concurrent requests cannot both pass the last slot.
    serialize_on(conn, "social-send", [me])
    for seconds, limit in RATE_WINDOWS:
        rows = conn.execute(
            "SELECT created_at FROM social_messages WHERE sender_identity = ? AND created_at >= ? "
            "AND message_type IN (?, ?, ?) ORDER BY created_at ASC LIMIT ?",
            (me, iso_before(seconds=seconds), *COUNTED_TYPES, limit),
        ).fetchall()
        if len(rows) >= limit:
            oldest = parse_iso(rows[0]["created_at"]) or utc_now()
            retry = calculate_retry_after_seconds(oldest_event_at=oldest.isoformat(), window_seconds=seconds,
                                                  now=utc_now())
            raise RateLimitExceededError("发送太频繁，请稍后再试。", retry_after_seconds=retry)


def _require_can_send(conn: Any, me: str, parsed: dict[str, Any]) -> None:
    if parsed["type"] == "dm":
        peer = parsed["peer"]
        if blocked_either(conn, me, peer):
            raise SocialError("暂时无法给对方发消息。", 403)
        if not are_friends(conn, me, peer):
            raise SocialError("你们已不是好友，无法继续发送消息。", 403)
        return
    if parsed["group"].get("dissolved_at"):
        raise SocialError("该群聊已解散。", 403)


def _existing_by_nonce(conn: Any, me: str, nonce: str) -> Optional[dict[str, Any]]:
    if not nonce:
        return None
    row = conn.execute("SELECT * FROM social_messages WHERE sender_identity = ? AND client_nonce = ? LIMIT 1",
                       (me, nonce)).fetchone()
    return row_dict(row) if row is not None else None


def clean_nonce(payload: Any) -> str:
    return str((payload or {}).get("client_nonce") or "").strip()[:NONCE_MAX] if isinstance(payload, dict) else ""


def message_for_nonce(conn: Any, user: dict, key: Any, nonce: str) -> Optional[dict[str, Any]]:
    """The message already stored for ``(sender, client_nonce)`` in ``key``, serialized for the sender.

    Used after a concurrent duplicate send lost the race (unique violation or a
    token consumed by the winner): the resend answers with the stored message.
    """
    me = user_identity(user)
    parsed = require_access(conn, me, key)
    if not nonce:
        return None
    row = conn.execute(
        "SELECT * FROM social_messages WHERE sender_identity = ? AND client_nonce = ? AND conversation_key = ? LIMIT 1",
        (me, nonce, parsed["key"]),
    ).fetchone()
    return serialize_messages(conn, me, [row_dict(row)])[0] if row is not None else None


def _clean_mentions(conn: Any, me: str, parsed: dict[str, Any], mentions: Any) -> list[str]:
    if not isinstance(mentions, (list, tuple)):
        return []
    if parsed["type"] == "dm":
        return [parsed["peer"]] if parsed["peer"] in mentions else []
    members = set(group_member_identities(conn, parsed["group_id"]))
    allowed_all = parsed["membership"]["role"] in ("owner", "admin")
    cleaned: list[str] = []
    for item in mentions:
        text = str(item or "").strip()
        if text == MENTION_ALL:
            if not allowed_all:
                raise SocialError("只有群主和管理员可以 @全体成员。", 403)
            cleaned.append(MENTION_ALL)
            continue
        try:
            identity = normalize_identity(text)
        except SocialError:
            continue
        if identity in members and identity != me:
            cleaned.append(identity)
    return list(dict.fromkeys(cleaned))[:MENTION_LIMIT]


def _valid_reply(conn: Any, key: str, reply_to_id: Any) -> Optional[int]:
    target = as_int(reply_to_id)
    if target <= 0:
        return None
    row = load_message_row(conn, target)
    if not row or row["conversation_key"] != key:
        raise SocialError("引用的消息不存在。", 400)
    return target


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def _wake_wanderers(conn: Any, parsed: dict[str, Any], sender: str) -> None:
    """Register 散修 reaction tasks (social doc §10); never sinks the send."""
    from ..db.savepoint import best_effort
    from .wanderer_social_triggers import on_message_created

    with best_effort(conn, "wanderer_social_hook", log_prefix="[SOCIAL]"):
        on_message_created(conn, parsed, sender)


def push_message(events: Optional[list], conn: Any, parsed: dict[str, Any], message: dict[str, Any]) -> None:
    from .social_notification_service import muted_map

    if events is None:
        return
    viewers = participants(conn, parsed)
    muted = muted_map(conn, parsed, viewers)
    for viewer in viewers:
        add_event(events, [viewer], {"type": "message", "message": {
            **for_viewer(message, viewer), "muted": muted.get(viewer, False)}})


def _validated_body(payload: dict[str, Any]) -> tuple[str, str]:
    body = str(payload.get("body") or "")
    if len(body) > BODY_MAX:
        raise SocialError(f"消息不能超过 {BODY_MAX} 字。", 400)
    body_format = str(payload.get("body_format") or "md")
    if body_format not in BODY_FORMATS:
        raise SocialError("消息格式无效。", 400)
    return body, body_format


def send_message(conn: Any, user: dict, key: Any, payload: dict[str, Any], events: Optional[list] = None) -> dict[str, Any]:
    from . import social_media_service as media_service
    from .social_notification_service import consume_mention, mention_quota, notify_message

    me = user_identity(user)
    parsed = require_access(conn, me, key)
    nonce = clean_nonce(payload)
    existing = _existing_by_nonce(conn, me, nonce)
    if existing:
        if existing["conversation_key"] != parsed["key"]:
            raise SocialError("该发送标识已用于其他会话，请重新发送。", 409)
        return serialize_messages(conn, me, [existing])[0]
    _require_can_send(conn, me, parsed)
    body, body_format = _validated_body(payload)
    lan_specs = payload.get("lan_media") or []
    if lan_specs and (parsed["type"] != "dm" or not isinstance(lan_specs, (list, tuple))):
        raise SocialError("局域网直传仅支持私聊。", 400)
    raw_tokens = payload.get("media_tokens") or []
    if (len(raw_tokens) if isinstance(raw_tokens, (list, tuple)) else 0) + len(lan_specs) > media_service.MAX_ATTACHMENTS:
        raise SocialError(f"一条消息最多 {media_service.MAX_ATTACHMENTS} 个附件。", 400)
    if not body.strip() and not raw_tokens and not lan_specs:
        raise SocialError("消息内容不能为空。", 400)
    serialize_on(conn, "social-send", [me])
    existing = _existing_by_nonce(conn, me, nonce)
    if existing:
        if existing["conversation_key"] != parsed["key"]:
            raise SocialError("该发送标识已用于其他会话，请重新发送。", 409)
        return serialize_messages(conn, me, [existing])[0]
    _enforce_rate_limit(conn, me)
    serialize_on(conn, "social-attention", [parsed["key"]])
    # Membership, manager role, friendship or a block may change while this
    # sender waits behind another transaction. Authorise the write under the
    # conversation lock, using fresh rows rather than the earlier snapshot.
    parsed = require_access(conn, me, parsed["key"])
    _require_can_send(conn, me, parsed)
    tokens = media_service.redeem_tokens(conn, me, raw_tokens)
    mentions = _clean_mentions(conn, me, parsed, payload.get("mentions"))
    quota = consume_mention(conn, user) if mentions else mention_quota(conn, user)
    message_id = insert_message(
        conn, key=parsed["key"], sender=me, body=body, body_format=body_format,
        message_type="media" if (tokens or lan_specs) and not body.strip() else "text",
        reply_to_id=_valid_reply(conn, parsed["key"], payload.get("reply_to_id")), mentions=mentions,
        client_nonce=nonce,
    )
    media_service.attach_media(conn, message_id, tokens)
    if lan_specs:
        media_service.attach_lan_media(conn, message_id, me, parsed["peer"], lan_specs)
    message = serialize_messages(conn, me, [load_message_row(conn, message_id)])[0]
    notify_message(conn, me, parsed, message)
    _wake_wanderers(conn, parsed, me)
    push_message(events, conn, parsed, message)
    return {**message, "mention_quota": quota}


# ---------------------------------------------------------------------------
# Recall, read, nudge
# ---------------------------------------------------------------------------

def recall_message(conn: Any, user: dict, message_id: int, events: Optional[list] = None) -> dict[str, Any]:
    from .social_notification_service import recall_notifications
    me = user_identity(user)
    row = load_message_row(conn, message_id)
    if not row:
        raise SocialError("消息不存在。", 404)
    serialize_on(conn, "social-attention", [row["conversation_key"]])
    row = load_message_row(conn, message_id)
    if not row:
        raise SocialError("消息不存在。", 404)
    parsed = require_access(conn, me, row["conversation_key"])
    if row.get("recalled_at"):
        return {"id": int(row["id"]), "recalled": True}
    if row["message_type"] == "system":
        raise SocialError("系统消息不能撤回。", 400)
    is_manager = parsed["type"] == "group" and parsed["membership"]["role"] in ("owner", "admin")
    if row["sender_identity"] != me and not is_manager:
        raise SocialError("只能撤回自己发送的消息。", 403)
    sent = parse_iso(row["created_at"]) or utc_now()
    if not is_manager and (utc_now() - sent).total_seconds() > RECALL_WINDOW_SECONDS:
        raise SocialError("超过 2 分钟的消息不能撤回。", 409)
    conn.execute("UPDATE social_messages SET recalled_at = ? WHERE id = ?", (now_iso(), int(row["id"])))
    recall_notifications(conn, int(row["id"]), parsed["key"])
    add_event(events, participants(conn, parsed),
              {"type": "message_recalled", "id": int(row["id"]), "conversation_key": parsed["key"]})
    return {"id": int(row["id"]), "recalled": True}


def mark_read(conn: Any, user: dict, key: Any, last_message_id: Any, events: Optional[list] = None) -> dict[str, Any]:
    from .social_notification_service import settle_read
    me = user_identity(user)
    parsed = require_access(conn, me, key)
    ensure_schema(conn)
    serialize_on(conn, "social-attention", [parsed["key"]])
    latest = conn.execute("SELECT MAX(id) AS n FROM social_messages WHERE conversation_key = ?",
                          (parsed["key"],)).fetchone()
    target = min(max(0, as_int(last_message_id)), as_int(latest["n"]) if latest else 0)
    settle_read(conn, me, parsed["key"], target)
    if parsed["type"] == "group":
        conn.execute(
            "UPDATE social_group_members SET last_read_message_id = ? WHERE id = ? AND last_read_message_id < ?",
            (target, int(parsed["membership"]["id"]), target),
        )
        add_event(events, [me], {"type": "read", "conversation_key": parsed["key"], "reader": me,
                                 "last_message_id": target})
        return {"conversation_key": parsed["key"], "last_message_id": target}
    conn.execute(
        "INSERT INTO social_dm_reads (conversation_key, reader_identity, last_read_message_id, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (conversation_key, reader_identity) DO UPDATE SET "
        "last_read_message_id = CASE WHEN excluded.last_read_message_id > social_dm_reads.last_read_message_id "
        "THEN excluded.last_read_message_id ELSE social_dm_reads.last_read_message_id END, "
        "updated_at = excluded.updated_at",
        (parsed["key"], me, target, now_iso()),
    )
    add_event(events, [parsed["peer"]], {"type": "read", "conversation_key": parsed["key"], "reader": me,
                                         "last_message_id": target})
    add_event(events, [me], {"type": "read", "conversation_key": parsed["key"], "reader": me,
                             "last_message_id": target})
    return {"conversation_key": parsed["key"], "last_message_id": target}


def _nudge_target(conn: Any, me: str, parsed: dict[str, Any], target: Any) -> str:
    if parsed["type"] == "dm":
        return parsed["peer"]
    identity = normalize_identity(target) if target else ""
    if not identity or identity == me or identity not in group_member_identities(conn, parsed["group_id"]):
        raise SocialError("请选择要拍一拍的群成员。", 400)
    if blocked_either(conn, me, identity):
        raise SocialError("暂时无法给对方发消息。", 403)  # same wording as a blocked direct message
    return identity


def nudge(conn: Any, user: dict, key: Any, target: Any = None, events: Optional[list] = None) -> dict[str, Any]:
    """拍一拍: a stored ``nudge`` message plus a ``nudge`` push; once per pair per minute."""
    from .social_notification_service import notify_message
    me = user_identity(user)
    parsed = require_access(conn, me, key)
    _require_can_send(conn, me, parsed)
    # Same global order as text/media sends: sender, then conversation.
    _enforce_rate_limit(conn, me)
    serialize_on(conn, "social-attention", [parsed["key"]])
    parsed = require_access(conn, me, parsed["key"])
    _require_can_send(conn, me, parsed)
    whom = _nudge_target(conn, me, parsed, target)
    recent = conn.execute(
        "SELECT 1 FROM social_messages WHERE sender_identity = ? AND conversation_key = ? AND message_type = 'nudge' "
        "AND mentions_json = ? AND created_at >= ? LIMIT 1",
        (me, parsed["key"], dumps([whom]), iso_before(seconds=NUDGE_WINDOW_SECONDS)),
    ).fetchone()
    if recent is not None:
        raise SocialError("拍得太频繁了，休息一下吧。", 429, retry_after=NUDGE_WINDOW_SECONDS)
    names = load_people(conn, [me, whom])
    body = f"{(names.get(me) or {}).get('name', '')} 拍了拍 {(names.get(whom) or {}).get('name', '')}"
    message_id = insert_message(conn, key=parsed["key"], sender=me, body=body, message_type="nudge", mentions=[whom])
    message = serialize_messages(conn, me, [load_message_row(conn, message_id)])[0]
    notify_message(conn, me, parsed, message)
    _wake_wanderers(conn, parsed, me)
    push_message(events, conn, parsed, message)
    add_event(events, [whom], {"type": "nudge", "conversation_key": parsed["key"], "from": me})
    return message


def typing_audience(conn: Any, me: str, key: Any) -> tuple[str, list[str]]:
    """Validated key and the other participants who should see "输入中"."""
    parsed = require_access(conn, me, key)
    if parsed["type"] == "dm" and blocked_either(conn, me, parsed["peer"]):
        return parsed["key"], []
    return parsed["key"], [item for item in participants(conn, parsed) if item != me]
