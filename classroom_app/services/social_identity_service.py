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
        f"SELECT s.id, s.name, s.gender, s.today_mood, s.enrollment_status, s.college, c.name AS class_name "
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
            "gender": str(row["gender"] or ""),
            "role_label": "散修" if wanderer else "学生",
            "org_label": "散修·无宗门" if wanderer else str(row["class_name"] or row["college"] or ""),
            "is_wanderer": bool(wanderer),
            "wanderer_slug": str(wanderer.get("slug") or ""),
            "cultivation_score": float(wanderer.get("cultivation_score") or 0),
            "wanderer_tier": int(wanderer.get("tier") or 0),
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
        "gender": person.get("gender") or "",
        "role_label": person["role_label"],
        "org_label": person["org_label"],
        "avatar_url": avatar_url(person["identity"]),
        "is_wanderer": bool(person["is_wanderer"]),
        "tier_badge": person["tier_badge"],
        "relation": relation,
    }


def add_capability(conn: Any, me: Any, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Set ``can_add`` / ``add_blocked_reason`` (§12.6) on PublicUser dicts from ``me``'s point of view.

    ``can_add`` means "the add button would work": relation ``none`` and in my
    境界 scope, or ``pending_in`` (answering is never restricted). Being blocked
    by the other side is never revealed here; the request itself is refused.
    """
    from .social_scope_service import evaluate_targets, scope_context

    if not users:
        return users
    pending = [item["identity"] for item in users if item.get("relation") == "none"]
    ctx = scope_context(conn, me) if me and pending else None
    verdicts = evaluate_targets(conn, ctx, pending) if ctx is not None else {}
    for item in users:
        relation = item.get("relation")
        if relation == "pending_in":
            item["can_add"], item["add_blocked_reason"] = True, None
        elif relation == "none" and ctx is not None:
            allowed, reason = verdicts.get(item["identity"], (False, ""))
            item["can_add"], item["add_blocked_reason"] = bool(allowed), (reason or None)
        else:
            item["can_add"], item["add_blocked_reason"] = False, None
    return users


def public_users(conn: Any, me: str, identities: Iterable[str]) -> list[dict[str, Any]]:
    ordered = [item for item in dict.fromkeys(str(value) for value in identities) if item and item != me]
    people = load_people(conn, ordered)
    relations = relations_for(conn, me, ordered)
    users = [public_user(people[item], relations.get(item, "none")) for item in ordered
             if item in people and people[item]["active"]]
    return add_capability(conn, me, users)


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
    """Name, 学号, 工号, email or 散修 name within my 境界 scope (§12.1); at most 20; public fields only."""
    from .social_scope_service import scope_context, student_scope_sql, teacher_scope_sql

    me = user_identity(user)
    needle = _clean_query(query)
    ctx = scope_context(conn, user)
    if not needle or not ctx.can_add_anyone:
        return []
    like = f"%{needle}%"
    student_where, student_params = student_scope_sql(conn, ctx)
    student_rows = conn.execute(
        "SELECT s.id FROM students s LEFT JOIN classes c ON c.id = s.class_id "
        "WHERE COALESCE(s.enrollment_status, 'active') IN (?, ?) "
        "AND (s.name LIKE ? OR s.student_id_number = ? OR LOWER(COALESCE(s.email, '')) = LOWER(?)) "
        f"AND {student_where} "
        "ORDER BY CASE WHEN s.name = ? THEN 0 ELSE 1 END, s.id LIMIT ?",
        (*ACTIVE_STUDENT_STATUSES, like, needle, needle, *student_params, needle, SEARCH_LIMIT + 1),
    ).fetchall()
    teacher_where, teacher_params = teacher_scope_sql(ctx)
    teacher_rows = conn.execute(
        "SELECT t.id FROM teachers t WHERE COALESCE(t.is_active, 1) = 1 "
        "AND (t.name LIKE ? OR LOWER(t.email) = LOWER(?) OR (t.employee_number <> '' AND t.employee_number = ?)) "
        f"AND {teacher_where} "
        "ORDER BY CASE WHEN t.name = ? THEN 0 ELSE 1 END, t.id LIMIT ?",
        (like, needle, needle, *teacher_params, needle, SEARCH_LIMIT + 1),
    ).fetchall()
    identities = [f"teacher:{int(row['id'])}" for row in teacher_rows]
    identities += [f"student:{int(row['id'])}" for row in student_rows]
    return public_users(conn, me, identities)[:SEARCH_LIMIT]


def search_payload(conn: Any, user: dict, query: Any) -> dict[str, Any]:
    """``GET /users/search`` (§12.6): ``{items, locked, locked_hint}``; 未入道 students are locked."""
    from .social_scope_service import locked_hint, scope_context

    ctx = scope_context(conn, user)
    if not ctx.can_add_anyone:
        return {"items": [], "locked": True, "locked_hint": locked_hint()}
    return {"items": search_users(conn, user, query), "locked": False, "locked_hint": ""}


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


def fallback_suggestions(conn: Any, user: dict) -> list[dict[str, Any]]:
    """同课堂同学、任课老师、在线散修 within my scope — used until the daily recommendations exist."""
    me = user_identity(user)
    candidates = _classmate_and_teacher_identities(conn, user) + _online_wanderer_identities(conn)
    hidden = blocked_by(conn, me) | blockers_of(conn, me)
    users = [item for item in public_users(conn, me, candidates)
             if item["relation"] == "none" and item["identity"] not in hidden and item.get("can_add")]
    return users[:SUGGESTION_LIMIT]


def suggest_users(conn: Any, user: dict) -> list[dict[str, Any]]:
    """Legacy ``/users/suggestions``: complement + similar recommendations (deduped), else the fallback."""
    from .social_recommendation_service import recommendations_payload

    payload = recommendations_payload(conn, user)
    if payload["locked"]:
        return []
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for item in payload["complement"] + payload["similar"]:
        if item["identity"] not in seen:
            seen.add(item["identity"])
            items.append({key: value for key, value in item.items() if key not in {"match", "hint"}})
    return items or fallback_suggestions(conn, user)


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
    if target != me:
        add_capability(conn, me, [payload])
    else:
        payload.update({"can_add": False, "add_blocked_reason": None})
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
