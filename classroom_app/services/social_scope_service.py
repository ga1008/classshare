"""境界门槛：谁可以向谁发起好友申请 (social doc §12.1, §12.5).

Only *sending* a request is gated; receiving, accepting, declining and blocking
are never restricted, and a request the target already sent me can always be
answered (``send_request`` skips the check for that case).

A student's scope tier is the highest certificate tier across the classrooms
they currently belong to (the same "current level" the global cultivation
profile shows). Teachers are unrestricted. 散修 keep their own tier but their
outgoing requests follow the 散修 rules (``wanderer_social_friends``), so the
check passes them through.

Ladder (cumulative):

=====  ==========================================================
tier   may send requests to
=====  ==========================================================
0      nobody (can still be added and can block)
1      same administrative class (``students.class_id``), teachers of my school
2      students whose classroom course *name* matches one of mine (normalised)
3      same college (of the same school), 散修
4      same school (``school_code``)
≥5     any registered user; unlocks AI 找人
=====  ==========================================================

Two equivalent forms of the rule live here and must stay in step:
``required_tier`` (Python, per target, used for reasons and single checks) and
``student_scope_sql`` / ``teacher_scope_sql`` (SQL candidate filters for search
and candidate pools). ``tests/test_social_scope.py`` checks both.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .offering_membership_service import offering_student_where
from .social_common import as_int, ensure_schema, in_clause, normalize_identity, now_iso, split_identity

MAX_SCOPE_TIER = 5
AI_SEARCH_TIER = 5
WANDERER_SCOPE_TIER = 3

LADDER_LABELS: dict[int, str] = {
    0: "可以接受好友申请，暂不能主动添加",
    1: "本班同学与本校教师",
    2: "同课程同学",
    3: "本学院同学与散修",
    4: "本校同学",
    5: "平台任意用户，并解锁「AI 找人」",
}
SCOPE_LINK = "/profile?section=notifications"

# SQL expressions over ``students s LEFT JOIN classes c ON c.id = s.class_id``.
COLLEGE_SQL = ("COALESCE(NULLIF(TRIM(s.college), ''), NULLIF(TRIM(s.academic_college), ''), "
               "NULLIF(TRIM(c.academic_college), ''), NULLIF(TRIM(c.college), ''), '')")
SCHOOL_SQL = "LOWER(COALESCE(NULLIF(TRIM(s.school_code), ''), NULLIF(TRIM(c.school_code), ''), ''))"
TEACHER_SCHOOL_SQL = "LOWER(COALESCE(NULLIF(TRIM(t.school_code), ''), ''))"


# ---------------------------------------------------------------------------
# Course-name normalisation (同名课程组)
# ---------------------------------------------------------------------------

_CJK = "一-鿿"
_BRACKET_SUFFIX = re.compile(r"[(\[【]\s*(上|中|下|[一二三四五六]|[ivx]+|\d{1,2}|[ab])\s*册?\s*[)\]】]$")
_VOLUME_SUFFIX = re.compile(rf"(?<=[{_CJK}])(上|中|下)册?$")
_ROMAN_SUFFIX = re.compile(rf"(?<=[{_CJK}])(iv|v|i{{1,3}})$")
_NUMBER_SUFFIX = re.compile(rf"(?<=[{_CJK}])[1-6一二三四五六]$")


def normalize_course_name(name: Any) -> str:
    """Comparable course name: NFKC (full/half width, Ⅱ → II), no spaces, lower case,
    and version suffixes such as （上）（下）(I)(II)、上/下册、尾随 1/2 removed."""
    text = unicodedata.normalize("NFKC", str(name or "")).lower()
    text = re.sub(r"\s+", "", text)
    for _ in range(3):  # 「大学英语（二）（上册）」 style double suffixes
        previous = text
        text = _BRACKET_SUFFIX.sub("", text)
        text = _VOLUME_SUFFIX.sub("", text)
        text = _ROMAN_SUFFIX.sub("", text)
        text = _NUMBER_SUFFIX.sub("", text)
        if text == previous:
            break
    return text.strip("-_·.")


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------

def tier_name(tier: Any) -> str:
    from .learning_progress_service import LEARNING_LEVELS, STARTER_LEVEL

    value = as_int(tier)
    for level in LEARNING_LEVELS:
        if int(level["tier"]) == value:
            return str(level["name"])
    return str(STARTER_LEVEL["name"])


def unlock_hint(required: int) -> str:
    return f"突破至「{tier_name(required)}」后可添加"


def student_global_tier(conn: Any, student_id: int, *, exclude_certificate_id: Optional[int] = None) -> int:
    """Highest certificate tier over the classrooms the student currently belongs to."""
    params: list[Any] = [int(student_id)]
    extra = ""
    if exclude_certificate_id:
        extra = " AND lc.id <> ?"
        params.append(int(exclude_certificate_id))
    row = conn.execute(
        "SELECT MAX(lc.tier) AS tier FROM learning_certificates lc "
        "JOIN class_offerings o ON o.id = lc.class_offering_id JOIN students s ON s.id = lc.student_id "
        "WHERE lc.student_id = ? AND COALESCE(s.enrollment_status, 'active') = 'active' "
        f"AND {offering_student_where()}{extra}",
        tuple(params),
    ).fetchone()
    return max(0, as_int(row["tier"] if row else 0))


# ---------------------------------------------------------------------------
# Requester context and target facts
# ---------------------------------------------------------------------------

@dataclass
class ScopeContext:
    identity: str
    role: str
    pk: int
    tier: int = 0
    is_teacher: bool = False
    is_wanderer: bool = False
    class_id: int = 0
    college: str = ""
    school_code: str = ""
    course_keys: frozenset = field(default_factory=frozenset)

    @property
    def unrestricted(self) -> bool:
        """Teachers, 散修 (their own rules apply elsewhere) and tier ≥ 5 reach everyone."""
        return self.is_teacher or self.is_wanderer or self.tier >= MAX_SCOPE_TIER

    @property
    def can_add_anyone(self) -> bool:
        return self.unrestricted or self.tier >= 1

    @property
    def ai_search(self) -> bool:
        return self.is_teacher or (not self.is_wanderer and self.tier >= AI_SEARCH_TIER)


def _student_rows(conn: Any, ids: list[int]) -> list[Any]:
    if not ids:
        return []
    marks, params = in_clause(ids)
    return conn.execute(
        f"SELECT s.id, s.class_id, COALESCE(s.enrollment_status, 'active') AS status, "
        f"{COLLEGE_SQL} AS college, {SCHOOL_SQL} AS school_code "
        f"FROM students s LEFT JOIN classes c ON c.id = s.class_id WHERE s.id IN ({marks})",
        params,
    ).fetchall()


def student_course_names(conn: Any, ids: Iterable[int]) -> dict[int, list[str]]:
    """``{student_id: [course name, ...]}`` over the classrooms each student belongs to."""
    wanted = sorted({int(item) for item in ids if as_int(item) > 0})
    if not wanted:
        return {}
    marks, params = in_clause(wanted)
    rows = conn.execute(
        f"SELECT DISTINCT s.id AS sid, c.name AS course_name FROM students s "
        f"JOIN class_offerings o ON {offering_student_where()} JOIN courses c ON c.id = o.course_id "
        f"WHERE s.id IN ({marks})",
        params,
    ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(int(row["sid"]), []).append(str(row["course_name"] or ""))
    return result


def _course_keys(names: Iterable[str]) -> frozenset:
    return frozenset(key for key in (normalize_course_name(name) for name in names) if key)


def scope_context(conn: Any, who: Any) -> ScopeContext:
    """Context for a user dict (``{'role','id'}``) or a ``role:pk`` identity."""
    if isinstance(who, ScopeContext):
        return who
    if isinstance(who, dict):
        role, pk = str(who.get("role") or ""), as_int(who.get("id"))
    else:
        role, pk = split_identity(str(who or ""))
    identity = f"{role}:{pk}"
    if role == "teacher":
        return ScopeContext(identity=identity, role=role, pk=pk, tier=MAX_SCOPE_TIER, is_teacher=True)
    if role != "student" or pk <= 0:
        return ScopeContext(identity=identity, role=role, pk=pk)
    rows = _student_rows(conn, [pk])
    if not rows:
        return ScopeContext(identity=identity, role=role, pk=pk)
    row = rows[0]
    if str(row["status"]) == "wanderer":
        from .wanderer_identity_service import load_wanderers_by_student_ids

        wanderer = load_wanderers_by_student_ids(conn, [pk]).get(pk) or {}
        return ScopeContext(identity=identity, role=role, pk=pk, tier=as_int(wanderer.get("tier")), is_wanderer=True)
    return ScopeContext(
        identity=identity, role=role, pk=pk, tier=student_global_tier(conn, pk),
        class_id=as_int(row["class_id"]), college=str(row["college"] or ""),
        school_code=str(row["school_code"] or ""),
        course_keys=_course_keys(student_course_names(conn, [pk]).get(pk, [])),
    )


def target_facts(conn: Any, identities: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Scope-relevant facts per target identity (students incl. 散修, teachers)."""
    students, teachers = [], []
    for identity in {str(item) for item in identities if item}:
        role, pk = split_identity(identity)
        if pk > 0 and role == "student":
            students.append(pk)
        elif pk > 0 and role == "teacher":
            teachers.append(pk)
    facts: dict[str, dict[str, Any]] = {}
    courses = student_course_names(conn, students)
    for row in _student_rows(conn, sorted(students)):
        pk = int(row["id"])
        facts[f"student:{pk}"] = {
            "kind": "student", "is_wanderer": str(row["status"]) == "wanderer",
            "class_id": as_int(row["class_id"]), "college": str(row["college"] or ""),
            "school_code": str(row["school_code"] or ""), "course_keys": _course_keys(courses.get(pk, [])),
        }
    if teachers:
        marks, params = in_clause(sorted(teachers))
        for row in conn.execute(f"SELECT t.id, {TEACHER_SCHOOL_SQL} AS school_code FROM teachers t "
                                f"WHERE t.id IN ({marks})", params).fetchall():
            facts[f"teacher:{int(row['id'])}"] = {"kind": "teacher", "school_code": str(row["school_code"] or "")}
    return facts


