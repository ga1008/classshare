from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ..db.connection import get_configured_db_engine
from .organization_scope_service import (
    DEFAULT_SCHOOL_NAME,
    normalize_org_text,
    normalize_school_code,
    normalize_school_name,
)


class OrganizationManagementError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    engine = get_configured_db_engine()
    if engine == "postgres":
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = ?
              AND table_name = ?
            LIMIT 1
            """,
            ("public", table_name),
        ).fetchone()
        return row is not None
    if engine != "sqlite":
        raise ValueError(f"Unsupported organization management database engine: {engine!r}")
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str, where_sql: str, params: tuple[Any, ...]) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where_sql}", params).fetchone()
    return int((row["cnt"] if row else 0) or 0)


def _count_distinct(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    where_sql: str,
    params: tuple[Any, ...],
) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(DISTINCT {column}) AS cnt FROM {table} WHERE {where_sql}", params).fetchone()
    return int((row["cnt"] if row else 0) or 0)


def _resource_counts(
    conn: sqlite3.Connection,
    *,
    school_code: str,
    college_name: str | None = None,
    department_name: str | None = None,
) -> dict[str, int]:
    school_code = normalize_school_code(school_code)
    filter_college = college_name is not None
    filter_department = department_name is not None
    college_name = normalize_org_text(college_name)
    department_name = normalize_org_text(department_name)
    where = ["school_code = ?"]
    params: list[Any] = [school_code]
    if filter_college:
        where.append("TRIM(COALESCE(college, '')) = ?")
        params.append(college_name)
    if filter_department:
        where.append("TRIM(COALESCE(department, '')) = ?")
        params.append(department_name)
    where_sql = " AND ".join(where)

    membership_where = where + ["COALESCE(is_active, 1) = 1"]
    membership_params = tuple(params)
    teacher_count = _count_distinct(
        conn,
        "teacher_organization_memberships",
        "teacher_id",
        " AND ".join(membership_where),
        membership_params,
    )
    if teacher_count <= 0:
        teacher_count = _count(conn, "teachers", where_sql, tuple(params))

    counts = {
        "teachers": teacher_count,
        "students": _count(conn, "students", where_sql, tuple(params)),
        "classes": _count(conn, "classes", where_sql, tuple(params)),
        "courses": _count(conn, "courses", where_sql, tuple(params)),
        "course_files": _count(conn, "course_files", where_sql, tuple(params)),
        "course_materials": _count(conn, "course_materials", where_sql, tuple(params)),
        "blog_posts": _count(conn, "blog_posts", where_sql, tuple(params)),
        "signatures": _count(conn, "electronic_signatures", where_sql, tuple(params)),
    }
    if not filter_college and not filter_department:
        counts["semesters"] = _count(conn, "academic_semesters", "school_code = ?", (school_code,))
    counts["total"] = sum(counts.values())
    return counts


def _school_row(conn: sqlite3.Connection, school_code: str):
    return conn.execute(
        """
        SELECT *
        FROM organization_schools
        WHERE school_code = ?
        LIMIT 1
        """,
        (normalize_school_code(school_code),),
    ).fetchone()


def _require_school(conn: sqlite3.Connection, school_code: str):
    row = _school_row(conn, school_code)
    if not row:
        raise OrganizationManagementError("学校不存在，请先创建学校。")
    return row


def _serialize_school(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    school_code = str(row["school_code"] or "")
    counts = _resource_counts(conn, school_code=school_code)
    return {
        "id": int(row["id"]),
        "school_code": school_code,
        "school_name": str(row["school_name"] or DEFAULT_SCHOOL_NAME),
        "display_order": int(row["display_order"] or 0),
        "is_active": bool(row["is_active"]),
        "source": str(row["source"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "deactivated_at": str(row["deactivated_at"] or ""),
        "reference_counts": counts,
        "reference_count": counts["total"],
    }


def _serialize_college(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    school_code = str(row["school_code"] or "")
    college_name = str(row["college_name"] or "")
    counts = _resource_counts(conn, school_code=school_code, college_name=college_name)
    return {
        "id": int(row["id"]),
        "school_code": school_code,
        "college_name": college_name,
        "display_order": int(row["display_order"] or 0),
        "is_active": bool(row["is_active"]),
        "source": str(row["source"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "deactivated_at": str(row["deactivated_at"] or ""),
        "reference_counts": counts,
        "reference_count": counts["total"],
    }


def _serialize_department(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    school_code = str(row["school_code"] or "")
    college_name = str(row["college_name"] or "")
    department_name = str(row["department_name"] or "")
    counts = _resource_counts(
        conn,
        school_code=school_code,
        college_name=college_name,
        department_name=department_name,
    )
    return {
        "id": int(row["id"]),
        "school_code": school_code,
        "college_name": college_name,
        "department_name": department_name,
        "display_order": int(row["display_order"] or 0),
        "is_active": bool(row["is_active"]),
        "source": str(row["source"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "deactivated_at": str(row["deactivated_at"] or ""),
        "reference_counts": counts,
        "reference_count": counts["total"],
    }


def list_school_options(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    include_inactive: bool = False,
    limit: int = 80,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if not include_inactive:
        where.append("COALESCE(is_active, 1) = 1")
    clean_query = normalize_org_text(query)
    if clean_query:
        like = f"%{clean_query}%"
        where.append("(school_code LIKE ? OR school_name LIKE ?)")
        params.extend([like, like])
    sql = """
        SELECT *
        FROM organization_schools
    """
    if where:
        sql += " WHERE " + " AND ".join(f"({item})" for item in where)
    sql += " ORDER BY display_order ASC, school_name COLLATE NOCASE ASC, id ASC LIMIT ?"
    params.append(max(1, min(_safe_int(limit, 80), 200)))
    return [_serialize_school(conn, row) for row in conn.execute(sql, params).fetchall()]


def list_organization_tree(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    include_inactive: bool = False,
) -> dict[str, Any]:
    school_rows = conn.execute(
        """
        SELECT *
        FROM organization_schools
        WHERE (? = 1 OR COALESCE(is_active, 1) = 1)
        ORDER BY display_order ASC, school_name COLLATE NOCASE ASC, id ASC
        """,
        (1 if include_inactive else 0,),
    ).fetchall()
    college_rows = conn.execute(
        """
        SELECT *
        FROM organization_colleges
        WHERE (? = 1 OR COALESCE(is_active, 1) = 1)
        ORDER BY school_code ASC, display_order ASC, college_name COLLATE NOCASE ASC, id ASC
        """,
        (1 if include_inactive else 0,),
    ).fetchall()
    department_rows = conn.execute(
        """
        SELECT *
        FROM organization_departments
        WHERE (? = 1 OR COALESCE(is_active, 1) = 1)
        ORDER BY school_code ASC, college_name COLLATE NOCASE ASC, display_order ASC, department_name COLLATE NOCASE ASC, id ASC
        """,
        (1 if include_inactive else 0,),
    ).fetchall()

    schools = [_serialize_school(conn, row) for row in school_rows]
    colleges = [_serialize_college(conn, row) for row in college_rows]
    departments = [_serialize_department(conn, row) for row in department_rows]
    clean_query = normalize_org_text(query).casefold()
    if clean_query:
        def matches(*parts: str) -> bool:
            return any(clean_query in str(part or "").casefold() for part in parts)

        visible_school_codes = {
            item["school_code"]
            for item in schools
            if matches(item["school_code"], item["school_name"])
        }
        visible_college_keys = {
            (item["school_code"], item["college_name"])
            for item in colleges
            if matches(item["college_name"], item["school_code"])
        }
        visible_school_codes.update(school for school, _college in visible_college_keys)
        visible_department_keys = {
            (item["school_code"], item["college_name"], item["department_name"])
            for item in departments
            if matches(item["department_name"], item["college_name"], item["school_code"])
        }
        visible_college_keys.update((school, college) for school, college, _department in visible_department_keys)
        visible_school_codes.update(school for school, _college in visible_college_keys)
        schools = [item for item in schools if item["school_code"] in visible_school_codes]
        colleges = [
            item for item in colleges
            if (item["school_code"], item["college_name"]) in visible_college_keys
               or item["school_code"] in visible_school_codes
        ]
        departments = [
            item for item in departments
            if (item["school_code"], item["college_name"], item["department_name"]) in visible_department_keys
               or (item["school_code"], item["college_name"]) in visible_college_keys
        ]

    departments_by_college: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for department in departments:
        departments_by_college.setdefault((department["school_code"], department["college_name"]), []).append(department)
    colleges_by_school: dict[str, list[dict[str, Any]]] = {}
    for college in colleges:
        college["departments"] = departments_by_college.get((college["school_code"], college["college_name"]), [])
        colleges_by_school.setdefault(college["school_code"], []).append(college)
    for school in schools:
        school["colleges"] = colleges_by_school.get(school["school_code"], [])
    return {
        "schools": schools,
        "summary": {
            "school_count": len(schools),
            "college_count": len(colleges),
            "department_count": len(departments),
            "active_school_count": sum(1 for item in schools if item["is_active"]),
        },
    }


def _update_school_name_references(conn: sqlite3.Connection, *, school_code: str, school_name: str) -> None:
    for table in ("teachers", "students", "classes", "courses", "academic_semesters", "electronic_signatures", "teacher_academic_system_credentials"):
        if _table_exists(conn, table):
            conn.execute(
                f"UPDATE {table} SET school_name = ? WHERE school_code = ?",
                (school_name, school_code),
            )


def _update_college_references(conn: sqlite3.Connection, *, school_code: str, old_name: str, new_name: str) -> None:
    for table in ("teachers", "students", "classes", "courses", "electronic_signatures"):
        if _table_exists(conn, table):
            conn.execute(
                f"UPDATE {table} SET college = ? WHERE school_code = ? AND TRIM(COALESCE(college, '')) = ?",
                (new_name, school_code, old_name),
            )
    conn.execute(
        """
        UPDATE organization_departments
        SET college_name = ?, updated_at = CURRENT_TIMESTAMP
        WHERE school_code = ? AND TRIM(COALESCE(college_name, '')) = ?
        """,
        (new_name, school_code, old_name),
    )


def _update_department_references(
    conn: sqlite3.Connection,
    *,
    school_code: str,
    college_name: str,
    old_name: str,
    new_name: str,
) -> None:
    for table in ("teachers", "students", "classes", "courses", "electronic_signatures"):
        if _table_exists(conn, table):
            conn.execute(
                f"""
                UPDATE {table}
                SET department = ?
                WHERE school_code = ?
                  AND TRIM(COALESCE(college, '')) = ?
                  AND TRIM(COALESCE(department, '')) = ?
                """,
                (new_name, school_code, college_name, old_name),
            )


def create_school(
    conn: sqlite3.Connection,
    *,
    school_code: str,
    school_name: str,
    display_order: int = 0,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    normalized_code = normalize_school_code(school_code)
    normalized_name = normalize_school_name(school_name)
    conn.execute(
        """
        INSERT INTO organization_schools (
            school_code, school_name, display_order, is_active, source,
            created_by_teacher_id, updated_by_teacher_id, updated_at, deactivated_at
        )
        VALUES (?, ?, ?, 1, 'manual', ?, ?, ?, NULL)
        ON CONFLICT(school_code) DO UPDATE SET
            school_name = excluded.school_name,
            display_order = excluded.display_order,
            is_active = 1,
            source = 'manual',
            updated_by_teacher_id = excluded.updated_by_teacher_id,
            updated_at = excluded.updated_at,
            deactivated_at = NULL
        """,
        (
            normalized_code,
            normalized_name,
            _safe_int(display_order),
            actor_teacher_id,
            actor_teacher_id,
            _now_iso(),
        ),
    )
    row = _school_row(conn, normalized_code)
    return _serialize_school(conn, row)


def update_school(
    conn: sqlite3.Connection,
    *,
    school_id: int,
    school_name: str,
    display_order: int = 0,
    is_active: bool = True,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM organization_schools WHERE id = ? LIMIT 1", (int(school_id),)).fetchone()
    if not row:
        raise OrganizationManagementError("学校不存在。")
    normalized_name = normalize_school_name(school_name)
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE organization_schools
        SET school_name = ?,
            display_order = ?,
            is_active = ?,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(deactivated_at, ?) END
        WHERE id = ?
        """,
        (
            normalized_name,
            _safe_int(display_order),
            1 if is_active else 0,
            actor_teacher_id,
            timestamp,
            1 if is_active else 0,
            timestamp,
            int(school_id),
        ),
    )
    _update_school_name_references(conn, school_code=row["school_code"], school_name=normalized_name)
    return _serialize_school(conn, _school_row(conn, row["school_code"]))


