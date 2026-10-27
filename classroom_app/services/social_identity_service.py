"""Public person cards for the social domain (social doc §2, §7 PublicUser / ProfileSidebar).

Only public information leaves this module: name, role label, class/college,
avatar URL, 散修 flag, 境界 badge, mood and recent public real-name posts.
Never email, phone, student number or password state.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional
from urllib.parse import quote

from .message_center_service import build_message_center_link
from .offering_membership_service import offering_student_where, student_offering_where
from .presence_service import presence_map
from .social_common import (
    SocialError,
    as_int,
    blocked_by,
    blockers_of,
    in_clause,
    normalize_identity,
    split_identity,
    user_identity,
)
from .social_relations import friendship_rows, get_friendship, my_remark, other_side, relations_for

SEARCH_LIMIT = 20
SUGGESTION_LIMIT = 20
RECENT_POST_LIMIT = 5
ACTIVE_STUDENT_STATUSES = ("active", "wanderer")


def avatar_url(identity: str) -> str:
    role, pk = split_identity(identity)
    return f"/api/profile/avatar?role={quote(role, safe='')}&user_id={int(pk)}"


def _tier_badge(emblem: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not emblem:
        return None
    return {
        "tier": emblem.get("tier"),
        "name": emblem.get("name") or "",
        "short_name": emblem.get("short_name") or "",
        "emblem_url": emblem.get("emblem_url") or "",
        "score": emblem.get("score"),
    }


def _student_people(conn: Any, ids: list[int]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    from .cultivation_display_service import load_student_emblems
    from .wanderer_identity_service import load_wanderers_by_student_ids

    marks, params = in_clause(ids)
    rows = conn.execute(
        f"SELECT s.id, s.name, s.today_mood, s.enrollment_status, s.college, c.name AS class_name "
        f"FROM students s LEFT JOIN classes c ON c.id = s.class_id WHERE s.id IN ({marks})",
        params,
    ).fetchall()
    wanderers = load_wanderers_by_student_ids(conn, ids)
    emblems = load_student_emblems(conn, ids)
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        student_id = int(row["id"])
        wanderer = wanderers.get(student_id) or {}
        status = str(row["enrollment_status"] or "active")
        identity = f"student:{student_id}"
        people[identity] = {
            "identity": identity,
            "role": "student",
            "name": str(wanderer.get("display_name") or row["name"] or ""),
            "role_label": "散修" if wanderer else "学生",
            "org_label": "散修·无宗门" if wanderer else str(row["class_name"] or row["college"] or ""),
            "is_wanderer": bool(wanderer),
            "wanderer_slug": str(wanderer.get("slug") or ""),
            "mood": str(row["today_mood"] or ""),
            "active": status in ACTIVE_STUDENT_STATUSES and wanderer.get("status") != "dead",
            "tier_badge": _tier_badge(emblems.get(student_id)),
        }
    return people


def _teacher_people(conn: Any, ids: list[int]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    marks, params = in_clause(ids)
    rows = conn.execute(
        f"SELECT id, name, today_mood, college, department, COALESCE(is_active, 1) AS is_active "
        f"FROM teachers WHERE id IN ({marks})",
        params,
    ).fetchall()
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = f"teacher:{int(row['id'])}"
        org = " · ".join(part for part in (str(row["college"] or ""), str(row["department"] or "")) if part)
        people[identity] = {
            "identity": identity,
            "role": "teacher",
            "name": str(row["name"] or ""),
            "role_label": "老师",
            "org_label": org,
            "is_wanderer": False,
            "wanderer_slug": "",
            "mood": str(row["today_mood"] or ""),
            "active": int(row["is_active"] or 0) == 1,
            "tier_badge": None,
        }
    return people


def load_people(conn: Any, identities: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{identity: person}`` for existing students/teachers (inactive ones flagged, not dropped)."""
    students, teachers = [], []
    for identity in {str(item) for item in identities if item}:
        role, pk = split_identity(identity)
        if pk > 0 and role == "student":
            students.append(pk)
        elif pk > 0 and role == "teacher":
            teachers.append(pk)
    people = _student_people(conn, sorted(students))
    people.update(_teacher_people(conn, sorted(teachers)))
    return people


def require_person(conn: Any, identity: Any) -> dict[str, Any]:
    """The active person behind ``identity``; 404 when missing or deactivated."""
    normalized = normalize_identity(identity)
    person = load_people(conn, [normalized]).get(normalized)
    if not person or not person["active"]:
        raise SocialError("用户不存在或已停用。", 404)
    return person


