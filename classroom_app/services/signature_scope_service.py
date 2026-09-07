"""Signature visibility, independent of signer title and usage authorization.

The same hierarchy drives detail checks and list SQL. Empty organization fields
never grant access, and department names are always anchored to their college.
"""
from __future__ import annotations

from typing import Any

SCOPE_LABELS = {
    "platform": "平台可见",
    "school": "学校可见",
    "college": "学院可见",
    "department": "系部可见",
    "personal": "个人",
}
SCOPE_FIELDS = {
    "school": ("school_code",),
    "college": ("school_code", "college"),
    "department": ("school_code", "college", "department"),
}
_SQL_WHITESPACE = "".join(chr(code) for code in range(0x3001) if chr(code).isspace())


def _sql_field(field: str) -> str:
    # Python str.strip and SQL TRIM must agree for legacy tab/newline-padded
    # names too. The fields here come exclusively from SCOPE_FIELDS.
    value = f"TRIM(COALESCE(s.{field}, ''), '{_SQL_WHITESPACE}')"
    return f"LOWER({value})" if field == "school_code" else value


def organization(value: Any) -> dict[str, str]:
    data = dict(value or {})
    result = {key: str(data.get(key) or "").strip()
              for key in ("school_code", "school_name", "college", "department")}
    result["school_code"] = result["school_code"].lower()
    return result


def memberships(actor: dict[str, Any]) -> list[dict[str, str]]:
    # An explicitly empty list means memberships were withdrawn, not that the
    # old primary account organization should be restored.
    source = actor.get("memberships")
    if not isinstance(source, list):
        source = [actor.get("scope") or {}]
    return [organization(item) for item in source if isinstance(item, dict)
            and str(item.get("school_code") or "").strip()]


def matches(actor: dict[str, Any], row: Any, level: str) -> bool:
    fields = SCOPE_FIELDS.get(level)
    if not fields:
        return False
    target = organization(row)
    return all(target[key] for key in fields) and any(
        all(member[key] == target[key] for key in fields)
        for member in memberships(actor)
    )


def visible(actor: dict[str, Any], row: Any) -> bool:
    role, actor_id = str(actor.get("role") or ""), int(actor.get("id") or 0)
    if role not in {"teacher", "student"} or actor_id <= 0:
        return False
    if actor.get("is_super_admin"):
        return True
    data = dict(row)
    if any(data.get(f"{kind}_role") == role and int(data.get(f"{kind}_id") or 0) == actor_id
           for kind in ("owner", "subject")):
        return True
    level = str(data.get("scope_level") or "personal")
    return level == "platform" or matches(actor, row, level)


def visibility_sql(actor: dict[str, Any], school_code: str = "") -> tuple[str, list[Any]]:
    role, actor_id = str(actor.get("role") or ""), int(actor.get("id") or 0)
    if role not in {"teacher", "student"} or actor_id <= 0:
        return "1 = 0", []
    params: list[Any] = []
    if actor.get("is_super_admin"):
        sql = "1 = 1"
    else:
        clauses = ["(s.owner_role = ? AND s.owner_id = ?)",
                   "(s.subject_role = ? AND s.subject_id = ?)", "s.scope_level = 'platform'"]
        params.extend([role, actor_id, role, actor_id])
        for level, fields in SCOPE_FIELDS.items():
            seen = set()
            for member in memberships(actor):
                key = tuple(member[field] for field in fields)
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                checks = ["s.scope_level = ?"]
                params.append(level)
                for field, value in zip(fields, key):
                    checks.append(f"{_sql_field(field)} = ?")
                    params.append(value)
                clauses.append("(" + " AND ".join(checks) + ")")
        sql = "(" + " OR ".join(clauses) + ")"
    selected = str(school_code or "").strip().lower()
    if selected:
        sql = f"({sql}) AND {_sql_field('school_code')} = ?"
        params.append(selected)
    return sql, params
