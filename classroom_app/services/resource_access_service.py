from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException

from .organization_scope_service import (
    build_org_scope,
    load_teacher_org_memberships,
    normalize_school_code,
)


SCOPE_PRIVATE = "private"
SCOPE_CLASSROOM = "classroom"
SCOPE_CLASS = "class"
SCOPE_DEPARTMENT = "department"
SCOPE_SCHOOL = "school"
SCOPE_PUBLIC = "public"

RESOURCE_SCOPE_LEVELS = {
    SCOPE_PRIVATE,
    SCOPE_CLASSROOM,
    SCOPE_CLASS,
    SCOPE_DEPARTMENT,
    SCOPE_SCHOOL,
    SCOPE_PUBLIC,
}


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if key in row.keys():
            return row[key]
    except (AttributeError, KeyError):
        pass
    return default


def normalize_scope_level(value: Any, default: str = SCOPE_PRIVATE) -> str:
    scope = str(value or "").strip().lower()
    return scope if scope in RESOURCE_SCOPE_LEVELS else default


def is_super_admin_teacher(conn: sqlite3.Connection, teacher_id: int | str | None) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    row = conn.execute(
        """
        SELECT COALESCE(is_super_admin, 0) AS is_super_admin,
               COALESCE(is_active, 1) AS is_active
        FROM teachers
        WHERE id = ?
        LIMIT 1
        """,
        (teacher_pk,),
    ).fetchone()
    return bool(row and int(row["is_active"] or 0) == 1 and int(row["is_super_admin"] or 0) == 1)