def required_tier(ctx: ScopeContext, facts: dict[str, Any]) -> int:
    """Lowest requester tier at which ``ctx`` may send a request to the target described by ``facts``."""
    if facts.get("kind") == "teacher":
        return 1 if ctx.school_code and facts.get("school_code") == ctx.school_code else MAX_SCOPE_TIER
    if facts.get("is_wanderer"):
        return WANDERER_SCOPE_TIER
    if ctx.class_id and facts.get("class_id") == ctx.class_id:
        return 1
    if ctx.course_keys & facts.get("course_keys", frozenset()):
        return 2
    same_school = bool(ctx.school_code) and facts.get("school_code") == ctx.school_code
    if ctx.college and facts.get("college") == ctx.college and same_school:  # 学院 names repeat across schools
        return 3
    if same_school:
        return 4
    return MAX_SCOPE_TIER


def evaluate_targets(conn: Any, ctx: ScopeContext, identities: Iterable[str]) -> dict[str, tuple[bool, str]]:
    """``{identity: (in_scope, add_blocked_reason)}``; reason is '' when in scope."""
    wanted = [item for item in dict.fromkeys(str(value) for value in identities if value) if item != ctx.identity]
    if not wanted:
        return {}
    if ctx.unrestricted:
        return {identity: (True, "") for identity in wanted}
    facts = target_facts(conn, wanted)
    result: dict[str, tuple[bool, str]] = {}
    for identity in wanted:
        needed = max(1, required_tier(ctx, facts[identity]) if identity in facts else MAX_SCOPE_TIER)
        allowed = ctx.tier >= needed
        result[identity] = (allowed, "" if allowed else unlock_hint(needed))
    return result


