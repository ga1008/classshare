"""群组 (social doc §4).

Create with ≥ 1 friend; only friends can be invited (a target who blocked the
inviter is skipped and reported). ≤ 200 members per group, ≤ 50 groups and
≤ 10 created groups per person. Owner: admins, transfer, dissolve. Admins:
kick members, edit name / announcement. Members: leave, nickname, mute.
Join / leave / kick / announcement changes produce system messages.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .presence_service import presence_map
from .social_common import (
    SocialError,
    add_event,
    as_int,
    blocked_either,
    ensure_schema,
    group_key,
    in_clause,
    normalize_identity,
    now_iso,
    row_dict,
    serialize_on,
    user_identity,
)
from .social_conversation_service import (
    group_last_ids,
    group_unread,
    insert_system_message,
    last_message_summary,
    last_messages,
    load_group,
    load_membership,
    media_payload,
    my_memberships,
)
from .social_identity_service import load_people, public_users
from .social_relations import are_friends, group_member_identities

GROUP_MAX_MEMBERS = 200
MAX_GROUPS_PER_PERSON = 50
MAX_CREATED_GROUPS = 10
NAME_MAX = 30
ANNOUNCEMENT_MAX = 500
NICKNAME_MAX = 20
FILES_PAGE = 50
ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER = "owner", "admin", "member"
MEDIA_KINDS = ("image", "video", "audio", "file")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _active_group(conn: Any, group_id: int) -> dict[str, Any]:
    group = load_group(conn, group_id)
    if not group or group.get("dissolved_at"):
        raise SocialError("群聊不存在或已解散。", 404)
    return group


def _require_member(conn: Any, group_id: int, me: str, roles: Iterable[str] = ()) -> dict[str, Any]:
    membership = load_membership(conn, group_id, me)
    if not membership or membership.get("left_at"):
        raise SocialError("你不在该群聊中。", 403)
    wanted = tuple(roles)
    if wanted and membership["role"] not in wanted:
        raise SocialError("只有群主或管理员可以这样做。" if ROLE_ADMIN in wanted else "只有群主可以这样做。", 403)
    return membership


def _active_count(conn: Any, group_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM social_group_members WHERE group_id = ? AND left_at IS NULL", (int(group_id),),
    ).fetchone()
    return as_int(row["n"]) if row else 0


def _groups_joined(conn: Any, identity: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM social_group_members m JOIN social_groups g ON g.id = m.group_id "
        "WHERE m.member_identity = ? AND m.left_at IS NULL AND g.dissolved_at IS NULL", (identity,),
    ).fetchone()
    return as_int(row["n"]) if row else 0


def _groups_created(conn: Any, identity: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM social_groups WHERE owner_identity = ? AND dissolved_at IS NULL", (identity,),
    ).fetchone()
    return as_int(row["n"]) if row else 0


def _names(conn: Any, identities: Iterable[str]) -> dict[str, str]:
    return {key: person["name"] for key, person in load_people(conn, identities).items()}


def _display(conn: Any, group_id: int, identities: Iterable[str]) -> str:
    """Names joined by 、 using group nicknames when set."""
    wanted = list(identities)
    names = _names(conn, wanted)
    marks, params = in_clause(wanted)
    nicknames = {str(row["member_identity"]): str(row["nickname"] or "") for row in conn.execute(
        f"SELECT member_identity, nickname FROM social_group_members WHERE group_id = ? AND member_identity IN ({marks})",
        (int(group_id), *params)).fetchall()}
    return "、".join(nicknames.get(item) or names.get(item, "成员") for item in wanted)


# ---------------------------------------------------------------------------
# Membership writes
# ---------------------------------------------------------------------------

def _add_member_row(conn: Any, group_id: int, identity: str, role: str, existing: Optional[dict]) -> None:
    timestamp = now_iso()
    last_row = conn.execute("SELECT MAX(id) AS n FROM social_messages WHERE conversation_key = ?",
                            (group_key(group_id),)).fetchone()
    last_id = as_int(last_row["n"]) if last_row else 0
    if existing:
        conn.execute(
            "UPDATE social_group_members SET role = ?, left_at = NULL, joined_at = ?, muted = 0, nickname = '', "
            "last_read_message_id = ? WHERE id = ?",
            (role, timestamp, last_id, int(existing["id"])),
        )
        return
    conn.execute(
        "INSERT INTO social_group_members (group_id, member_identity, role, joined_at, last_read_message_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (int(group_id), identity, role, timestamp, last_id),
    )


def _skip_reason(conn: Any, group_id: int, inviter: str, identity: str, person: Optional[dict], capacity: int) -> str:
    existing = load_membership(conn, group_id, identity)
    if identity == inviter or (existing and not existing.get("left_at")):
        return "已在群中"
    if not (person or {}).get("active"):
        return "用户不存在或已停用"
    if not are_friends(conn, inviter, identity):
        return "只能邀请好友进群"
    if blocked_either(conn, inviter, identity):
        return "暂时无法邀请该用户"
    if _groups_joined(conn, identity) >= MAX_GROUPS_PER_PERSON:
        return f"对方加入的群聊已达 {MAX_GROUPS_PER_PERSON} 个上限"
    if capacity <= 0:
        return f"群成员已达 {GROUP_MAX_MEMBERS} 人上限"
    return ""


def _invite(conn: Any, group: dict[str, Any], inviter: str, identities: Iterable[str]) -> dict[str, list]:
    group_id = int(group["id"])
    wanted = list(dict.fromkeys(identities))
    added: list[str] = []
    skipped: list[dict[str, str]] = []
    limit = min(GROUP_MAX_MEMBERS, as_int(group.get("max_members"), GROUP_MAX_MEMBERS))
    capacity = limit - _active_count(conn, group_id)
    people = load_people(conn, wanted)
    for identity in wanted:
        reason = _skip_reason(conn, group_id, inviter, identity, people.get(identity), capacity)
        if reason:
            skipped.append({"identity": identity, "reason": reason})
            continue
        _add_member_row(conn, group_id, identity, ROLE_MEMBER, load_membership(conn, group_id, identity))
        added.append(identity)
        capacity -= 1
    return {"added": added, "skipped": skipped}


def _changed(events: Optional[list], conn: Any, group_id: int, reason: str, extra: Iterable[str] = ()) -> None:
    targets = set(group_member_identities(conn, group_id)) | set(extra)
    add_event(events, targets, {"type": "group_changed", "group_id": int(group_id), "reason": reason})


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _cards(conn: Any, me: str, memberships: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    last_ids = group_last_ids(conn, memberships)
    rows, kinds = last_messages(conn, last_ids.values())
    cards = []
    for group_id, membership in memberships.items():
        members = group_member_identities(conn, group_id)
        presence = presence_map(conn, members)
        cards.append({
            "id": group_id,
            "name": str(membership.get("group_name") or ""),
            "avatar_url": None,
            "member_count": len(members),
            "online_count": sum(1 for item in presence.values() if item["online"]),
            "my_role": str(membership["role"]),
            "conversation_key": group_key(group_id),
            "unread": group_unread(conn, me, membership),
            "last_message": last_message_summary(rows, kinds, last_ids.get(group_id, 0)),
            "_sort": last_ids.get(group_id, 0),
        })
    cards.sort(key=lambda card: card.pop("_sort"), reverse=True)
    return cards


def list_groups(conn: Any, user: dict) -> list[dict[str, Any]]:
    me = user_identity(user)
    return _cards(conn, me, my_memberships(conn, me))


def group_card(conn: Any, me: str, group_id: int) -> dict[str, Any]:
    memberships = {gid: row for gid, row in my_memberships(conn, me).items() if gid == int(group_id)}
    if not memberships:
        raise SocialError("你不在该群聊中。", 403)
    return _cards(conn, me, memberships)[0]


def _member_cards(conn: Any, me: str, group_id: int) -> list[dict[str, Any]]:
    rows = [row_dict(row) for row in conn.execute(
        "SELECT member_identity, role, nickname, joined_at FROM social_group_members "
        "WHERE group_id = ? AND left_at IS NULL ORDER BY id", (int(group_id),)).fetchall()]
    identities = [row["member_identity"] for row in rows]
    users = {item["identity"]: item for item in public_users(conn, me, identities)}
    users.update({item["identity"]: {**item, "relation": "self"} for item in public_users(conn, "", [me])})
    from .social_relations import presence_visible

    visible = presence_visible(conn, me, identities)  # co-members, minus anyone blocked either way
    presence = presence_map(conn, [item for item in identities if item in visible])
    members = []
    for row in rows:
        card = users.get(row["member_identity"])
        if not card:
            continue
        state = presence.get(row["member_identity"], {"online": None, "last_seen_at": None})
        members.append({**card, "group_role": row["role"], "nickname": str(row["nickname"] or ""),
                        "online": None if state["online"] is None else bool(state["online"]),
                        "last_seen_at": state["last_seen_at"], "joined_at": row["joined_at"]})
    order = {ROLE_OWNER: 0, ROLE_ADMIN: 1, ROLE_MEMBER: 2}
    members.sort(key=lambda item: (not item["online"], order.get(item["group_role"], 3), item["name"]))
    return members


def group_detail(conn: Any, user: dict, group_id: int) -> dict[str, Any]:
    """GroupCard + announcement, owner and members (online first)."""
    me = user_identity(user)
    group = _active_group(conn, group_id)
    mine = _require_member(conn, group_id, me)
    detail = group_card(conn, me, group_id)
    detail.update({
        "announcement": str(group["announcement"] or ""),
        "owner_identity": str(group["owner_identity"]),
        "max_members": as_int(group["max_members"], GROUP_MAX_MEMBERS),
        "created_at": str(group["created_at"] or ""),
        "members": _member_cards(conn, me, group_id),
        "me": {"nickname": str(mine.get("nickname") or ""), "muted": bool(as_int(mine.get("muted")))},
    })
    return detail


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _clean_name(raw: Any) -> str:
    name = " ".join(str(raw or "").split())[:NAME_MAX]
    if not name:
        raise SocialError("请填写群名称。", 400)
    return name


def _identity_list(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        raise SocialError("成员列表格式不正确。", 400)
    return list(dict.fromkeys(normalize_identity(item) for item in raw if str(item or "").strip()))


def create_group(conn: Any, user: dict, name: Any, member_identities: Any,
                 events: Optional[list] = None) -> dict[str, Any]:
    from ..db.connection import execute_insert_returning_id

    me = user_identity(user)
    ensure_schema(conn)
    title = _clean_name(name)
    wanted = [item for item in _identity_list(member_identities or []) if item != me]
    if not wanted:
        raise SocialError("建群至少需要选择 1 位好友。", 400)
    if not any(are_friends(conn, me, item) for item in wanted):
        raise SocialError("只能拉自己的好友进群。", 400)
    if _groups_created(conn, me) >= MAX_CREATED_GROUPS:
        raise SocialError(f"你创建的群聊已达 {MAX_CREATED_GROUPS} 个上限。", 409)
    if _groups_joined(conn, me) >= MAX_GROUPS_PER_PERSON:
        raise SocialError(f"你加入的群聊已达 {MAX_GROUPS_PER_PERSON} 个上限。", 409)
    timestamp = now_iso()
    group_id = execute_insert_returning_id(
        conn,
        "INSERT INTO social_groups (name, owner_identity, max_members, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (title, me, GROUP_MAX_MEMBERS, timestamp, timestamp),
    )
    _add_member_row(conn, group_id, me, ROLE_OWNER, None)
    result = _invite(conn, load_group(conn, group_id), me, wanted)
    if not result["added"]:
        raise SocialError("所选好友都无法加入群聊。", 400)
    text = f"{_display(conn, group_id, [me])} 创建了群聊，并邀请 {_display(conn, group_id, result['added'])} 加入"
    insert_system_message(conn, group_key(group_id), text, events)
    _changed(events, conn, group_id, "created")
    return {**group_card(conn, me, group_id), "skipped": result["skipped"]}


def update_group(conn: Any, user: dict, group_id: int, *, name: Any = None, announcement: Any = None,
                 events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    _active_group(conn, group_id)
    _require_member(conn, group_id, me, roles=(ROLE_OWNER, ROLE_ADMIN))
    actor = _display(conn, group_id, [me])
    if name is not None:
        title = _clean_name(name)
        conn.execute("UPDATE social_groups SET name = ?, updated_at = ? WHERE id = ?", (title, now_iso(), int(group_id)))
        insert_system_message(conn, group_key(group_id), f"{actor} 将群名称修改为「{title}」", events)
    if announcement is not None:
        text = str(announcement or "").strip()[:ANNOUNCEMENT_MAX]
        conn.execute("UPDATE social_groups SET announcement = ?, updated_at = ? WHERE id = ?",
                     (text, now_iso(), int(group_id)))
        insert_system_message(conn, group_key(group_id),
                              f"{actor} 更新了群公告：{text}" if text else f"{actor} 清空了群公告", events)
    _changed(events, conn, group_id, "updated")
    return group_detail(conn, user, group_id)


def add_members(conn: Any, user: dict, group_id: int, identities: Any, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    group = _active_group(conn, group_id)
    _require_member(conn, group_id, me)
    wanted = _identity_list(identities or [])
    if not wanted:
        raise SocialError("请选择要邀请的好友。", 400)
    result = _invite(conn, group, me, wanted)
    if result["added"]:
        text = f"{_display(conn, group_id, [me])} 邀请 {_display(conn, group_id, result['added'])} 加入了群聊"
        insert_system_message(conn, group_key(group_id), text, events)
        _changed(events, conn, group_id, "members_added")
    return result


def _mark_left(conn: Any, group_id: int, identity: str) -> None:
    conn.execute("UPDATE social_group_members SET left_at = ?, role = ? WHERE group_id = ? AND member_identity = ?",
                 (now_iso(), ROLE_MEMBER, int(group_id), identity))


def remove_member(conn: Any, user: dict, group_id: int, identity: Any, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    target = normalize_identity(identity)
    serialize_on(conn, "social-attention", [group_key(group_id)])
    _active_group(conn, group_id)
    mine = _require_member(conn, group_id, me, roles=(ROLE_OWNER, ROLE_ADMIN))
    theirs = load_membership(conn, group_id, target)
    if target == me or not theirs or theirs.get("left_at"):
        raise SocialError("该成员不在群中。", 404)
    if theirs["role"] == ROLE_OWNER or (mine["role"] == ROLE_ADMIN and theirs["role"] == ROLE_ADMIN):
        raise SocialError("不能移出群主或其他管理员。", 403)
    name = _display(conn, group_id, [target])
    audience = [*group_member_identities(conn, group_id)]
    _mark_left(conn, group_id, target)
    insert_system_message(conn, group_key(group_id), f"{name} 被移出了群聊", events, audience=audience)
    _changed(events, conn, group_id, "member_removed", extra=[target])
    return {"group_id": int(group_id), "removed": target}


def leave_group(conn: Any, user: dict, group_id: int, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    serialize_on(conn, "social-attention", [group_key(group_id)])
    _active_group(conn, group_id)
    mine = _require_member(conn, group_id, me)
    if mine["role"] == ROLE_OWNER:
        raise SocialError("群主需要先转让群主或解散群聊。", 409)
    name = _display(conn, group_id, [me])
    _mark_left(conn, group_id, me)
    insert_system_message(conn, group_key(group_id), f"{name} 退出了群聊", events)
    _changed(events, conn, group_id, "member_left", extra=[me])
    return {"group_id": int(group_id), "left": True}


def dissolve_group(conn: Any, user: dict, group_id: int, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    serialize_on(conn, "social-attention", [group_key(group_id)])
    _active_group(conn, group_id)
    _require_member(conn, group_id, me, roles=(ROLE_OWNER,))
    insert_system_message(conn, group_key(group_id), f"{_display(conn, group_id, [me])} 解散了群聊", events)
    _changed(events, conn, group_id, "dissolved")
    timestamp = now_iso()
    conn.execute("UPDATE social_groups SET dissolved_at = ?, updated_at = ? WHERE id = ?",
                 (timestamp, timestamp, int(group_id)))
    return {"group_id": int(group_id), "dissolved": True}


def _require_other_member(conn: Any, group_id: int, me: str, target: str) -> dict[str, Any]:
    theirs = load_membership(conn, group_id, target)
    if target == me or not theirs or theirs.get("left_at"):
        raise SocialError("该成员不在群中。", 404)
    return theirs


def transfer_owner(conn: Any, user: dict, group_id: int, identity: Any, events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    target = normalize_identity(identity)
    serialize_on(conn, "social-attention", [group_key(group_id)])
    _active_group(conn, group_id)
    _require_member(conn, group_id, me, roles=(ROLE_OWNER,))
    _require_other_member(conn, group_id, me, target)
    conn.execute("UPDATE social_group_members SET role = ? WHERE group_id = ? AND member_identity = ?",
                 (ROLE_MEMBER, int(group_id), me))
    conn.execute("UPDATE social_group_members SET role = ? WHERE group_id = ? AND member_identity = ?",
                 (ROLE_OWNER, int(group_id), target))
    conn.execute("UPDATE social_groups SET owner_identity = ?, updated_at = ? WHERE id = ?",
                 (target, now_iso(), int(group_id)))
    text = f"{_display(conn, group_id, [me])} 将群主转让给了 {_display(conn, group_id, [target])}"
    insert_system_message(conn, group_key(group_id), text, events)
    _changed(events, conn, group_id, "owner_transferred")
    return group_detail(conn, user, group_id)


def set_admin(conn: Any, user: dict, group_id: int, identity: Any, admin: Any,
              events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    target = normalize_identity(identity)
    serialize_on(conn, "social-attention", [group_key(group_id)])
    _active_group(conn, group_id)
    _require_member(conn, group_id, me, roles=(ROLE_OWNER,))
    _require_other_member(conn, group_id, me, target)
    role = ROLE_ADMIN if bool(admin) else ROLE_MEMBER
    conn.execute("UPDATE social_group_members SET role = ? WHERE group_id = ? AND member_identity = ?",
                 (role, int(group_id), target))
    verb = "设为管理员" if role == ROLE_ADMIN else "取消了管理员"
    insert_system_message(conn, group_key(group_id), f"{_display(conn, group_id, [target])} 已被{verb}", events)
    _changed(events, conn, group_id, "admin_changed")
    return {"group_id": int(group_id), "identity": target, "role": role}


def update_me(conn: Any, user: dict, group_id: int, *, nickname: Any = None, muted: Any = None,
              events: Optional[list] = None) -> dict[str, Any]:
    me = user_identity(user)
    _active_group(conn, group_id)
    mine = _require_member(conn, group_id, me)
    if nickname is not None:
        conn.execute("UPDATE social_group_members SET nickname = ? WHERE id = ?",
                     (str(nickname or "").strip()[:NICKNAME_MAX], int(mine["id"])))
    if muted is not None:
        if not isinstance(muted, bool):
            raise SocialError("免打扰设置必须是开启或关闭。", 400)
        conn.execute("UPDATE social_group_members SET muted = ? WHERE id = ?", (1 if bool(muted) else 0, int(mine["id"])))
        add_event(events, [me], {"type": "conversation_preferences", "conversation_key": group_key(group_id),
                                "muted": muted})
    fresh = load_membership(conn, group_id, me) or {}
    return {"group_id": int(group_id), "nickname": str(fresh.get("nickname") or ""),
            "muted": bool(as_int(fresh.get("muted")))}


def list_group_files(conn: Any, user: dict, group_id: int, *, kind: Any = None, before_id: Any = None) -> dict[str, Any]:
    """Media ever posted in the group (since I joined), newest first (§4 群文件)."""
    me = user_identity(user)
    _active_group(conn, group_id)
    mine = _require_member(conn, group_id, me)
    where = ["msg.conversation_key = ?", "msg.recalled_at IS NULL", "msg.created_at >= ?"]
    params: list[Any] = [group_key(group_id), str(mine["joined_at"])]
    if str(kind or "") in MEDIA_KINDS:
        where.append("m.media_kind = ?")
        params.append(str(kind))
    if as_int(before_id) > 0:
        where.append("m.id < ?")
        params.append(as_int(before_id))
    rows = [row_dict(row) for row in conn.execute(
        "SELECT m.*, msg.sender_identity, msg.created_at AS sent_at FROM social_message_media m "
        "JOIN social_messages msg ON msg.id = m.message_id WHERE " + " AND ".join(where)
        + " ORDER BY m.id DESC LIMIT ?", (*params, FILES_PAGE + 1)).fetchall()]
    names = _names(conn, [row["sender_identity"] for row in rows])
    items = [{**media_payload(row), "message_id": int(row["message_id"]), "sender_identity": row["sender_identity"],
              "sender_name": names.get(row["sender_identity"], ""), "sent_at": row["sent_at"]}
             for row in rows[:FILES_PAGE]]
    return {"items": items, "has_more": len(rows) > FILES_PAGE}
