"""Read-only relationship queries shared by the social services (no writes here).

Friendships (``social_friendships``: one row per sorted identity pair), group
co-membership and the presence audience ("my friends + everyone in a group
with me") live here so identity, friend, group, message and push services can
all use them without importing each other.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .social_common import blocked_by, blockers_of, ensure_schema, in_clause, pair, row_dict

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"


def get_friendship(conn: Any, identity_a: str, identity_b: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    first, second = pair(identity_a, identity_b)
    row = conn.execute(
        "SELECT * FROM social_friendships WHERE identity_a = ? AND identity_b = ? LIMIT 1", (first, second),
    ).fetchone()
    return row_dict(row) if row is not None else None


def get_friendship_by_id(conn: Any, friendship_id: int) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM social_friendships WHERE id = ? LIMIT 1", (int(friendship_id),)).fetchone()
    return row_dict(row) if row is not None else None


def are_friends(conn: Any, identity_a: str, identity_b: str) -> bool:
    row = get_friendship(conn, identity_a, identity_b)
    return bool(row and row.get("status") == STATUS_ACCEPTED)


def other_side(row: dict[str, Any], me: str) -> str:
    return str(row["identity_b"] if row["identity_a"] == me else row["identity_a"])


def my_remark(row: dict[str, Any], me: str) -> str:
    return str((row.get("remark_a") if row["identity_a"] == me else row.get("remark_b")) or "")


def friendship_rows(conn: Any, me: str, *, statuses: Iterable[str] = (STATUS_ACCEPTED,)) -> list[dict[str, Any]]:
    ensure_schema(conn)
    marks, params = in_clause(statuses)
    rows = conn.execute(
        f"SELECT * FROM social_friendships WHERE (identity_a = ? OR identity_b = ?) AND status IN ({marks}) "
        "ORDER BY id",
        (me, me, *params),
    ).fetchall()
    return [row_dict(row) for row in rows]


def friend_identities(conn: Any, me: str) -> set[str]:
    return {other_side(row, me) for row in friendship_rows(conn, me)}


def count_friends(conn: Any, me: str) -> int:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM social_friendships WHERE (identity_a = ? OR identity_b = ?) AND status = ?",
        (me, me, STATUS_ACCEPTED),
    ).fetchone()
    return int(row["n"] or 0)


def relations_for(conn: Any, me: str, identities: Iterable[str]) -> dict[str, str]:
    """``{identity: 'none'|'friend'|'pending_out'|'pending_in'|'blocked'}`` from my point of view.

    ``blocked`` only when *I* blocked them; being blocked by someone is never revealed.
    """
    others = sorted({str(item) for item in identities if item and item != me})
    result = {identity: "none" for identity in others}
    if not others:
        return result
    rows = {other_side(row, me): row for row in friendship_rows(conn, me, statuses=(STATUS_PENDING, STATUS_ACCEPTED))}
    my_blocks = blocked_by(conn, me)
    for identity in others:
        row = rows.get(identity)
        if identity in my_blocks:
            result[identity] = "blocked"
        elif row and row["status"] == STATUS_ACCEPTED:
            result[identity] = "friend"
        elif row and row["status"] == STATUS_PENDING:
            result[identity] = "pending_out" if row["requester_identity"] == me else "pending_in"
    return result


def active_group_ids(conn: Any, me: str) -> list[int]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT m.group_id FROM social_group_members m JOIN social_groups g ON g.id = m.group_id "
        "WHERE m.member_identity = ? AND m.left_at IS NULL AND g.dissolved_at IS NULL ORDER BY m.group_id",
        (me,),
    ).fetchall()
    return [int(row["group_id"]) for row in rows]


def group_member_identities(conn: Any, group_id: int) -> list[str]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT member_identity FROM social_group_members WHERE group_id = ? AND left_at IS NULL ORDER BY id",
        (int(group_id),),
    ).fetchall()
    return [str(row["member_identity"]) for row in rows]


def co_group_members(conn: Any, me: str) -> set[str]:
    groups = active_group_ids(conn, me)
    if not groups:
        return set()
    marks, params = in_clause(groups)
    rows = conn.execute(
        f"SELECT DISTINCT member_identity FROM social_group_members WHERE group_id IN ({marks}) AND left_at IS NULL",
        params,
    ).fetchall()
    return {str(row["member_identity"]) for row in rows} - {me}


def share_group(conn: Any, identity_a: str, identity_b: str) -> bool:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM social_group_members a JOIN social_group_members b ON b.group_id = a.group_id "
        "JOIN social_groups g ON g.id = a.group_id "
        "WHERE a.member_identity = ? AND b.member_identity = ? AND a.left_at IS NULL AND b.left_at IS NULL "
        "AND g.dissolved_at IS NULL LIMIT 1",
        (identity_a, identity_b),
    ).fetchone()
    return row is not None


def presence_audience(conn: Any, me: str) -> set[str]:
    """Who sees my online changes: friends and co-group members, never across a block."""
    hidden = blocked_by(conn, me) | blockers_of(conn, me)
    return (friend_identities(conn, me) | co_group_members(conn, me)) - {me} - hidden


def presence_visible(conn: Any, me: str, identities: Iterable[str]) -> set[str]:
    """Whose online/last-seen/mood ``me`` may see: self, friends and co-group members, never across a block."""
    wanted = {str(item) for item in identities if item}
    if not wanted:
        return set()
    visible = {me} & wanted
    others = wanted - {me}
    if others:
        allowed = (friend_identities(conn, me) | co_group_members(conn, me)) - blocked_by(conn, me) - blockers_of(conn, me)
        visible |= others & allowed
    return visible