def homepage_url(person: dict[str, Any]) -> Optional[str]:
    if person.get("is_wanderer") and person.get("wanderer_slug"):
        return f"/wanderers/{quote(str(person['wanderer_slug']), safe='')}"
    return None


def public_user(person: dict[str, Any], relation: str) -> dict[str, Any]:
    return {
        "identity": person["identity"],
        "name": person["name"],
        "role_label": person["role_label"],
        "org_label": person["org_label"],
        "avatar_url": avatar_url(person["identity"]),
        "is_wanderer": bool(person["is_wanderer"]),
        "tier_badge": person["tier_badge"],
        "relation": relation,
    }


def public_users(conn: Any, me: str, identities: Iterable[str]) -> list[dict[str, Any]]:
    ordered = [item for item in dict.fromkeys(str(value) for value in identities) if item and item != me]
    people = load_people(conn, ordered)
    relations = relations_for(conn, me, ordered)
    return [public_user(people[item], relations.get(item, "none")) for item in ordered
            if item in people and people[item]["active"]]


def sender_card(person: Optional[dict[str, Any]], identity: str) -> dict[str, Any]:
    """The compact ``Message.sender`` shape."""
    if not person:
        return {"identity": identity, "name": "系统" if identity == "system" else "已注销用户",
                "avatar_url": None, "is_wanderer": False, "tier_badge": None}
    return {
        "identity": identity,
        "name": person["name"],
        "avatar_url": avatar_url(identity),
        "is_wanderer": bool(person["is_wanderer"]),
        "tier_badge": person["tier_badge"],
    }


# ---------------------------------------------------------------------------
# Search and suggestions
# ---------------------------------------------------------------------------

def _clean_query(raw: Any) -> str:
    text = str(raw or "").strip()[:40]
    return "".join(ch for ch in text if ch not in "%_\\")


def search_users(conn: Any, user: dict, query: Any) -> list[dict[str, Any]]:
    """Name, 学号, 工号, email or 散修 name; at most 20; public fields only."""
    me = user_identity(user)
    needle = _clean_query(query)
    if not needle:
        return []
    like = f"%{needle}%"
    student_rows = conn.execute(
        "SELECT id FROM students WHERE COALESCE(enrollment_status, 'active') IN (?, ?) "
        "AND (name LIKE ? OR student_id_number = ? OR LOWER(COALESCE(email, '')) = LOWER(?)) "
        "ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END, id LIMIT ?",
        (*ACTIVE_STUDENT_STATUSES, like, needle, needle, needle, SEARCH_LIMIT + 1),
    ).fetchall()
    teacher_rows = conn.execute(
        "SELECT id FROM teachers WHERE COALESCE(is_active, 1) = 1 "
        "AND (name LIKE ? OR LOWER(email) = LOWER(?) OR (employee_number <> '' AND employee_number = ?)) "
        "ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END, id LIMIT ?",
        (like, needle, needle, needle, SEARCH_LIMIT + 1),
    ).fetchall()
    identities = [f"teacher:{int(row['id'])}" for row in teacher_rows]
    identities += [f"student:{int(row['id'])}" for row in student_rows]
    return public_users(conn, me, identities)[:SEARCH_LIMIT]


def _classmate_and_teacher_identities(conn: Any, user: dict) -> list[str]:
    role, pk = str(user.get("role") or ""), as_int(user.get("id"))
    if role == "teacher":
        rows = conn.execute(
            f"SELECT DISTINCT s.id FROM class_offerings o JOIN students s ON {offering_student_where()} "
            "WHERE o.teacher_id = ? AND COALESCE(s.enrollment_status, 'active') = 'active' ORDER BY s.id LIMIT 60",
            (pk,),
        ).fetchall()
        return [f"student:{int(row['id'])}" for row in rows]
    me_row = conn.execute("SELECT class_id FROM students WHERE id = ?", (pk,)).fetchone()
    class_id = as_int(me_row["class_id"]) if me_row else 0
    if class_id <= 0:
        return []
    classmates = conn.execute(
        "SELECT id FROM students WHERE class_id = ? AND id <> ? AND COALESCE(enrollment_status, 'active') = 'active' "
        "ORDER BY id LIMIT 60",
        (class_id, pk),
    ).fetchall()
    teachers = conn.execute(
        f"SELECT DISTINCT o.teacher_id FROM class_offerings o WHERE {student_offering_where()} LIMIT 20",
        (class_id, class_id),
    ).fetchall()
    return ([f"teacher:{int(row['teacher_id'])}" for row in teachers if row["teacher_id"]]
            + [f"student:{int(row['id'])}" for row in classmates])


