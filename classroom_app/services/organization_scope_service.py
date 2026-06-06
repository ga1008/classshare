from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..db.connection import get_configured_db_engine
from .department_service import infer_department_from_text, normalize_department


DEFAULT_SCHOOL_CODE = "gxufl"
DEFAULT_SCHOOL_NAME = "广西外国语学院"


def normalize_org_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text


def normalize_school_code(value: Any) -> str:
    text = normalize_org_text(value).casefold()
    return text or DEFAULT_SCHOOL_CODE


def normalize_school_name(value: Any) -> str:
    return normalize_org_text(value) or DEFAULT_SCHOOL_NAME


def normalize_college(value: Any) -> str:
    return normalize_org_text(value)


def build_org_scope(
    *,
    school_code: Any = "",
    school_name: Any = "",
    college: Any = "",
    department: Any = "",
) -> dict[str, str]:
    normalized_school_name = normalize_school_name(school_name)
    normalized_school_code_source = normalize_org_text(school_code)
    if not normalized_school_code_source and normalized_school_name != DEFAULT_SCHOOL_NAME:
        normalized_school_code_source = normalized_school_name
    return {
        "school_code": normalize_school_code(normalized_school_code_source),
        "school_name": normalized_school_name,
        "college": normalize_college(college),
        "department": normalize_department(department),
    }


def org_scope_from_row(row: Any) -> dict[str, str]:
    if row is None:
        return build_org_scope()
    data = dict(row)
    return build_org_scope(
        school_code=data.get("school_code"),
        school_name=data.get("school_name"),
        college=data.get("college") or data.get("academic_college"),
        department=data.get("department"),
    )


