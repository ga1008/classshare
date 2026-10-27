"""Conversations: access, storage of messages, serialization and listing (social doc §5, §7).

A conversation is ``dm:<a>|<b>`` (two people, history kept after unfriending,
writable only while friends) or ``group:<id>`` (current members only). Messages
of both kinds live in ``social_messages``; ``Message`` / ``ConversationItem``
shapes follow §7 exactly, with a few additive fields noted inline.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Iterable, Optional

from .social_common import (
    SYSTEM_SENDER,
    SocialError,
    add_event,
    as_int,
    dm_peer,
    dumps,
    ensure_schema,
    group_key,
    in_clause,
    loads_list,
    now_iso,
    parse_conversation_key,
    row_dict,
    user_identity,
)
from .social_identity_service import avatar_url, load_people, sender_card
from .social_relations import active_group_ids, group_member_identities

PAGE_LIMIT_DEFAULT = 50
PAGE_LIMIT_MAX = 100
PREVIEW_CHARS = 60
_MARKDOWN_NOISE = re.compile(r"[*_`>#~\[\]()!|-]+")
MEDIA_PREVIEW = {"image": "[图片]", "video": "[视频]", "audio": "[语音]", "file": "[文件]"}


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def load_group(conn: Any, group_id: int) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM social_groups WHERE id = ? LIMIT 1", (int(group_id),)).fetchone()
    return row_dict(row) if row is not None else None


def load_membership(conn: Any, group_id: int, identity: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM social_group_members WHERE group_id = ? AND member_identity = ? LIMIT 1",
        (int(group_id), identity),
    ).fetchone()
    return row_dict(row) if row is not None else None


def require_access(conn: Any, me: str, key: Any) -> dict[str, Any]:
    """Parsed key plus ``peer`` (dm) or ``group``/``membership`` (group); 403/404 otherwise."""
    ensure_schema(conn)
    parsed = parse_conversation_key(key)
    if parsed["type"] == "dm":
        parsed["peer"] = dm_peer(parsed, me)
        return parsed
    group = load_group(conn, parsed["group_id"])
    if not group:
        raise SocialError("群聊不存在。", 404)
    membership = load_membership(conn, parsed["group_id"], me)
    if not membership or membership.get("left_at"):
        raise SocialError("你不在该群聊中。", 403)
    parsed.update(group=group, membership=membership)
    return parsed


def participants(conn: Any, parsed: dict[str, Any]) -> list[str]:
    if parsed["type"] == "dm":
        return list(parsed["parties"])
    return group_member_identities(conn, parsed["group_id"])


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def insert_message(
    conn: Any,
    *,
    key: str,
    sender: str,
    body: str,
    body_format: str = "text",
    message_type: str = "text",
    reply_to_id: Optional[int] = None,
    mentions: Iterable[str] = (),
    client_nonce: str = "",
) -> int:
    from ..db.connection import execute_insert_returning_id

    ensure_schema(conn)
    conversation_type = "group" if key.startswith("group:") else "dm"
    message_id = execute_insert_returning_id(
        conn,
        "INSERT INTO social_messages (conversation_type, conversation_key, sender_identity, body, body_format, "
        "message_type, reply_to_id, mentions_json, client_nonce, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_type, key, sender, body, body_format, message_type, reply_to_id,
         dumps(list(mentions)), client_nonce or uuid.uuid4().hex, now_iso()),
    )
    if conversation_type == "dm":
        _touch_dm_participants(conn, key, message_id)
    return message_id


def _touch_dm_participants(conn: Any, key: str, message_id: int) -> None:
    """Keep the indexed (identity, dm key) -> last message id table current (no key scans)."""
    for party in parse_conversation_key(key)["parties"]:
        conn.execute(
            "INSERT INTO social_dm_participants (identity, conversation_key, last_message_id) VALUES (?, ?, ?) "
            "ON CONFLICT (identity, conversation_key) DO UPDATE SET last_message_id = CASE "
            "WHEN excluded.last_message_id > social_dm_participants.last_message_id "
            "THEN excluded.last_message_id ELSE social_dm_participants.last_message_id END",
            (party, key, int(message_id)),
        )


def insert_system_message(conn: Any, key: str, body: str, events: Optional[list] = None,
                          *, audience: Iterable[str] = ()) -> dict[str, Any]:
    """Store a system line and queue a ``message`` push to the conversation's participants."""
    message_id = insert_message(conn, key=key, sender=SYSTEM_SENDER, body=body, message_type="system")
    message = serialize_messages(conn, "", [load_message_row(conn, message_id)])[0]
    targets = set(audience) or set(participants(conn, parse_conversation_key(key)))
    add_event(events, targets, {"type": "message", "message": message})
    return message