def _online_wanderer_identities(conn: Any) -> list[str]:
    from .presence_service import simulated_wanderer_online
    from .wanderer_identity_service import STATUS_ACTIVE, list_wanderers

    ids = [int(item["student_id"]) for item in list_wanderers(conn, statuses=[STATUS_ACTIVE])]
    online = simulated_wanderer_online(conn, ids)
    return [f"student:{student_id}" for student_id in ids if student_id in online]


def suggest_users(conn: Any, user: dict) -> list[dict[str, Any]]:
    """同课堂同学、任课老师、在线散修 — minus me, friends, pending and blocked either way."""
    me = user_identity(user)
    candidates = _classmate_and_teacher_identities(conn, user) + _online_wanderer_identities(conn)
    hidden = blocked_by(conn, me) | blockers_of(conn, me)
    users = [item for item in public_users(conn, me, candidates)
             if item["relation"] == "none" and item["identity"] not in hidden]
    return users[:SUGGESTION_LIMIT]


# ---------------------------------------------------------------------------
# Profile sidebar
# ---------------------------------------------------------------------------

def _recent_posts(conn: Any, identity: str) -> list[dict[str, Any]]:
    from ..db.savepoint import best_effort
    from .blog_moderation_states import public_state_sql

    rows: list[Any] = []
    # Decorative: a failing blog query must neither fail the sidebar nor (PostgreSQL) abort the
    # surrounding transaction, hence the savepoint rather than a bare try/except.
    with best_effort(conn, "social_recent_posts", log_prefix="[SOCIAL]"):
        rows = conn.execute(
            "SELECT p.id, p.title, p.created_at FROM blog_posts p WHERE p.author_identity = ? "
            "AND p.status = 'published' AND p.visibility = 'public' AND p.author_display_mode = 'real_name' "
            f"AND {public_state_sql('p')} ORDER BY p.id DESC LIMIT ?",
            (identity, RECENT_POST_LIMIT),
        ).fetchall()
    return [{"kind": "blog", "title": str(row["title"] or ""), "url": f"/blog?post={int(row['id'])}",
             "at": str(row["created_at"] or "")} for row in rows]


def _has_classroom_private_messages(conn: Any, me: str, other: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM private_messages WHERE (sender_identity = ? AND recipient_identity = ?) "
        "OR (sender_identity = ? AND recipient_identity = ?) LIMIT 1",
        (me, other, other, me),
    ).fetchone()
    return row is not None


def _cultivation(person: dict[str, Any]) -> Optional[dict[str, Any]]:
    badge = person.get("tier_badge")
    if not badge:
        return None
    return {"tier_name": badge.get("name") or "", "score": badge.get("score"), "emblem": badge.get("emblem_url") or ""}


def profile_sidebar(conn: Any, user: dict, identity: Any) -> dict[str, Any]:
    me = user_identity(user)
    person = require_person(conn, identity)
    target = person["identity"]
    from .social_relations import presence_visible

    relation = relations_for(conn, me, [target]).get(target, "none") if target != me else "none"
    visible = target in presence_visible(conn, me, [target])
    presence = (presence_map(conn, [target]).get(target, {"online": False, "last_seen_at": None}) if visible
                else {"online": None, "last_seen_at": None})
    friendship = get_friendship(conn, me, target) if target != me else None
    is_friend = bool(friendship and friendship.get("status") == "accepted")
    payload = public_user(person, relation)
    payload.update({
        "mood": person["mood"] if visible else "",
        "online": bool(presence["online"]) if visible else None,
        "last_seen_at": presence["last_seen_at"],
        "remark": my_remark(friendship, me) if is_friend else "",
        "cultivation": _cultivation(person),
        "recent": _recent_posts(conn, target),
        "private_message_url": build_message_center_link(target)
        if target != me and _has_classroom_private_messages(conn, me, target) else None,
        "homepage_url": homepage_url(person),
    })
    return payload


def friend_people(conn: Any, me: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Accepted friendship rows and the people behind them."""
    rows = friendship_rows(conn, me)
    people = load_people(conn, [other_side(row, me) for row in rows])
    return rows, people