def delete_school(conn: sqlite3.Connection, *, school_id: int, actor_teacher_id: int | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM organization_schools WHERE id = ? LIMIT 1", (int(school_id),)).fetchone()
    if not row:
        raise OrganizationManagementError("学校不存在。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE organization_schools
        SET is_active = 0,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = COALESCE(deactivated_at, ?)
        WHERE id = ?
        """,
        (actor_teacher_id, timestamp, timestamp, int(school_id)),
    )
    return _serialize_school(conn, _school_row(conn, row["school_code"]))


def create_college(
    conn: sqlite3.Connection,
    *,
    school_code: str,
    college_name: str,
    display_order: int = 0,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    normalized_school = normalize_school_code(school_code)
    _require_school(conn, normalized_school)
    normalized_college = normalize_org_text(college_name)
    if not normalized_college:
        raise OrganizationManagementError("学院名称不能为空。")
    timestamp = _now_iso()
    conn.execute(
        """
        INSERT INTO organization_colleges (
            school_code, college_name, display_order, is_active, source,
            created_by_teacher_id, updated_by_teacher_id, updated_at, deactivated_at
        )
        VALUES (?, ?, ?, 1, 'manual', ?, ?, ?, NULL)
        ON CONFLICT(school_code, college_name) DO UPDATE SET
            display_order = excluded.display_order,
            is_active = 1,
            source = 'manual',
            updated_by_teacher_id = excluded.updated_by_teacher_id,
            updated_at = excluded.updated_at,
            deactivated_at = NULL
        """,
        (
            normalized_school,
            normalized_college,
            _safe_int(display_order),
            actor_teacher_id,
            actor_teacher_id,
            timestamp,
        ),
    )
    row = conn.execute(
        "SELECT * FROM organization_colleges WHERE school_code = ? AND college_name = ? LIMIT 1",
        (normalized_school, normalized_college),
    ).fetchone()
    return _serialize_college(conn, row)


def update_college(
    conn: sqlite3.Connection,
    *,
    college_id: int,
    college_name: str,
    display_order: int = 0,
    is_active: bool = True,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM organization_colleges WHERE id = ? LIMIT 1", (int(college_id),)).fetchone()
    if not row:
        raise OrganizationManagementError("学院不存在。")
    old_name = str(row["college_name"] or "")
    new_name = normalize_org_text(college_name)
    if not new_name:
        raise OrganizationManagementError("学院名称不能为空。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE organization_colleges
        SET college_name = ?,
            display_order = ?,
            is_active = ?,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(deactivated_at, ?) END
        WHERE id = ?
        """,
        (
            new_name,
            _safe_int(display_order),
            1 if is_active else 0,
            actor_teacher_id,
            timestamp,
            1 if is_active else 0,
            timestamp,
            int(college_id),
        ),
    )
    if old_name != new_name:
        _update_college_references(conn, school_code=row["school_code"], old_name=old_name, new_name=new_name)
    updated = conn.execute("SELECT * FROM organization_colleges WHERE id = ? LIMIT 1", (int(college_id),)).fetchone()
    return _serialize_college(conn, updated)


def delete_college(conn: sqlite3.Connection, *, college_id: int, actor_teacher_id: int | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM organization_colleges WHERE id = ? LIMIT 1", (int(college_id),)).fetchone()
    if not row:
        raise OrganizationManagementError("学院不存在。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE organization_colleges
        SET is_active = 0,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = COALESCE(deactivated_at, ?)
        WHERE id = ?
        """,
        (actor_teacher_id, timestamp, timestamp, int(college_id)),
    )
    updated = conn.execute("SELECT * FROM organization_colleges WHERE id = ? LIMIT 1", (int(college_id),)).fetchone()
    return _serialize_college(conn, updated)


def create_department(
    conn: sqlite3.Connection,
    *,
    school_code: str,
    college_name: str,
    department_name: str,
    display_order: int = 0,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    normalized_school = normalize_school_code(school_code)
    _require_school(conn, normalized_school)
    normalized_college = normalize_org_text(college_name)
    normalized_department = normalize_org_text(department_name)
    if not normalized_department:
        raise OrganizationManagementError("系部名称不能为空。")
    if normalized_college:
        create_college(
            conn,
            school_code=normalized_school,
            college_name=normalized_college,
            display_order=0,
            actor_teacher_id=actor_teacher_id,
        )
    timestamp = _now_iso()
    conn.execute(
        """
        INSERT INTO organization_departments (
            school_code, college_name, department_name, display_order, is_active, source,
            created_by_teacher_id, updated_by_teacher_id, updated_at, deactivated_at
        )
        VALUES (?, ?, ?, ?, 1, 'manual', ?, ?, ?, NULL)
        ON CONFLICT(school_code, college_name, department_name) DO UPDATE SET
            display_order = excluded.display_order,
            is_active = 1,
            source = 'manual',
            updated_by_teacher_id = excluded.updated_by_teacher_id,
            updated_at = excluded.updated_at,
            deactivated_at = NULL
        """,
        (
            normalized_school,
            normalized_college,
            normalized_department,
            _safe_int(display_order),
            actor_teacher_id,
            actor_teacher_id,
            timestamp,
        ),
    )
    row = conn.execute(
        """
        SELECT *
        FROM organization_departments
        WHERE school_code = ? AND college_name = ? AND department_name = ?
        LIMIT 1
        """,
        (normalized_school, normalized_college, normalized_department),
    ).fetchone()
    return _serialize_department(conn, row)


def update_department(
    conn: sqlite3.Connection,
    *,
    department_id: int,
    department_name: str,
    display_order: int = 0,
    is_active: bool = True,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM organization_departments WHERE id = ? LIMIT 1", (int(department_id),)).fetchone()
    if not row:
        raise OrganizationManagementError("系部不存在。")
    old_name = str(row["department_name"] or "")
    new_name = normalize_org_text(department_name)
    if not new_name:
        raise OrganizationManagementError("系部名称不能为空。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE organization_departments
        SET department_name = ?,
            display_order = ?,
            is_active = ?,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(deactivated_at, ?) END
        WHERE id = ?
        """,
        (
            new_name,
            _safe_int(display_order),
            1 if is_active else 0,
            actor_teacher_id,
            timestamp,
            1 if is_active else 0,
            timestamp,
            int(department_id),
        ),
    )
    if old_name != new_name:
        _update_department_references(
            conn,
            school_code=row["school_code"],
            college_name=row["college_name"],
            old_name=old_name,
            new_name=new_name,
        )
    updated = conn.execute("SELECT * FROM organization_departments WHERE id = ? LIMIT 1", (int(department_id),)).fetchone()
    return _serialize_department(conn, updated)


def delete_department(conn: sqlite3.Connection, *, department_id: int, actor_teacher_id: int | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM organization_departments WHERE id = ? LIMIT 1", (int(department_id),)).fetchone()
    if not row:
        raise OrganizationManagementError("系部不存在。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE organization_departments
        SET is_active = 0,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = COALESCE(deactivated_at, ?)
        WHERE id = ?
        """,
        (actor_teacher_id, timestamp, timestamp, int(department_id)),
    )
    updated = conn.execute("SELECT * FROM organization_departments WHERE id = ? LIMIT 1", (int(department_id),)).fetchone()
    return _serialize_department(conn, updated)