def load_message_row(conn: Any, message_id: int) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM social_messages WHERE id = ? LIMIT 1", (int(message_id),)).fetchone()
    return row_dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def media_payload(row: dict[str, Any]) -> dict[str, Any]:
    media_id = int(row["id"])
    kind = str(row["media_kind"] or "file")
    return {
        "id": media_id,
        "kind": kind,
        "filename": str(row["original_filename"] or ""),
        "size": as_int(row["file_size"]),
        "mime": str(row["mime_type"] or ""),
        "url": f"/api/social/media/{media_id}",
        "thumb_url": f"/api/social/media/{media_id}?variant=thumb" if kind == "image" else None,
        "expired": bool(row.get("expired_at")),
        "delivered_via": str(row["delivered_via"] or "server"),
        "server_ready": bool(as_int(row["server_ready"], 1)),
        # additive: dimensions/duration and the LAN transfer id (None when sent via server)
        "width": row.get("width"),
        "height": row.get("height"),
        "duration_ms": row.get("duration_ms"),
        "transfer_id": row.get("transfer_id"),
    }


def _media_by_message(conn: Any, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    marks, params = in_clause(message_ids)
    rows = conn.execute(
        f"SELECT m.*, (SELECT MIN(t.id) FROM social_lan_transfers t WHERE t.media_id = m.id) AS transfer_id "
        f"FROM social_message_media m WHERE m.message_id IN ({marks}) ORDER BY m.id",
        params,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["message_id"]), []).append(media_payload(row_dict(row)))
    return grouped


def preview_text(row: Optional[dict[str, Any]], media_kinds: Iterable[str] = ()) -> str:
    if not row:
        return ""
    if row.get("recalled_at"):
        return "撤回了一条消息"
    body = " ".join(_MARKDOWN_NOISE.sub(" ", str(row.get("body") or "")).split())
    if not body:
        kinds = list(media_kinds)
        return MEDIA_PREVIEW.get(kinds[0], "[文件]") if kinds else ""
    return body[:PREVIEW_CHARS]


def _reply_payloads(conn: Any, rows: list[dict[str, Any]], people: dict[str, dict]) -> dict[int, dict[str, Any]]:
    ids = sorted({as_int(row.get("reply_to_id")) for row in rows if as_int(row.get("reply_to_id")) > 0})
    if not ids:
        return {}
    marks, params = in_clause(ids)
    replies = [row_dict(row) for row in conn.execute(
        f"SELECT * FROM social_messages WHERE id IN ({marks})", params).fetchall()]
    people.update(load_people(conn, [row["sender_identity"] for row in replies if row["sender_identity"] not in people]))
    return {int(row["id"]): {
        "id": int(row["id"]),
        "preview": preview_text(row),
        "sender_name": sender_card(people.get(row["sender_identity"]), row["sender_identity"])["name"],
    } for row in replies}


