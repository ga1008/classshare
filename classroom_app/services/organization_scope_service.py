from __future__ import annotations

import re
import sqlite3
from typing import Any

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
    return {
        "school_code": normalize_school_code(school_code),
        "school_name": normalize_school_name(school_name),
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
