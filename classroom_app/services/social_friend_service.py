"""好友与黑名单 (social doc §2, §3).

Rules: no self / AI friends; a block in either direction yields the uniform
refusal 「暂时无法添加该用户」 (never revealing who blocked whom); ≤ 30 requests
per person per UTC day; ≤ 1000 friends; a declined requester waits 7 days.
A pending request in the other direction turns a new request into an instant
friendship. Blocking (``private_message_blocks``, the single truth shared with
classroom private messages) also ends the friendship and drops pending requests.
"""
from __future__ import annotations

from typing import Any, Optional

from .message_center_service import (
    add_private_message_block,
    create_social_notification,
    list_private_message_blocks,
    remove_private_message_block,
)
from .presence_service import presence_map
from .social_common import (
    SocialError,
    add_event,
    as_int,
    blocked_either,
    dm_key,
    ensure_schema,
    iso_after,
    iso_before,
    normalize_identity,
    now_iso,
    pair,
    parse_iso,
    utc_day_start_iso,
    utc_iso_from_any,
    utc_now,
    user_identity,
)
from .social_conversation_service import dm_unread, insert_system_message
from .social_identity_service import avatar_url, friend_people, homepage_url, load_people, public_users, require_person
from .social_relations import (
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_PENDING,
    count_friends,
    friendship_rows,
    get_friendship,
    get_friendship_by_id,
    my_remark,
    other_side,
)

UNIFORM_REFUSAL = "暂时无法添加该用户"
REQUEST_MESSAGE_MAX = 100
REMARK_MAX = 30
DAILY_REQUEST_LIMIT = 30
FRIEND_LIMIT = 1000
DECLINE_COOLDOWN_DAYS = 7
FRIENDS_LINK = "/profile?section=notifications"
WELCOME_TEXT = "你们已成为好友，开始聊天吧"


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

LOG_REQUEST = "request"
LOG_WITHDRAW = "withdraw"
WITHDRAW_COOLDOWN_HOURS = 24
NOTIFY_AGGREGATE_HOURS = 24


def _log(conn: Any, requester: str, target: str, event: str) -> int:
    from ..db.connection import execute_insert_returning_id

    return execute_insert_returning_id(
        conn,
        "INSERT INTO social_friend_request_log (requester_identity, target_identity, event, created_at) "
        "VALUES (?, ?, ?, ?)", (requester, target, event, now_iso()),
    )