def serialize_messages(conn: Any, me: str, rows: list[Optional[dict[str, Any]]]) -> list[dict[str, Any]]:
    items = [row for row in rows if row]
    people = load_people(conn, [row["sender_identity"] for row in items])
    media = _media_by_message(conn, [int(row["id"]) for row in items])
    replies = _reply_payloads(conn, items, people)
    result = []
    for row in items:
        recalled = bool(row.get("recalled_at"))
        sender = str(row["sender_identity"])
        result.append({
            "id": int(row["id"]),
            "conversation_key": str(row["conversation_key"]),
            "sender": sender_card(people.get(sender), sender),
            "body": "" if recalled else str(row["body"] or ""),
            "body_format": str(row["body_format"] or "text"),
            "message_type": str(row["message_type"] or "text"),
            "reply_to": replies.get(as_int(row.get("reply_to_id"))),
            "mentions": loads_list(row.get("mentions_json")),
            "media": [] if recalled else media.get(int(row["id"]), []),
            "created_at": str(row["created_at"] or ""),
            "recalled": recalled,
            "mine": bool(me) and sender == me,
        })
    return result


def for_viewer(message: dict[str, Any], viewer: str) -> dict[str, Any]:
    """A serialized message with ``mine`` recomputed for another recipient."""
    return {**message, "mine": message["sender"]["identity"] == viewer}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def dm_read_cursor(conn: Any, key: str, reader: str) -> int:
    row = conn.execute(
        "SELECT last_read_message_id FROM social_dm_reads WHERE conversation_key = ? AND reader_identity = ?",
        (key, reader),
    ).fetchone()
    return as_int(row["last_read_message_id"]) if row else 0


def list_messages(conn: Any, user: dict, key: Any, *, before_id: Any = None, limit: Any = None) -> dict[str, Any]:
    me = user_identity(user)
    parsed = require_access(conn, me, key)
    size = max(1, min(PAGE_LIMIT_MAX, as_int(limit, PAGE_LIMIT_DEFAULT) or PAGE_LIMIT_DEFAULT))
    params: list[Any] = [parsed["key"]]
    where = "conversation_key = ?"
    if as_int(before_id) > 0:
        where += " AND id < ?"
        params.append(as_int(before_id))
    if parsed["type"] == "group":
        # Members see history from when they (re)joined onwards.
        where += " AND created_at >= ?"
        params.append(str(parsed["membership"]["joined_at"]))
    rows = conn.execute(
        f"SELECT * FROM social_messages WHERE {where} ORDER BY id DESC LIMIT ?", (*params, size + 1),
    ).fetchall()
    page = [row_dict(row) for row in rows]
    has_more = len(page) > size
    page = list(reversed(page[:size]))
    payload = {"items": serialize_messages(conn, me, page), "has_more": has_more}
    if parsed["type"] == "dm":  # additive: lets the client draw 已读
        payload["peer_read_message_id"] = dm_read_cursor(conn, parsed["key"], parsed["peer"])
    return payload


def unread_count(conn: Any, key: str, me: str, after_id: int, since: str = "") -> int:
    """Others' messages after my cursor; system lines (welcome, created the group, joined...) never count."""
    sql = ("SELECT COUNT(*) AS n FROM social_messages WHERE conversation_key = ? AND id > ? "
           "AND sender_identity <> ? AND recalled_at IS NULL AND message_type <> 'system'")
    params: list[Any] = [key, after_id, me]
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    row = conn.execute(sql, params).fetchone()
    return as_int(row["n"]) if row else 0


