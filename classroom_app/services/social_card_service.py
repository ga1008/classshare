"""个人信息面板 InfoCard (social doc §12.4, §12.6).

Public fields only: avatar + 境界 badge, name, role, school/college/class/
major/grade, the last 4 digits of the student number, courses with the
per-classroom 境界, the ability radar (students), mood/online under the
presence rules of §10.2. Never phone / QQ / WeChat / email / the full student
number / any learning-support or conversation content.

* Hidden cultivation: no badge, no per-course 境界, no radar, no breakthroughs.
* Teachers: college/department and the courses they teach.
* 散修: archetype and their homepage.
* Friends additionally get ``friend_extras``: recent real-name blog posts,
  achievements and breakthroughs, plus the real-name post list. Pen-name and
  anonymous posts never appear (they would de-anonymise the author).
* Someone I blocked gets a minimal card.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from .offering_membership_service import offering_student_where
from .social_common import as_int, in_clause, normalize_identity, split_identity, user_identity

RADAR_CACHE_SECONDS = 600.0
RADAR_CACHE_MAX = 2000
RECENT_LIMIT = 8
CREATION_LIMIT = 10

_RADAR_CACHE: dict[int, tuple[float, Optional[dict[str, Any]]]] = {}
_RADAR_LOCK = threading.Lock()

STUDENT_ORG_SQL = (
    "SELECT s.id, s.student_id_number, COALESCE(s.enrollment_status, 'active') AS status, "
    "COALESCE(NULLIF(TRIM(s.school_name), ''), NULLIF(TRIM(c.school_name), ''), '') AS school, "
    "COALESCE(NULLIF(TRIM(s.college), ''), NULLIF(TRIM(s.academic_college), ''), "
    "NULLIF(TRIM(c.academic_college), ''), NULLIF(TRIM(c.college), ''), '') AS college, "
    "COALESCE(c.name, '') AS class_name, "
    "COALESCE(NULLIF(TRIM(s.academic_major), ''), NULLIF(TRIM(c.academic_major), ''), "
    "NULLIF(TRIM(c.major), ''), '') AS major, "
    "COALESCE(NULLIF(TRIM(s.academic_grade), ''), NULLIF(TRIM(c.academic_grade), ''), "
    "CASE WHEN c.enrollment_year IS NULL THEN '' ELSE CAST(c.enrollment_year AS TEXT) END) AS grade "
    "FROM students s LEFT JOIN classes c ON c.id = s.class_id"
)
ORG_KEYS = ("school", "college", "class_name", "major", "grade")


def clear_radar_cache() -> None:
    with _RADAR_LOCK:
        _RADAR_CACHE.clear()


def student_org_rows(conn: Any, ids: list[int]) -> dict[int, dict[str, str]]:
    """Public organisation fields per student id (plus the number, for the 4-digit tail only)."""
    if not ids:
        return {}
    marks, params = in_clause(sorted(set(ids)))
    rows = conn.execute(f"{STUDENT_ORG_SQL} WHERE s.id IN ({marks})", params).fetchall()
    return {int(row["id"]): {key: str(row[key] or "") for key in ("student_id_number", "status", *ORG_KEYS)}
            for row in rows}


def _student_offerings(conn: Any, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT o.id AS offering_id, c.name AS course_name FROM students s "
        f"JOIN class_offerings o ON {offering_student_where()} JOIN courses c ON c.id = o.course_id "
        "WHERE s.id = ? ORDER BY o.id DESC",
        (int(student_id),),
    ).fetchall()
    return [{"offering_id": int(row["offering_id"]), "course_name": str(row["course_name"] or "")} for row in rows]


def _offering_tiers(conn: Any, student_id: int) -> dict[int, int]:
    rows = conn.execute("SELECT class_offering_id, MAX(tier) AS tier FROM learning_certificates "
                        "WHERE student_id = ? GROUP BY class_offering_id", (int(student_id),)).fetchall()
    return {int(row["class_offering_id"]): as_int(row["tier"]) for row in rows}


def _level(tier: int) -> Optional[dict[str, Any]]:
    from .learning_progress_service import LEARNING_LEVELS

    return next((item for item in LEARNING_LEVELS if int(item["tier"]) == int(tier)), None)


def _courses(conn: Any, student_id: int, *, hidden: bool) -> tuple[list[dict[str, Any]], list[int]]:
    from .cultivation_display_service import emblem_for_level
    from .social_scope_service import tier_name

    offerings = _student_offerings(conn, student_id)
    tiers = {} if hidden else _offering_tiers(conn, student_id)
    courses = []
    for item in offerings:
        tier = tiers.get(item["offering_id"], 0)
        emblem = None if hidden else emblem_for_level(_level(tier))
        courses.append({"course_name": item["course_name"],
                        "tier_name": None if hidden else tier_name(tier),
                        "emblem": (emblem or {}).get("emblem_url") or None})
    return courses, [item["offering_id"] for item in offerings]


def _radar(conn: Any, student_id: int, offering_ids: list[int]) -> Optional[dict[str, Any]]:
    from ..db.savepoint import best_effort
    from .ability_radar_service import build_ability_radar

    if not offering_ids:
        return None
    now = time.monotonic()
    with _RADAR_LOCK:
        cached = _RADAR_CACHE.get(int(student_id))
        if cached and cached[0] > now:
            return cached[1]
    radar: Optional[dict[str, Any]] = None
    with best_effort(conn, "social_card_radar", log_prefix="[SOCIAL]"):
        radar = build_ability_radar(conn, student_id=int(student_id), offering_ids=offering_ids)
    with _RADAR_LOCK:
        if len(_RADAR_CACHE) >= RADAR_CACHE_MAX:
            _RADAR_CACHE.clear()
        _RADAR_CACHE[int(student_id)] = (now + RADAR_CACHE_SECONDS, radar)
    return radar


def _teacher_courses(conn: Any, teacher_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT DISTINCT c.name FROM class_offerings o JOIN courses c ON c.id = o.course_id "
                        "WHERE o.teacher_id = ? ORDER BY c.name", (int(teacher_id),)).fetchall()
    return [{"course_name": str(row["name"] or ""), "tier_name": None, "emblem": None} for row in rows]


def _teacher_org(conn: Any, teacher_id: int) -> dict[str, str]:
    row = conn.execute("SELECT school_name, college, department FROM teachers WHERE id = ?",
                       (int(teacher_id),)).fetchone()
    data = {key: str((row[key] if row else "") or "") for key in ("school_name", "college", "department")}
    return {"school": data["school_name"], "college": data["college"], "department": data["department"],
            "class_name": "", "major": "", "grade": ""}


# ---------------------------------------------------------------------------
# Friend extras (real-name content only)
# ---------------------------------------------------------------------------

def _real_name_posts(conn: Any, identity: str, limit: int) -> list[Any]:
    from ..db.savepoint import best_effort
    from .blog_moderation_states import public_state_sql

    rows: list[Any] = []
    with best_effort(conn, "social_card_posts", log_prefix="[SOCIAL]"):
        rows = conn.execute(
            "SELECT p.id, p.title, p.created_at, COALESCE(p.like_count, 0) AS likes, "
            "COALESCE(p.comment_count, 0) AS comments FROM blog_posts p WHERE p.author_identity = ? "
            "AND p.status = 'published' AND p.visibility = 'public' AND p.author_display_mode = 'real_name' "
            f"AND {public_state_sql('p')} ORDER BY p.id DESC LIMIT ?",
            (identity, int(limit)),
        ).fetchall()
    return rows


def _achievements(conn: Any, student_id: int) -> list[dict[str, Any]]:
    from ..db.savepoint import best_effort
    from .student_achievement_service import BADGES

    names = {badge["key"]: badge["name"] for badge in BADGES}
    rows: list[Any] = []
    with best_effort(conn, "social_card_achievements", log_prefix="[SOCIAL]"):
        rows = conn.execute("SELECT badge_key, earned_at FROM student_achievements WHERE student_id = ? "
                            "ORDER BY earned_at DESC LIMIT ?", (int(student_id), RECENT_LIMIT)).fetchall()
    return [{"kind": "achievement", "title": f"获得成就「{names.get(str(row['badge_key']), '成就')}」",
             "url": None, "at": str(row["earned_at"] or "")} for row in rows]


def _breakthroughs(conn: Any, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT level_name, issued_at FROM learning_certificates WHERE student_id = ? "
                        "ORDER BY issued_at DESC LIMIT ?", (int(student_id), RECENT_LIMIT)).fetchall()
    return [{"kind": "breakthrough", "title": f"突破至「{row['level_name']}」", "url": None,
             "at": str(row["issued_at"] or "")} for row in rows]


def _friend_extras(conn: Any, identity: str, *, is_student: bool, hidden: bool) -> dict[str, Any]:
    posts = _real_name_posts(conn, identity, CREATION_LIMIT)
    recent = [{"kind": "blog", "title": str(row["title"] or ""), "url": f"/blog?post={int(row['id'])}",
               "at": str(row["created_at"] or "")} for row in posts[:RECENT_LIMIT]]
    _role, pk = split_identity(identity)
    if is_student:
        recent += _achievements(conn, pk)
        if not hidden:
            recent += _breakthroughs(conn, pk)
    recent.sort(key=lambda item: item["at"], reverse=True)
    creations = [{"title": str(row["title"] or ""), "url": f"/blog?post={int(row['id'])}",
                  "at": str(row["created_at"] or ""), "likes": as_int(row["likes"]),
                  "comments": as_int(row["comments"])} for row in posts]
    return {"recent": recent[:RECENT_LIMIT], "creations": creations}


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def _empty_card(person: dict[str, Any], relation: str) -> dict[str, Any]:
    from .social_identity_service import avatar_url

    return {
        "identity": person["identity"], "name": person["name"], "role": person["role"],
        "role_label": person["role_label"], "avatar_url": avatar_url(person["identity"]),
        "is_wanderer": bool(person["is_wanderer"]), "tier_badge": None, "archetype": None, "homepage_url": None,
        "org": {key: "" for key in ORG_KEYS},
        "student_no_tail": None, "mood": None, "courses": [], "radar": None, "cultivation_hidden": False,
        "relation": relation, "can_add": False, "add_blocked_reason": None, "online": None, "friend_extras": None,
    }


def _wanderer_archetype(conn: Any, student_id: int) -> Optional[str]:
    from .wanderer_identity_service import load_wanderer_by_student_id

    wanderer = load_wanderer_by_student_id(conn, student_id) or {}
    return str(wanderer.get("archetype") or "") or None


def user_card(conn: Any, user: dict, identity: Any) -> dict[str, Any]:
    """``GET /api/social/users/{identity}/card`` -> InfoCard."""
    from .cultivation_display_service import is_cultivation_hidden
    from .presence_service import presence_map
    from .social_identity_service import add_capability, homepage_url, require_person
    from .social_relations import presence_visible, relations_for

    me = user_identity(user)
    person = require_person(conn, normalize_identity(identity))
    target = person["identity"]
    relation = "self" if target == me else relations_for(conn, me, [target]).get(target, "none")
    card = _empty_card(person, relation)
    if relation == "blocked":
        return card
    role, pk = split_identity(target)
    if relation != "self":
        probe = add_capability(conn, me, [{"identity": target, "relation": relation}])[0]
        card["can_add"], card["add_blocked_reason"] = probe["can_add"], probe["add_blocked_reason"]
    if target in presence_visible(conn, me, [target]):
        state = presence_map(conn, [target]).get(target) or {}
        card["online"], card["mood"] = bool(state.get("online")), (person["mood"] or None)
    hidden = role == "student" and not person["is_wanderer"] and is_cultivation_hidden(conn, pk)
    card["cultivation_hidden"] = bool(hidden)
    card["tier_badge"] = None if hidden else person["tier_badge"]
    if role == "teacher":
        card["org"] = _teacher_org(conn, pk)
        card["courses"] = _teacher_courses(conn, pk)
    elif person["is_wanderer"]:
        card["archetype"] = _wanderer_archetype(conn, pk)
        card["homepage_url"] = homepage_url(person)
    else:
        org = student_org_rows(conn, [pk]).get(pk) or {}
        card["org"] = {key: org.get(key, "") for key in ORG_KEYS}
        number = "".join(ch for ch in str(org.get("student_id_number") or "") if ch.isalnum())
        card["student_no_tail"] = number[-4:] if len(number) >= 4 else None
        card["courses"], offering_ids = _courses(conn, pk, hidden=hidden)
        card["radar"] = None if hidden else _radar(conn, pk, offering_ids)
    if relation == "friend":
        is_student = role == "student" and not person["is_wanderer"]
        card["friend_extras"] = _friend_extras(conn, target, is_student=is_student, hidden=hidden)
    return card