def _requests_today(conn: Any, me: str) -> int:
    """Counted from the append-only log, so withdrawing a request never refunds the daily cap."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM social_friend_request_log WHERE requester_identity = ? AND event = ? "
        "AND created_at >= ?", (me, LOG_REQUEST, utc_day_start_iso()),
    ).fetchone()
    return as_int(row["n"]) if row else 0


def _recent_log_id(conn: Any, requester: str, target: str, event: str, hours: int) -> int:
    row = conn.execute(
        "SELECT MIN(id) AS first_id FROM social_friend_request_log WHERE requester_identity = ? "
        "AND target_identity = ? AND event = ? AND created_at >= ?",
        (requester, target, event, iso_before(hours=hours)),
    ).fetchone()
    return as_int(row["first_id"]) if row else 0


def _check_can_request(conn: Any, me: str, target: str, existing: Optional[dict[str, Any]]) -> None:
    if target == me:
        raise SocialError("不能添加自己为好友。", 400)
    if blocked_either(conn, me, target):
        raise SocialError(UNIFORM_REFUSAL, 403)
    if existing and existing["status"] == STATUS_ACCEPTED:
        raise SocialError("你们已经是好友了。", 409)
    if existing and existing["status"] == STATUS_DECLINED and existing["requester_identity"] == me:
        until = parse_iso(existing.get("declined_until"))
        if until and until > utc_now():
            raise SocialError("对方暂未通过你的申请，请 7 天后再试。", 429)
    if _recent_log_id(conn, me, target, LOG_WITHDRAW, WITHDRAW_COOLDOWN_HOURS):
        raise SocialError("刚撤回过对该用户的申请，请 24 小时后再试。", 429)
    if _requests_today(conn, me) >= DAILY_REQUEST_LIMIT:
        raise SocialError(f"今天的好友申请已达 {DAILY_REQUEST_LIMIT} 个上限，请明天再试。", 429)
    for identity in (me, target):
        if count_friends(conn, identity) >= FRIEND_LIMIT:
            raise SocialError(f"好友数量已达 {FRIEND_LIMIT} 人上限。", 409)


SCOPE_REFUSAL = "当前境界尚不能添加该用户"


def _check_scope(conn: Any, user: dict, target: str) -> None:
    from .social_scope_service import can_add

    allowed, reason = can_add(conn, user, target)
    if not allowed:
        raise SocialError(f"{SCOPE_REFUSAL}。{reason}" if reason else f"{SCOPE_REFUSAL}。", 403)


def _notify(conn: Any, recipient: str, *, title: str, body: str, actor: str, actor_name: str, ref_id: str) -> None:
    from ..db.savepoint import best_effort

    with best_effort(conn, "social_notify", log_prefix="[SOCIAL]"):
        create_social_notification(
            conn, recipient_identity=recipient, title=title, body_preview=body, link_url=FRIENDS_LINK,
            actor_identity=actor, actor_display_name=actor_name, ref_id=ref_id,
        )


def _become_friends(conn: Any, row: dict[str, Any], events: Optional[list]) -> None:
    timestamp = now_iso()
    conn.execute(
        "UPDATE social_friendships SET status = ?, responded_at = ?, declined_until = '' WHERE id = ?",
        (STATUS_ACCEPTED, timestamp, int(row["id"])),
    )
    first, second = row["identity_a"], row["identity_b"]
    key = dm_key(first, second)
    insert_system_message(conn, key, WELCOME_TEXT, events, audience=(first, second))
    people = load_people(conn, (first, second))
    for me, other in ((first, second), (second, first)):
        name = (people.get(other) or {}).get("name") or ""
        _notify(conn, me, title=f"你和 {name} 已成为好友", body=WELCOME_TEXT, actor=other, actor_name=name,
                ref_id=f"friend_accepted:{row['id']}:{timestamp}")
        add_event(events, [me], {"type": "friend_accepted", "identity": other, "conversation_key": key,
                                 "request_id": int(row["id"])})


def send_request(conn: Any, user: dict, target_identity: Any, message: Any = "",
                 events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    target = require_person(conn, target_identity)["identity"]
    text = str(message or "").strip()[:REQUEST_MESSAGE_MAX]
    existing = get_friendship(conn, me, target)
    _check_can_request(conn, me, target, existing)
    answering = bool(existing and existing["status"] == STATUS_PENDING and existing["requester_identity"] == target)
    if not answering:  # §12.1: only *sending* is gated by 境界; answering their request never is
        _check_scope(conn, user, target)
    if existing and existing["status"] == STATUS_PENDING:
        if existing["requester_identity"] == target:
            _become_friends(conn, existing, events)
            return {"status": STATUS_ACCEPTED, "request_id": int(existing["id"])}
        return {"status": STATUS_PENDING, "request_id": int(existing["id"])}
    first, second = pair(me, target)
    timestamp = now_iso()
    if existing:
        conn.execute(
            "UPDATE social_friendships SET status = ?, requester_identity = ?, request_message = ?, created_at = ?, "
            "responded_at = NULL, declined_until = '' WHERE id = ?",
            (STATUS_PENDING, me, text, timestamp, int(existing["id"])),
        )
        request_id = int(existing["id"])
    else:
        from ..db.connection import execute_insert_returning_id

        request_id = execute_insert_returning_id(
            conn,
            "INSERT INTO social_friendships (identity_a, identity_b, status, requester_identity, request_message, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (first, second, STATUS_PENDING, me, text, timestamp),
        )
    log_id = _log(conn, me, target, LOG_REQUEST)
    # One notification per pair per 24 h window (anchored at the window's first request): re-requests
    # within the window reuse its ref_id, which the message center de-duplicates instead of stacking.
    anchor = _recent_log_id(conn, me, target, LOG_REQUEST, NOTIFY_AGGREGATE_HOURS) or log_id
    sender = load_people(conn, [me]).get(me) or {}
    _notify(conn, target, title=f"{sender.get('name') or '有人'} 请求添加你为好友", body=text,
            actor=me, actor_name=str(sender.get("name") or ""), ref_id=f"friend_request:{me}:{target}:{anchor}")
    request = _serialize_requests(conn, target, [get_friendship_by_id(conn, request_id)])
    add_event(events, [target], {"type": "friend_request", "request": request[0] if request else None})
    _wake_wanderer(conn, request_id, me, target)
    return {"status": STATUS_PENDING, "request_id": request_id}


def _wake_wanderer(conn: Any, request_id: int, requester: str, target: str) -> None:
    """A request to a 散修 schedules its personality-driven decision (social doc §10)."""
    from ..db.savepoint import best_effort
    from .wanderer_social_triggers import on_friend_request

    with best_effort(conn, "wanderer_friend_hook", log_prefix="[SOCIAL]"):
        on_friend_request(conn, request_id, requester, target)


def _pending_for(conn: Any, request_id: int, me: str, *, as_addressee: bool) -> dict[str, Any]:
    ensure_schema(conn)
    row = get_friendship_by_id(conn, request_id)
    if not row or row["status"] != STATUS_PENDING or me not in (row["identity_a"], row["identity_b"]):
        raise SocialError("好友申请不存在或已处理。", 404)
    is_requester = row["requester_identity"] == me
    if as_addressee and is_requester:
        raise SocialError("只有被申请人可以处理该申请。", 403)
    if not as_addressee and not is_requester:
        raise SocialError("只能撤回自己发出的申请。", 403)
    return row


def accept_request(conn: Any, user: dict, request_id: int, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    row = _pending_for(conn, request_id, me, as_addressee=True)
    other = other_side(row, me)
    if blocked_either(conn, me, other):
        raise SocialError(UNIFORM_REFUSAL, 403)
    if count_friends(conn, me) >= FRIEND_LIMIT or count_friends(conn, other) >= FRIEND_LIMIT:
        raise SocialError(f"好友数量已达 {FRIEND_LIMIT} 人上限。", 409)
    _become_friends(conn, row, events)
    return {"status": STATUS_ACCEPTED, "request_id": int(row["id"]), "conversation_key": dm_key(me, other)}


def decline_request(conn: Any, user: dict, request_id: int, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    row = _pending_for(conn, request_id, me, as_addressee=True)
    conn.execute(
        "UPDATE social_friendships SET status = ?, responded_at = ?, declined_until = ? WHERE id = ?",
        (STATUS_DECLINED, now_iso(), iso_after(days=DECLINE_COOLDOWN_DAYS), int(row["id"])),
    )
    return {"status": STATUS_DECLINED, "request_id": int(row["id"])}


def withdraw_request(conn: Any, user: dict, request_id: int, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    row = _pending_for(conn, request_id, me, as_addressee=False)
    conn.execute("DELETE FROM social_friendships WHERE id = ?", (int(row["id"]),))
    _log(conn, me, other_side(row, me), LOG_WITHDRAW)  # starts the 24 h re-request cooldown for this pair
    add_event(events, [other_side(row, me)], {"type": "friend_removed", "identity": me, "reason": "withdrawn"})
    return {"status": "withdrawn", "request_id": int(row["id"])}


def _serialize_requests(conn: Any, me: str, rows: list[Optional[dict[str, Any]]]) -> list[dict[str, Any]]:
    items = [row for row in rows if row]
    users = {item["identity"]: item for item in public_users(conn, me, [other_side(row, me) for row in items])}
    result = []
    for row in items:
        other = other_side(row, me)
        if other not in users:
            continue
        result.append({
            "id": int(row["id"]),
            "user": users[other],
            "message": str(row.get("request_message") or ""),
            "direction": "outgoing" if row["requester_identity"] == me else "incoming",
            "created_at": str(row["created_at"] or ""),
        })
    return result


def list_requests(conn: Any, user: dict) -> dict[str, list[dict[str, Any]]]:
    me = user_identity(user)
    rows = friendship_rows(conn, me, statuses=(STATUS_PENDING,))
    serialized = _serialize_requests(conn, me, sorted(rows, key=lambda row: str(row["created_at"]), reverse=True))
    return {
        "incoming": [item for item in serialized if item["direction"] == "incoming"],
        "outgoing": [item for item in serialized if item["direction"] == "outgoing"],
    }


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------

def list_friends(conn: Any, user: dict) -> dict[str, Any]:
    """FriendCards, online first then by name, plus counts."""
    me = user_identity(user)
    rows, people = friend_people(conn, me)
    presence = presence_map(conn, people.keys())
    items = []
    for row in rows:
        other = other_side(row, me)
        person = people.get(other)
        if not person:
            continue
        state = presence.get(other, {"online": False, "last_seen_at": None})
        key = dm_key(me, other)
        items.append({
            "identity": other,
            "name": person["name"],
            "remark": my_remark(row, me),
            "role_label": person["role_label"],
            "avatar_url": avatar_url(other),
            "is_wanderer": bool(person["is_wanderer"]),
            "tier_badge": person["tier_badge"],
            "mood": person["mood"],
            "online": bool(state["online"]),
            "last_seen_at": state["last_seen_at"],
            "conversation_key": key,
            "unread": dm_unread(conn, me, key),
            "homepage_url": homepage_url(person),
        })
    items.sort(key=lambda item: (not item["online"], (item["remark"] or item["name"]).lower()))
    pending_in = sum(1 for row in friendship_rows(conn, me, statuses=(STATUS_PENDING,))
                     if row["requester_identity"] != me)
    return {"items": items, "counts": {"online": sum(1 for item in items if item["online"]),
                                       "total": len(items), "pending_in": pending_in}}


def _accepted_row(conn: Any, me: str, other: str) -> dict[str, Any]:
    row = get_friendship(conn, me, other)
    if not row or row["status"] != STATUS_ACCEPTED:
        raise SocialError("你们还不是好友。", 404)
    return row


def delete_friend(conn: Any, user: dict, identity: Any, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    other = normalize_identity(identity)
    row = _accepted_row(conn, me, other)
    conn.execute("DELETE FROM social_friendships WHERE id = ?", (int(row["id"]),))
    add_event(events, [me], {"type": "friend_removed", "identity": other, "reason": "deleted"})
    add_event(events, [other], {"type": "friend_removed", "identity": me, "reason": "deleted"})
    return {"status": "deleted", "identity": other}


def set_remark(conn: Any, user: dict, identity: Any, remark: Any) -> dict[str, Any]:
    me = user_identity(user)
    other = normalize_identity(identity)
    row = _accepted_row(conn, me, other)
    text = str(remark or "").strip()[:REMARK_MAX]
    column = "remark_a" if row["identity_a"] == me else "remark_b"
    conn.execute(f"UPDATE social_friendships SET {column} = ? WHERE id = ?", (text, int(row["id"])))
    return {"identity": other, "remark": text}


# ---------------------------------------------------------------------------
# Blacklist (§3) — reuses the classroom private-message block list
# ---------------------------------------------------------------------------

def list_blocks(conn: Any, user: dict) -> list[dict[str, Any]]:
    """Both kinds: ``scope`` 'all' (shared with classroom private messages) or 'social' (social only)."""
    me = user_identity(user)
    items = []
    for block in list_private_message_blocks(conn, user):
        identity = str(block.get("identity") or "")
        items.append({
            "identity": identity,
            "name": str(block.get("display_name") or ""),
            "avatar_url": avatar_url(identity) if identity.split(":")[0] in {"student", "teacher"} else None,
            "created_at": utc_iso_from_any(block.get("created_at")),
            "scope": "all",
        })
    ensure_schema(conn)
    rows = [dict(row) for row in conn.execute(
        "SELECT blocked_identity, created_at FROM social_blocks WHERE owner_identity = ?", (me,)).fetchall()]
    people = load_people(conn, [row["blocked_identity"] for row in rows])
    for row in rows:
        identity = str(row["blocked_identity"])
        items.append({
            "identity": identity,
            "name": str((people.get(identity) or {}).get("name") or ""),
            "avatar_url": avatar_url(identity) if identity.split(":")[0] in {"student", "teacher"} else None,
            "created_at": utc_iso_from_any(row["created_at"]),
            "scope": "social",
        })
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


def block_user(conn: Any, user: dict, identity: Any, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    other = normalize_identity(identity)
    if other == me:
        raise SocialError("不能把自己加入黑名单。", 400)
    person = require_person(conn, other)
    if person.get("role") == "teacher":
        # The classroom list refuses teachers (classroom duty contacts): block them in the social
        # scope only, so their classroom private messages and notifications keep arriving.
        ensure_schema(conn)
        conn.execute("INSERT INTO social_blocks (owner_identity, blocked_identity, created_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (owner_identity, blocked_identity) DO NOTHING", (me, other, now_iso()))
    else:
        try:
            add_private_message_block(conn, user, contact_identity=other)
        except ValueError as exc:
            raise SocialError("该用户不能加入黑名单。", 400) from exc
    row = get_friendship(conn, me, other)
    if row and row["status"] in (STATUS_ACCEPTED, STATUS_PENDING):
        conn.execute("DELETE FROM social_friendships WHERE id = ?", (int(row["id"]),))
        add_event(events, [me], {"type": "friend_removed", "identity": other, "reason": "blocked"})
        # The other side sees an ordinary removal; who blocked whom is never revealed.
        add_event(events, [other], {"type": "friend_removed", "identity": me, "reason": "deleted"})
    return {"identity": other, "blocked": True}


def unblock_user(conn: Any, user: dict, identity: Any) -> dict[str, Any]:
    me = user_identity(user)
    other = normalize_identity(identity)
    removed = remove_private_message_block(conn, user, contact_identity=other)
    ensure_schema(conn)
    social = conn.execute("DELETE FROM social_blocks WHERE owner_identity = ? AND blocked_identity = ?", (me, other))
    removed = int(removed) + as_int(getattr(social, "rowcount", 0))
    return {"identity": other, "blocked": False, "removed": removed}