def can_add(conn: Any, requester: Any, target: Any) -> tuple[bool, str]:
    """May ``requester`` (user dict, identity or context) send a friend request to ``target``? -> (ok, reason)."""
    ctx = scope_context(conn, requester)
    target_identity = normalize_identity(target)
    if target_identity == ctx.identity:
        return False, ""
    return evaluate_targets(conn, ctx, [target_identity]).get(target_identity, (False, unlock_hint(MAX_SCOPE_TIER)))


# ---------------------------------------------------------------------------
# SQL candidate filters
# ---------------------------------------------------------------------------

def course_group_offering_ids(conn: Any, course_keys: Iterable[str]) -> list[int]:
    """Offerings whose course name normalises to one of ``course_keys``."""
    keys = set(course_keys)
    if not keys:
        return []
    rows = conn.execute("SELECT o.id, c.name FROM class_offerings o JOIN courses c ON c.id = o.course_id").fetchall()
    return sorted(int(row["id"]) for row in rows if normalize_course_name(row["name"]) in keys)


def student_scope_sql(conn: Any, ctx: ScopeContext) -> tuple[str, tuple]:
    """WHERE fragment over ``students s LEFT JOIN classes c`` limiting rows to my scope (active or 散修)."""
    if ctx.unrestricted:
        return "COALESCE(s.enrollment_status, 'active') IN ('active', 'wanderer')", ()
    inner: list[str] = []
    params: list[Any] = []
    if ctx.tier >= 1 and ctx.class_id:
        inner.append("s.class_id = ?")
        params.append(ctx.class_id)
    if ctx.tier >= 2:
        offering_ids = course_group_offering_ids(conn, ctx.course_keys)
        if offering_ids:
            marks, values = in_clause(offering_ids)
            inner.append(f"EXISTS (SELECT 1 FROM class_offerings og WHERE og.id IN ({marks}) "
                         f"AND {offering_student_where(offering_alias='og', student_alias='s')})")
            params.extend(values)
    if ctx.tier >= 3 and ctx.college and ctx.school_code:
        inner.append(f"({COLLEGE_SQL} = ? AND {SCHOOL_SQL} = ?)")
        params.extend((ctx.college, ctx.school_code))
    if ctx.tier >= 4 and ctx.school_code:
        inner.append(f"{SCHOOL_SQL} = ?")
        params.append(ctx.school_code)
    parts: list[str] = []
    if inner:
        parts.append(f"(COALESCE(s.enrollment_status, 'active') = 'active' AND ({' OR '.join(inner)}))")
    if ctx.tier >= WANDERER_SCOPE_TIER:
        parts.append("s.enrollment_status = 'wanderer'")
    return (f"({' OR '.join(parts)})" if parts else "1 = 0"), tuple(params)


