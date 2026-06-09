"""Unified material attribution + openness (归属 + 开放范围) core.

Every kind of teacher-facing material (公文 / 教材 / 课程材料 / 试卷 …) shares the
same two orthogonal properties:

* **归属 (attribution)** — which org unit a material belongs to: 学校 → 学院 → 系部.
  The deepest non-empty unit is the ``attr_level``.
* **开放范围 (openness)** — how far *up* the hierarchy it is visible, starting from
  its attribution level: 系部归属 ⇒ {本系部, 本院级, 本校, 完全公开};
  院级归属 ⇒ {本院级, 本校, 完全公开}; 校级归属 ⇒ {本校, 完全公开}.

A material table adopts the standard columns
``attr_school_code, attr_college, attr_department, attr_level, openness`` and then
reuses every function here — normalization, the visibility predicate, the
engine-agnostic SQL filter, the UI option lists and labels. Built on top of
``organization_scope_service`` (teacher org resolution + normalizers).
"""

from __future__ import annotations

from typing import Any

from .department_service import normalize_department
from .organization_scope_service import (
    normalize_college,
    normalize_school_code,
)

# Attribution / openness levels, ordered from narrowest to broadest.
LEVEL_DEPARTMENT = "department"
LEVEL_COLLEGE = "college"
LEVEL_SCHOOL = "school"
LEVEL_PUBLIC = "public"

LEVEL_ORDER = {LEVEL_DEPARTMENT: 0, LEVEL_COLLEGE: 1, LEVEL_SCHOOL: 2, LEVEL_PUBLIC: 3}

# Label of an *attribution* level (what the material belongs to).
ATTR_LABELS = {LEVEL_DEPARTMENT: "系部", LEVEL_COLLEGE: "学院", LEVEL_SCHOOL: "学校"}
# Label of an *openness* level (who can see it).
OPENNESS_LABELS = {
    LEVEL_DEPARTMENT: "本系部可见",
    LEVEL_COLLEGE: "本院级可见",
    LEVEL_SCHOOL: "本校可见",
    LEVEL_PUBLIC: "完全公开",
}

