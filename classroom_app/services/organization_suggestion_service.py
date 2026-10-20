"""Small, read-only organization directory for editable teacher selectors.

The directory and active teacher memberships provide evidence, never a language
model's guess. Cache values contain organization names only. Saving a membership
always resolves against the current transaction instead of this UI cache.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any

from .department_service import department_aliases, normalize_department
from .organization_scope_service import (
    build_org_scope,
    normalize_college,
    normalize_org_text,
    normalize_school_code,
)

ORGANIZATION_SUGGESTION_CACHE_SECONDS = 30
_CACHE_DATABASE_LIMIT = 8
_cache_lock = threading.RLock()
_cache: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()


def _table_names(conn) -> set[str]:
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {str(row["name"]) for row in rows}
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    return {str(row["table_name"]) for row in rows}


def _school_sources(conn, tables: set[str]) -> list[dict[str, Any]]:
    """Directory entries take precedence over denormalized teacher display names."""
    sources: list[dict[str, Any]] = []
    if "organization_schools" in tables:
        sources.extend(dict(row) for row in conn.execute(
            "SELECT school_code, school_name, is_active FROM organization_schools"
        ).fetchall())
    if "teacher_organization_memberships" in tables:
        teacher_filter = (
            " AND EXISTS (SELECT 1 FROM teachers t WHERE t.id = m.teacher_id "
            "AND COALESCE(t.is_active, 1) = 1)"
            if "teachers" in tables else ""
        )
        sources.extend(dict(row) for row in conn.execute(
            "SELECT DISTINCT school_code, school_name, 1 AS is_active "
            "FROM teacher_organization_memberships m "
            "WHERE COALESCE(m.is_active, 1) = 1" + teacher_filter
        ).fetchall())
    if "teachers" in tables:
        # All-revoked memberships must not resurrect the legacy primary scope.
        legacy_filter = (
            " AND NOT EXISTS (SELECT 1 FROM teacher_organization_memberships m WHERE m.teacher_id = t.id)"
            if "teacher_organization_memberships" in tables else ""
        )
        sources.extend(dict(row) for row in conn.execute(
            "SELECT DISTINCT school_code, school_name, 1 AS is_active "
            "FROM teachers t WHERE COALESCE(t.is_active, 1) = 1" + legacy_filter
        ).fetchall())
    return sources


def _school_map(sources: list[dict[str, Any]], *, include_inactive: bool = False) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for row in sources:
        if not normalize_org_text(row.get("school_code")) and not normalize_org_text(row.get("school_name")):
            continue
        scope = build_org_scope(school_code=row.get("school_code"), school_name=row.get("school_name"))
        code = scope["school_code"]
        if code in seen:
            continue
        seen.add(code)
        if include_inactive or row.get("is_active") is None or int(row["is_active"]) == 1:
            result[code] = {"school_code": code, "school_name": scope["school_name"]}
    return result


def _resolve_school(
    schools: dict[str, dict[str, str]], *, school_code: Any = "", school_name: Any = "",
    strict: bool = False,
) -> dict[str, str]:
    code = normalize_org_text(school_code).casefold()
    name = normalize_org_text(school_name)
    matches = [item for item in schools.values() if item["school_name"].casefold() == name.casefold()] if name else []
    if code and code in schools:
        known = schools[code]
        if name and name.casefold() != known["school_name"].casefold():
            if strict:
                raise ValueError("学校名称与学校标识不一致，请重新选择学校。")
            return {"school_code": "", "school_name": name}
        return dict(known)
    if matches:
        if len(matches) > 1:
            if strict:
                raise ValueError("存在同名学校，请从推荐列表选择学校并保留学校标识。")
            return {"school_code": "", "school_name": name}
        known = matches[0]
        if code and code != known["school_code"]:
            if strict:
                raise ValueError("学校名称与学校标识不一致，请重新选择学校。")
            return {"school_code": "", "school_name": name}
        return dict(known)
    scope = build_org_scope(school_code=code, school_name=name)
    return {"school_code": scope["school_code"], "school_name": scope["school_name"]}


def canonicalize_membership_scope(
    conn, *, school_code: Any = "", school_name: Any = "",
    college: Any = "", department: Any = "",
) -> dict[str, str]:
    """Resolve a save using authoritative school identifiers and safe aliases.

    This never renames an existing school or guesses an unknown abbreviation.
    Inactive directory records also participate in identity resolution so a
    stale form cannot create a second identity for the same school.
    """
    schools = _school_map(_school_sources(conn, _table_names(conn)), include_inactive=True)
    school = _resolve_school(schools, school_code=school_code, school_name=school_name, strict=True)
    return build_org_scope(**school, college=college, department=department)


def invalidate_organization_suggestions(conn=None) -> None:
    """Call after organization/account writes; other workers expire within 30s.

    Clearing before a transaction commits is harmless (the cache is a hint).
    A post-commit clear is preferred. Save validation never consults this cache.
    """
    with _cache_lock:
        _cache.clear()


def _has_open_transaction(conn) -> bool:
    if isinstance(conn, sqlite3.Connection):
        return conn.in_transaction
    info = getattr(getattr(conn, "raw_connection", conn), "info", None)
    return bool(int(getattr(info, "transaction_status", 0)))


def _cache_key(conn) -> tuple:
    if isinstance(conn, sqlite3.Connection):
        row = conn.execute("PRAGMA database_list").fetchone()
        path = str(row["file"] or "")
        # Keep in-memory fixtures distinct, including when object ids are reused.
        return ("sqlite", path or conn)
    raw = getattr(conn, "raw_connection", conn)
    info = getattr(raw, "info", None)
    row = conn.execute("SELECT current_database() AS db, current_schema() AS schema").fetchone()
    return ("postgres", getattr(info, "host", ""), getattr(info, "port", ""), row["db"], row["schema"])


def _load_snapshot(conn) -> dict[str, Any]:
    tables = _table_names(conn)
    schools = _school_map(_school_sources(conn, tables))
    colleges: dict[str, set[str]] = {}
    departments: dict[tuple[str, str], set[str]] = {}
    disabled_colleges: set[tuple[str, str]] = set()
    disabled_departments: set[tuple[str, str, str]] = set()
    if "organization_colleges" in tables:
        for row in conn.execute(
            "SELECT school_code, college_name, is_active FROM organization_colleges"
        ).fetchall():
            code, college = normalize_school_code(row["school_code"]), normalize_college(row["college_name"])
            if row["is_active"] is not None and not int(row["is_active"]):
                disabled_colleges.add((code, college))
            elif code in schools and college:
                colleges.setdefault(code, set()).add(college)
    if "organization_departments" in tables:
        for row in conn.execute(
            "SELECT school_code, college_name, department_name, is_active FROM organization_departments"
        ).fetchall():
            code = normalize_school_code(row["school_code"])
            college, department = normalize_college(row["college_name"]), normalize_department(row["department_name"])
            if row["is_active"] is not None and not int(row["is_active"]):
                disabled_departments.add((code, college, department))
            elif code in schools and department and (code, college) not in disabled_colleges:
                departments.setdefault((code, college), set()).add(department)
                if college:
                    colleges.setdefault(code, set()).add(college)
    teacher_scopes: list[Any] = []
    if "teacher_organization_memberships" in tables:
        teacher_filter = (
            " AND EXISTS (SELECT 1 FROM teachers t WHERE t.id = m.teacher_id AND COALESCE(t.is_active, 1) = 1)"
            if "teachers" in tables else ""
        )
        teacher_scopes.extend(conn.execute(
            "SELECT DISTINCT school_code, college, department FROM teacher_organization_memberships m "
            "WHERE COALESCE(m.is_active, 1) = 1" + teacher_filter
        ).fetchall())
    if "teachers" in tables:
        legacy_filter = (
            " AND NOT EXISTS (SELECT 1 FROM teacher_organization_memberships m WHERE m.teacher_id = t.id)"
            if "teacher_organization_memberships" in tables else ""
        )
        teacher_scopes.extend(conn.execute(
            "SELECT DISTINCT school_code, college, department FROM teachers t "
            "WHERE COALESCE(t.is_active, 1) = 1" + legacy_filter
        ).fetchall())
    for row in teacher_scopes:
        code = normalize_school_code(row["school_code"])
        college, department = normalize_college(row["college"]), normalize_department(row["department"])
        if code not in schools or (code, college) in disabled_colleges:
            continue
        if college:
            colleges.setdefault(code, set()).add(college)
        if department and (code, college, department) not in disabled_departments:
            departments.setdefault((code, college), set()).add(department)
    return {"schools": schools, "colleges": colleges, "departments": departments}


def get_organization_suggestions(
    conn, *, school_code: Any = "", school_name: Any = "", college: Any = "",
) -> dict[str, Any]:
    # Never publish a caller's uncommitted rows into the shared UI cache.
    cacheable = not _has_open_transaction(conn)
    key = _cache_key(conn) if cacheable else None
    with _cache_lock:
        entry = _cache.get(key) if key is not None else None
        if entry and time.monotonic() - entry[0] < ORGANIZATION_SUGGESTION_CACHE_SECONDS:
            snapshot = entry[1]
            _cache.move_to_end(key)
        else:
            snapshot = _load_snapshot(conn)
            if key is not None:
                _cache[key] = (time.monotonic(), snapshot)
                _cache.move_to_end(key)
                while len(_cache) > _CACHE_DATABASE_LIMIT:
                    _cache.popitem(last=False)
    selected = _resolve_school(snapshot["schools"], school_code=school_code, school_name=school_name)
    code = selected["school_code"] if normalize_org_text(school_code) or normalize_org_text(school_name) else ""
    clean_college = normalize_college(college)
    departments: set[str] = set()
    for (scope_school, scope_college), options in snapshot["departments"].items():
        if scope_school == code and (not clean_college or scope_college == clean_college):
            departments.update(options)
    department_options = sorted(departments)[:200]
    return {
        "schools": [dict(item) for item in sorted(
            snapshot["schools"].values(), key=lambda item: (item["school_name"], item["school_code"])
        )][:200],
        "colleges": sorted(snapshot["colleges"].get(code, set()))[:200],
        "departments": department_options,
        "department_aliases": {item: department_aliases(item) for item in department_options},
        "school_code": code,
        "cache_seconds": ORGANIZATION_SUGGESTION_CACHE_SECONDS,
    }