def teacher_scope_sql(ctx: ScopeContext) -> tuple[str, tuple]:
    """WHERE fragment over ``teachers t`` limiting rows to my scope."""
    if ctx.unrestricted:
        return "1 = 1", ()
    if ctx.tier >= 1 and ctx.school_code:
        return f"{TEACHER_SCHOOL_SQL} = ?", (ctx.school_code,)
    return "1 = 0", ()


# ---------------------------------------------------------------------------
# GET /api/social/scope
# ---------------------------------------------------------------------------

def _ladder(tier: int, *, teacher: bool) -> list[dict[str, Any]]:
    return [{"tier": level, "tier_name": tier_name(level), "label": LADDER_LABELS[level],
             "unlocked": teacher or tier >= level} for level in range(0, MAX_SCOPE_TIER + 1)]


def scope_payload(conn: Any, user: dict) -> dict[str, Any]:
    ctx = scope_context(conn, user)
    if ctx.role == "student" and not ctx.is_wanderer and ctx.pk > 0:
        check_scope_unlock(conn, ctx.pk, tier=ctx.tier)
    tier = MAX_SCOPE_TIER if ctx.is_teacher else ctx.tier
    next_tier = None if ctx.is_teacher or ctx.tier >= MAX_SCOPE_TIER else ctx.tier + 1
    return {
        "tier": tier,
        "tier_name": "教师" if ctx.is_teacher else tier_name(ctx.tier),
        "is_teacher": ctx.is_teacher,
        "can_add": ctx.can_add_anyone,
        "ai_search": ctx.ai_search,
        "ladder": _ladder(tier, teacher=ctx.is_teacher),
        "next_unlock": ({"tier": next_tier, "tier_name": tier_name(next_tier), "label": LADDER_LABELS[next_tier]}
                        if next_tier is not None else None),
    }