# Standard column names a material table exposes for this core.
DEFAULT_COLUMNS = {
    "school": "attr_school_code",
    "college": "attr_college",
    "department": "attr_department",
    "level": "attr_level",
    "openness": "openness",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def attribution_level(school_code: Any, college: Any, department: Any) -> str:
    """Deepest non-empty unit = attribution level (school is always implied)."""
    if normalize_department(department):
        return LEVEL_DEPARTMENT
    if normalize_college(college):
        return LEVEL_COLLEGE
    return LEVEL_SCHOOL


def openness_levels_for(attr_level: str) -> list[str]:
    """Valid openness levels for an attribution level — its level and up."""
    floor = LEVEL_ORDER.get(attr_level, LEVEL_ORDER[LEVEL_SCHOOL])
    return [lvl for lvl, rank in sorted(LEVEL_ORDER.items(), key=lambda kv: kv[1]) if rank >= floor]


def openness_options(attr_level: str) -> list[dict[str, str]]:
    """UI options [{value, label}] for the openness select given attribution."""
    return [{"value": lvl, "label": OPENNESS_LABELS[lvl]} for lvl in openness_levels_for(attr_level)]


def clamp_openness(openness: Any, attr_level: str) -> str:
    """Openness can never be narrower than the attribution level."""
    candidate = _norm(openness).lower()
    valid = openness_levels_for(attr_level)
    if candidate in valid:
        return candidate
    return attr_level  # default to the narrowest valid (its own level)


def normalize_scope(
    *,
    school_code: Any,
    college: Any = "",
    department: Any = "",
    openness: Any = "",
    fallback_school: Any = "",
    default_openness: str = LEVEL_SCHOOL,
) -> dict[str, str]:
    """Normalize a material's attribution + openness into the standard fields."""
    school = normalize_school_code(school_code or fallback_school)
    col = normalize_college(college)
    dept = normalize_department(department)
    level = attribution_level(school, col, dept)
    chosen = openness if _norm(openness) else default_openness
    return {
        "attr_school_code": school,
        "attr_college": col,
        "attr_department": dept,
        "attr_level": level,
        "openness": clamp_openness(chosen, level),
    }


def _material_scope(material: Any) -> dict[str, str]:
    data = dict(material) if not isinstance(material, dict) else material
    return {
        "attr_school_code": normalize_school_code(data.get("attr_school_code")),
        "attr_college": normalize_college(data.get("attr_college")),
        "attr_department": normalize_department(data.get("attr_department")),
        "openness": _norm(data.get("openness")).lower() or LEVEL_SCHOOL,
    }


def can_view(material: Any, teacher_scope: dict[str, str], *, is_super_admin: bool = False) -> bool:
    """Whether a teacher (org scope) may view a material given its openness."""
    if is_super_admin:
        return True
    m = _material_scope(material)
    openness = m["openness"]
    if openness == LEVEL_PUBLIC:
        return True
    t_school = normalize_school_code(teacher_scope.get("school_code"))
    if m["attr_school_code"] != t_school:
        return False
    if openness == LEVEL_SCHOOL:
        return True
    t_college = normalize_college(teacher_scope.get("college"))
    if m["attr_college"] and m["attr_college"] != t_college:
        return False
    if openness == LEVEL_COLLEGE:
        return True
    # department openness
    t_department = normalize_department(teacher_scope.get("department"))
    return bool(m["attr_department"]) and m["attr_department"] == t_department


def build_visibility_filter(
    teacher_scope: dict[str, str],
    *,
    is_super_admin: bool = False,
    cols: dict[str, str] | None = None,
    table_alias: str = "",
) -> tuple[str, list[Any]]:
    """Engine-agnostic WHERE fragment limiting rows to those the teacher can see.

    Returns ``(sql, params)``; ``sql`` is always a self-contained boolean group.
    Compares normalized ``lower(trim(col))`` against normalized teacher values.
    """
    if is_super_admin:
        return "1=1", []
    columns = cols or DEFAULT_COLUMNS
    prefix = f"{table_alias}." if table_alias else ""

    def col(name: str) -> str:
        return f"lower(trim({prefix}{columns[name]}))"

    school = normalize_school_code(teacher_scope.get("school_code"))
    college = normalize_college(teacher_scope.get("college")).lower()
    department = normalize_department(teacher_scope.get("department")).lower()
    openness_col = f"lower(trim({prefix}{columns['openness']}))"

    clauses = [f"{openness_col} = 'public'"]
    params: list[Any] = []
    # school-visible
    clauses.append(f"({openness_col} = 'school' AND {col('school')} = ?)")
    params.append(school)
    # college-visible
    clauses.append(f"({openness_col} = 'college' AND {col('school')} = ? AND {col('college')} = ?)")
    params.extend([school, college])
    # department-visible
    clauses.append(
        f"({openness_col} = 'department' AND {col('school')} = ? AND {col('college')} = ? AND {col('department')} = ?)"
    )
    params.extend([school, college, department])
    return "(" + " OR ".join(clauses) + ")", params


def scope_summary(material: Any) -> dict[str, str]:
    """Human-facing labels for a material's attribution + openness (UI)."""
    data = dict(material) if not isinstance(material, dict) else material
    school_code = normalize_school_code(data.get("attr_school_code"))
    college = normalize_college(data.get("attr_college"))
    department = normalize_department(data.get("attr_department"))
    level = _norm(data.get("attr_level")).lower() or attribution_level(school_code, college, department)
    openness = _norm(data.get("openness")).lower() or LEVEL_SCHOOL
    school_name = _norm(data.get("attr_school_name")) or _norm(data.get("school_name"))
    attribution_parts = [part for part in (school_name or school_code, college, department) if part]
    return {
        "attr_level": level,
        "attr_level_label": ATTR_LABELS.get(level, "学校"),
        "attribution_label": " / ".join(attribution_parts) if attribution_parts else "本校",
        "openness": openness,
        "openness_label": OPENNESS_LABELS.get(openness, OPENNESS_LABELS[LEVEL_SCHOOL]),
    }