def _load_student_context(conn: sqlite3.Connection, student_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id, s.class_id, s.school_code, s.school_name, s.college, s.department,
               COALESCE(s.enrollment_status, 'active') AS enrollment_status
        FROM students s
        WHERE s.id = ?
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    if not row or str(row["enrollment_status"] or "active") != "active":
        return None
    scope = build_org_scope(
        school_code=row["school_code"],
        school_name=row["school_name"],
        college=row["college"],
        department=row["department"],
    )
    return {
        "id": int(row["id"]),
        "class_id": int(row["class_id"]),
        "scope": scope,
    }


def _teacher_memberships(conn: sqlite3.Connection, teacher_id: int) -> list[dict[str, str]]:
    return load_teacher_org_memberships(conn, teacher_id)


def _resource_scope(row: Any) -> dict[str, str]:
    return build_org_scope(
        school_code=_row_value(row, "school_code"),
        school_name=_row_value(row, "school_name"),
        college=_row_value(row, "college"),
        department=_row_value(row, "department"),
    )


def _same_school(scope: dict[str, str], candidate: dict[str, str]) -> bool:
    return normalize_school_code(scope.get("school_code")) == normalize_school_code(candidate.get("school_code"))


def _same_department(scope: dict[str, str], candidate: dict[str, str]) -> bool:
    return _same_school(scope, candidate) and bool(scope.get("department")) and scope.get("department") == candidate.get("department")


def _teacher_matches_school(conn: sqlite3.Connection, teacher_id: int, row: Any) -> bool:
    target = _resource_scope(row)
    return any(_same_school(scope, target) for scope in _teacher_memberships(conn, teacher_id))


def _teacher_matches_department(conn: sqlite3.Connection, teacher_id: int, row: Any) -> bool:
    target = _resource_scope(row)
    return any(_same_department(scope, target) for scope in _teacher_memberships(conn, teacher_id))


def teacher_matches_school(conn: sqlite3.Connection, teacher_id: int | str, row: Any) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    return _teacher_matches_school(conn, teacher_pk, row)


def teacher_matches_department(conn: sqlite3.Connection, teacher_id: int | str, row: Any) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    return _teacher_matches_department(conn, teacher_pk, row)


def teacher_can_manage_owned_row(
    conn: sqlite3.Connection,
    teacher_id: int | str,
    row: Any,
    *,
    owner_key: str = "teacher_id",
) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    owner_pk = _safe_int(_row_value(row, owner_key))
    return owner_pk == teacher_pk or is_super_admin_teacher(conn, teacher_pk)


def teacher_can_use_class(conn: sqlite3.Connection, teacher_id: int | str, class_row: Any) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    return teacher_can_manage_owned_row(conn, teacher_pk, class_row, owner_key="created_by_teacher_id") or _teacher_matches_school(
        conn,
        teacher_pk,
        class_row,
    )


def teacher_can_manage_class(conn: sqlite3.Connection, teacher_id: int | str, class_row: Any) -> bool:
    return teacher_can_manage_owned_row(conn, teacher_id, class_row, owner_key="created_by_teacher_id")


def teacher_can_use_course(conn: sqlite3.Connection, teacher_id: int | str, course_row: Any) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    return teacher_can_manage_owned_row(conn, teacher_pk, course_row, owner_key="created_by_teacher_id") or _teacher_matches_school(
        conn,
        teacher_pk,
        course_row,
    )


def teacher_can_manage_course(conn: sqlite3.Connection, teacher_id: int | str, course_row: Any) -> bool:
    return teacher_can_manage_owned_row(conn, teacher_id, course_row, owner_key="created_by_teacher_id")


def teacher_can_use_exam_paper(conn: sqlite3.Connection, teacher_id: int | str, paper_row: Any) -> bool:
    teacher_pk = _safe_int(teacher_id)
    if teacher_pk is None:
        return False
    return teacher_can_manage_owned_row(conn, teacher_pk, paper_row, owner_key="teacher_id") or _teacher_matches_department(
        conn,
        teacher_pk,
        paper_row,
    )


def teacher_can_manage_exam_paper(conn: sqlite3.Connection, teacher_id: int | str, paper_row: Any) -> bool:
    return teacher_can_manage_owned_row(conn, teacher_id, paper_row, owner_key="teacher_id")


def _student_matches_school(conn: sqlite3.Connection, student_id: int, row: Any) -> bool:
    student = _load_student_context(conn, student_id)
    return bool(student and _same_school(student["scope"], _resource_scope(row)))


def _student_matches_department(conn: sqlite3.Connection, student_id: int, row: Any) -> bool:
    student = _load_student_context(conn, student_id)
    return bool(student and _same_department(student["scope"], _resource_scope(row)))


def user_owns_resource(user: dict[str, Any] | None, row: Any) -> bool:
    if not user:
        return False
    role = str(user.get("role") or "").strip().lower()
    user_pk = _safe_int(user.get("id"))
    owner_role = str(_row_value(row, "owner_role", "") or "").strip().lower()
    owner_pk = _safe_int(_row_value(row, "owner_user_pk", _row_value(row, "owner_id")))
    if owner_role and owner_pk is not None:
        return role == owner_role and user_pk == owner_pk

    if role == "teacher":
        teacher_owner = _safe_int(_row_value(row, "teacher_id", _row_value(row, "uploaded_by_teacher_id", _row_value(row, "created_by_teacher_id"))))
        return teacher_owner is not None and user_pk == teacher_owner
    if role == "student":
        student_owner = _safe_int(_row_value(row, "student_pk_id", _row_value(row, "author_user_pk")))
        return student_owner is not None and user_pk == student_owner
    return False


def can_manage_scoped_resource(conn: sqlite3.Connection, row: Any, user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    role = str(user.get("role") or "").strip().lower()
    if role == "teacher" and is_super_admin_teacher(conn, user.get("id")):
        return True
    return user_owns_resource(user, row)


def _student_in_course(conn: sqlite3.Connection, student_id: int, course_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.course_id = ?
          AND s.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        LIMIT 1
        """,
        (course_id, student_id),
    ).fetchone()
    return row is not None


def _student_in_classroom(conn: sqlite3.Connection, student_id: int, class_offering_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM class_offerings o
        JOIN students s ON s.class_id = o.class_id
        WHERE o.id = ?
          AND s.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        LIMIT 1
        """,
        (class_offering_id, student_id),
    ).fetchone()
    return row is not None


def _teacher_teaches_class(conn: sqlite3.Connection, teacher_id: int, class_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM class_offerings
        WHERE teacher_id = ?
          AND class_id = ?
        LIMIT 1
        """,
        (teacher_id, class_id),
    ).fetchone()
    return row is not None


def can_read_scoped_resource(conn: sqlite3.Connection, row: Any, user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    role = str(user.get("role") or "").strip().lower()
    user_pk = _safe_int(user.get("id"))
    if user_pk is None:
        return False
    if role == "teacher" and is_super_admin_teacher(conn, user_pk):
        return True
    if user_owns_resource(user, row):
        return True

    scope = normalize_scope_level(_row_value(row, "scope_level"), default=SCOPE_PRIVATE)
    if scope == SCOPE_PRIVATE:
        return False
    if scope == SCOPE_PUBLIC:
        return True
    if scope == SCOPE_SCHOOL:
        if role == "teacher":
            return _teacher_matches_school(conn, user_pk, row)
        if role == "student":
            return _student_matches_school(conn, user_pk, row)
        return False
    if scope == SCOPE_DEPARTMENT:
        if role == "teacher":
            return _teacher_matches_department(conn, user_pk, row)
        if role == "student":
            return _student_matches_department(conn, user_pk, row)
        return False
    if scope == SCOPE_CLASS:
        class_id = _safe_int(_row_value(row, "class_id", _row_value(row, "visible_class_id")))
        if class_id is None:
            return False
        if role == "student":
            student = _load_student_context(conn, user_pk)
            return bool(student and int(student["class_id"]) == class_id)
        if role == "teacher":
            return _teacher_teaches_class(conn, user_pk, class_id)
        return False
    if scope == SCOPE_CLASSROOM:
        class_offering_id = _safe_int(_row_value(row, "class_offering_id"))
        if class_offering_id is not None:
            try:
                ensure_classroom_access(conn, class_offering_id, user)
                return True
            except HTTPException:
                return False
        course_id = _safe_int(_row_value(row, "course_id"))
        if role == "student" and course_id is not None:
            return _student_in_course(conn, user_pk, course_id)
        return False
    return False


def ensure_scoped_resource_access(conn: sqlite3.Connection, row: Any, user: dict[str, Any] | None, *, manage: bool = False) -> None:
    allowed = can_manage_scoped_resource(conn, row, user) if manage else can_read_scoped_resource(conn, row, user)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied")


def ensure_classroom_access(conn: sqlite3.Connection, class_offering_id: int, user: dict[str, Any] | None):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_pk = _safe_int(user.get("id"))
    if user_pk is None:
        raise HTTPException(status_code=403, detail="Invalid user")

    offering = conn.execute(
        """
        SELECT o.*, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON o.course_id = c.id
        JOIN classes cl ON o.class_id = cl.id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if not offering:
        raise HTTPException(status_code=404, detail="Classroom not found")

    role = str(user.get("role") or "").strip().lower()
    if role == "teacher":
        if int(offering["teacher_id"]) == user_pk or is_super_admin_teacher(conn, user_pk):
            return offering
        raise HTTPException(status_code=403, detail="Permission denied")

    if role == "student":
        if _student_in_classroom(conn, user_pk, int(offering["id"])):
            return offering

    raise HTTPException(status_code=403, detail="Permission denied")


def build_course_file_scope(
    conn: sqlite3.Connection,
    *,
    user: dict[str, Any],
    course_id: int,
    class_offering_id: int | None = None,
    is_public: bool = True,
    is_teacher_resource: bool = False,
) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    user_pk = _safe_int(user.get("id"))
    if role != "teacher" or user_pk is None:
        raise HTTPException(status_code=403, detail="Permission denied")

    course = conn.execute(
        """
        SELECT id, school_code, school_name, college, department, created_by_teacher_id
        FROM courses
        WHERE id = ?
        LIMIT 1
        """,
        (int(course_id),),
    ).fetchone()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    class_id: int | None = None
    if class_offering_id is not None:
        offering = conn.execute(
            """
            SELECT id, class_id, teacher_id
            FROM class_offerings
            WHERE id = ? AND course_id = ?
            LIMIT 1
            """,
            (int(class_offering_id), int(course_id)),
        ).fetchone()
        if not offering:
            raise HTTPException(status_code=404, detail="Classroom not found")
        if int(offering["teacher_id"]) != user_pk and not is_super_admin_teacher(conn, user_pk):
            raise HTTPException(status_code=403, detail="Permission denied")
        class_id = int(offering["class_id"])
    elif int(course["created_by_teacher_id"]) != user_pk and not is_super_admin_teacher(conn, user_pk):
        raise HTTPException(status_code=403, detail="Permission denied")

    scope = build_org_scope(
        school_code=course["school_code"],
        school_name=course["school_name"],
        college=course["college"],
        department=course["department"],
    )
    if not is_public or is_teacher_resource:
        scope_level = SCOPE_PRIVATE
    elif class_offering_id is not None:
        scope_level = SCOPE_CLASSROOM
    elif scope["department"]:
        scope_level = SCOPE_DEPARTMENT
    else:
        scope_level = SCOPE_SCHOOL

    return {
        "owner_role": "teacher",
        "owner_user_pk": user_pk,
        "scope_level": scope_level,
        "class_offering_id": int(class_offering_id) if class_offering_id is not None else None,
        "class_id": class_id,
        "school_code": scope["school_code"],
        "school_name": scope["school_name"],
        "college": scope["college"],
        "department": scope["department"],
    }