def locked_hint() -> str:
    return f"突破第一境界「{tier_name(1)}」后即可添加本班同学与本校教师"


# ---------------------------------------------------------------------------
# Tier-up notification (§12.5): one message per newly unlocked scope, deduped.
# ---------------------------------------------------------------------------

def check_scope_unlock(conn: Any, student_id: int, *, tier: Optional[int] = None,
                       previous_tier: Optional[int] = None) -> bool:
    """Notify once when the student's scope tier rose above the last one recorded.

    The first observation of a student only records a baseline (``previous_tier``
    when the caller knows it, else the current tier) so rolling the feature out
    does not message everyone. ``ref_id`` is per (student, tier), so a repeat is
    de-duplicated by the message center as well.
    """
    from ..db.savepoint import best_effort

    ensure_schema(conn)
    identity = f"student:{int(student_id)}"
    current = student_global_tier(conn, student_id) if tier is None else int(tier)
    row = conn.execute("SELECT notified_tier FROM social_scope_state WHERE identity = ?", (identity,)).fetchone()
    if row is None:
        baseline = current if previous_tier is None else min(int(previous_tier), current)
        conn.execute("INSERT INTO social_scope_state (identity, notified_tier, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (identity) DO NOTHING", (identity, baseline, now_iso()))
        row = conn.execute("SELECT notified_tier FROM social_scope_state WHERE identity = ?", (identity,)).fetchone()
    notified = as_int(row["notified_tier"]) if row else current
    if current <= notified:
        return False
    conn.execute("UPDATE social_scope_state SET notified_tier = ?, updated_at = ? WHERE identity = ?",
                 (current, now_iso(), identity))
    if min(current, MAX_SCOPE_TIER) <= min(notified, MAX_SCOPE_TIER):
        return False  # above tier 5 nothing new unlocks
    labels = [LADDER_LABELS[level] for level in range(max(1, notified + 1), min(current, MAX_SCOPE_TIER) + 1)]
    sent = False
    with best_effort(conn, "social_scope_notify", log_prefix="[SOCIAL]"):
        from .message_center_service import create_social_notification

        sent = bool(create_social_notification(
            conn, recipient_identity=identity, title=f"新的交友范围已解锁：{'、'.join(labels)}",
            body_preview=f"你已达到「{tier_name(current)}」，可以在好友「添加」页向新的范围发起申请。",
            link_url=SCOPE_LINK, ref_id=f"social_scope_unlock:{identity}:{min(current, MAX_SCOPE_TIER)}",
        ))
    return sent


def on_certificate_issued(conn: Any, student_id: int, certificate_id: Optional[int]) -> None:
    """Hook called right after a learning certificate row is inserted (best effort, own savepoint)."""
    from ..db.savepoint import best_effort

    with best_effort(conn, "social_scope_unlock", log_prefix="[SOCIAL]"):
        previous = student_global_tier(conn, student_id, exclude_certificate_id=certificate_id)
        check_scope_unlock(conn, student_id, previous_tier=previous)