def dm_keys(conn: Any, me: str) -> dict[str, int]:
    """``{dm key: last message id}`` for every direct conversation I take part in."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT conversation_key, last_message_id FROM social_dm_participants WHERE identity = ?", (me,),
    ).fetchall()
    return {str(row["conversation_key"]): as_int(row["last_message_id"]) for row in rows}


def _last_rows(conn: Any, ids: list[int]) -> tuple[dict[int, dict[str, Any]], dict[int, list[str]]]:
    ids = [item for item in ids if item]
    if not ids:
        return {}, {}
    marks, params = in_clause(ids)
    rows = {int(row["id"]): row_dict(row) for row in conn.execute(
        f"SELECT * FROM social_messages WHERE id IN ({marks})", params).fetchall()}
    kinds: dict[int, list[str]] = {}
    for row in conn.execute(
            f"SELECT message_id, media_kind FROM social_message_media WHERE message_id IN ({marks}) ORDER BY id",
            params).fetchall():
        kinds.setdefault(int(row["message_id"]), []).append(str(row["media_kind"]))
    return rows, kinds


def last_message_summary(rows: dict, kinds: dict, last_id: int) -> Optional[dict[str, Any]]:
    row = rows.get(last_id)
    if not row:
        return None
    return {"preview": preview_text(row, kinds.get(last_id, [])), "at": str(row["created_at"] or "")}


def group_last_ids(conn: Any, group_ids: Iterable[int]) -> dict[int, int]:
    keys = [group_key(group_id) for group_id in group_ids]
    if not keys:
        return {}
    marks, params = in_clause(keys)
    rows = conn.execute(
        f"SELECT conversation_key, MAX(id) AS last_id FROM social_messages WHERE conversation_key IN ({marks}) "
        "GROUP BY conversation_key", params,
    ).fetchall()
    return {as_int(str(row["conversation_key"]).split(":", 1)[1]): as_int(row["last_id"]) for row in rows}


def last_messages(conn: Any, last_ids: Iterable[int]) -> tuple[dict, dict]:
    return _last_rows(conn, list(last_ids))


def my_memberships(conn: Any, me: str) -> dict[int, dict[str, Any]]:
    groups = active_group_ids(conn, me)
    if not groups:
        return {}
    marks, params = in_clause(groups)
    rows = conn.execute(
        f"SELECT m.*, g.name AS group_name FROM social_group_members m "
        f"JOIN social_groups g ON g.id = m.group_id WHERE m.member_identity = ? AND m.group_id IN ({marks})",
        (me, *params),
    ).fetchall()
    return {int(row["group_id"]): row_dict(row) for row in rows}


def group_unread(conn: Any, me: str, membership: dict[str, Any]) -> int:
    return unread_count(conn, group_key(int(membership["group_id"])), me,
                        as_int(membership.get("last_read_message_id")), str(membership.get("joined_at") or ""))


def dm_unread(conn: Any, me: str, key: str) -> int:
    return unread_count(conn, key, me, dm_read_cursor(conn, key, me))


def list_conversations(conn: Any, user: dict) -> list[dict[str, Any]]:
    """Direct and group conversations with a last message, newest first."""
    from .presence_service import presence_map

    me = user_identity(user)
    dm_last = dm_keys(conn, me)
    memberships = my_memberships(conn, me)
    group_last = group_last_ids(conn, memberships)
    rows, kinds = _last_rows(conn, [*dm_last.values(), *group_last.values()])
    from .social_relations import presence_visible

    peers = {key: dm_peer(parse_conversation_key(key), me) for key in dm_last}
    people = load_people(conn, peers.values())
    visible = presence_visible(conn, me, peers.values())
    presence = presence_map(conn, [item for item in peers.values() if item in visible])
    items = []
    for key, last_id in dm_last.items():
        peer = peers[key]
        person = people.get(peer)
        items.append({
            "conversation_key": key, "type": "dm",
            "title": person["name"] if person else "已注销用户",
            "avatar_url": avatar_url(peer) if person else None,
            "last_message": last_message_summary(rows, kinds, last_id),
            "unread": dm_unread(conn, me, key),
            "online": bool(presence[peer]["online"]) if peer in presence else None,
            "peer_identity": peer,
            "_sort": last_id,
        })
    for group_id, membership in memberships.items():
        last_id = group_last.get(group_id, 0)
        items.append({
            "conversation_key": group_key(group_id), "type": "group",
            "title": str(membership["group_name"] or ""),
            "avatar_url": None,
            "last_message": last_message_summary(rows, kinds, last_id),
            "unread": group_unread(conn, me, membership),
            "group_id": group_id,
            "muted": bool(as_int(membership.get("muted"))),  # additive
            "_sort": last_id,
        })
    items.sort(key=lambda item: item.pop("_sort"), reverse=True)
    return items


def unread_total(conn: Any, me: str) -> int:
    total = sum(dm_unread(conn, me, key) for key in dm_keys(conn, me))
    for membership in my_memberships(conn, me).values():
        if not as_int(membership.get("muted")):
            total += group_unread(conn, me, membership)
    return total