def _first_nonempty(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> str:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return ""
    if not row:
        return ""
    for value in row:
        text = normalize_org_text(value)
        if text:
            return text
    return ""


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    try:
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
            raise ValueError(f"Unsupported organization scope database engine: {engine!r}")
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _teacher_membership_rows(
    conn: sqlite3.Connection,
    teacher_id: int,
    *,
    include_inactive: bool = False,
):
    if not _table_exists(conn, "teacher_organization_memberships"):
        return []
    where = ["teacher_id = ?"]
    params: list[Any] = [int(teacher_id)]
    if not include_inactive:
        where.append("COALESCE(is_active, 1) = 1")
    return conn.execute(
        f"""
        SELECT *
        FROM teacher_organization_memberships
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(is_primary, 0) DESC,
                 COALESCE(is_active, 1) DESC,
                 updated_at DESC,
                 id DESC
        """,
        params,
    ).fetchall()


def load_teacher_org_memberships(
    conn: sqlite3.Connection,
    teacher_id: int | str,
    *,
    include_inactive: bool = False,
) -> list[dict[str, str]]:
    """Return all school memberships for a teacher.

    The legacy teachers.school_* columns remain the compatibility source when
    the membership table has not been created yet or is empty.
    """
    teacher_id_int = int(teacher_id)
    rows = _teacher_membership_rows(conn, teacher_id_int, include_inactive=include_inactive)
    memberships: list[dict[str, str]] = []
    seen_school_codes: set[str] = set()
    for row in rows:
        scope = build_org_scope(
            school_code=row["school_code"],
            school_name=row["school_name"],
            college=row["college"],
            department=row["department"],
        )
        school_code = scope["school_code"]
        if not school_code or school_code in seen_school_codes:
            continue
        seen_school_codes.add(school_code)
        memberships.append(
            {
                **scope,
                "membership_id": str(row["id"]),
                "is_primary": "1" if int(row["is_primary"] or 0) else "0",
                "is_active": "1" if int(row["is_active"] or 0) else "0",
            }
        )

    if memberships:
        return memberships

    row = conn.execute(
        """
        SELECT id, school_code, school_name, college, department
        FROM teachers
        WHERE id = ?
        LIMIT 1
        """,
        (teacher_id_int,),
    ).fetchone()
    if not row:
        return []
    scope = org_scope_from_row(row)
    return [
        {
            **scope,
            "membership_id": "",
            "is_primary": "1",
            "is_active": "1",
        }
    ]


def _most_used_department(conn: sqlite3.Connection, teacher_id: int) -> str:
    department = _first_nonempty(
        conn,
        """
        SELECT department
        FROM (
            SELECT department, COUNT(*) AS usage_count
            FROM courses
            WHERE created_by_teacher_id = ?
              AND TRIM(COALESCE(department, '')) != ''
            GROUP BY department
            UNION ALL
            SELECT department, COUNT(*) AS usage_count
            FROM classes
            WHERE created_by_teacher_id = ?
              AND TRIM(COALESCE(department, '')) != ''
            GROUP BY department
        )
        ORDER BY usage_count DESC
        LIMIT 1
        """,
        (int(teacher_id), int(teacher_id)),
    )
    return normalize_department(department)


def load_teacher_org_scope(conn: sqlite3.Connection, teacher_id: int | str) -> dict[str, str]:
    teacher_id_int = int(teacher_id)
    memberships = load_teacher_org_memberships(conn, teacher_id_int)
    if memberships:
        primary = next((item for item in memberships if item.get("is_primary") == "1"), memberships[0])
        return build_org_scope(
            school_code=primary.get("school_code"),
            school_name=primary.get("school_name"),
            college=primary.get("college"),
            department=primary.get("department"),
        )

    row = conn.execute(
        """
        SELECT id, school_code, school_name, college, department
        FROM teachers
        WHERE id = ?
        LIMIT 1
        """,
        (teacher_id_int,),
    ).fetchone()
    scope = org_scope_from_row(row)

    if scope["school_code"] == DEFAULT_SCHOOL_CODE and scope["school_name"] == DEFAULT_SCHOOL_NAME:
        credential_school = conn.execute(
            """
            SELECT school_code, school_name
            FROM teacher_academic_system_credentials
            WHERE teacher_id = ?
              AND TRIM(COALESCE(school_code, '')) != ''
            ORDER BY enabled DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (teacher_id_int,),
        ).fetchone()
        if credential_school:
            scope["school_code"] = normalize_school_code(credential_school["school_code"])
            scope["school_name"] = normalize_school_name(credential_school["school_name"])

    if not scope["college"]:
        scope["college"] = normalize_college(
            _first_nonempty(
                conn,
                """
                SELECT academic_college
                FROM classes
                WHERE created_by_teacher_id = ?
                  AND TRIM(COALESCE(academic_college, '')) != ''
                ORDER BY academic_sync_at DESC, id DESC
                LIMIT 1
                """,
                (teacher_id_int,),
            )
        )
    if not scope["department"]:
        scope["department"] = _most_used_department(conn, teacher_id_int)
    return scope


def apply_teacher_scope_to_org(
    conn: sqlite3.Connection,
    teacher_id: int | str,
    *,
    school_code: Any = "",
    school_name: Any = "",
    college: Any = "",
    department: Any = "",
) -> dict[str, str]:
    teacher_scope = load_teacher_org_scope(conn, int(teacher_id))
    explicit = build_org_scope(
        school_code=school_code or teacher_scope["school_code"],
        school_name=school_name or teacher_scope["school_name"],
        college=college if normalize_org_text(college) else teacher_scope["college"],
        department=department if normalize_org_text(department) else teacher_scope["department"],
    )
    if not explicit["college"]:
        explicit["college"] = teacher_scope["college"]
    if not explicit["department"]:
        explicit["department"] = teacher_scope["department"]
    return explicit


def is_same_school(row: Any, teacher_scope: dict[str, str]) -> bool:
    row_scope = org_scope_from_row(row)
    return row_scope["school_code"] == normalize_school_code(teacher_scope.get("school_code"))


def is_same_department(row: Any, teacher_scope: dict[str, str]) -> bool:
    row_scope = org_scope_from_row(row)
    teacher_department = normalize_department(teacher_scope.get("department"))
    if not teacher_department:
        return False
    if row_scope["school_code"] != normalize_school_code(teacher_scope.get("school_code")):
        return False
    return normalize_department(row_scope.get("department")) == teacher_department


def teacher_has_school_scope(conn: sqlite3.Connection, teacher_id: int | str, row: Any) -> bool:
    row_scope = org_scope_from_row(row)
    row_school = normalize_school_code(row_scope.get("school_code"))
    if not row_school:
        return False
    return any(
        normalize_school_code(scope.get("school_code")) == row_school
        for scope in load_teacher_org_memberships(conn, int(teacher_id))
    )


def teacher_has_department_scope(conn: sqlite3.Connection, teacher_id: int | str, row: Any) -> bool:
    row_scope = org_scope_from_row(row)
    row_school = normalize_school_code(row_scope.get("school_code"))
    row_department = normalize_department(row_scope.get("department"))
    if not row_school or not row_department:
        return False
    for scope in load_teacher_org_memberships(conn, int(teacher_id)):
        if normalize_school_code(scope.get("school_code")) != row_school:
            continue
        if normalize_department(scope.get("department")) == row_department:
            return True
    return False


def organization_label(scope: dict[str, str]) -> str:
    return " / ".join(
        part
        for part in (
            normalize_school_name(scope.get("school_name")),
            normalize_college(scope.get("college")),
            normalize_department(scope.get("department")),
        )
        if part
    )
