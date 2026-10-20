from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from ..db.connection import execute_insert_returning_id
from ..dependencies import get_password_hash
from .organization_scope_service import build_org_scope, normalize_org_text, organization_label
from .organization_suggestion_service import canonicalize_membership_scope, invalidate_organization_suggestions
from .teacher_identity_service import assert_employee_number_available, normalize_employee_number

TEACHER_PASSWORD_MIN_LENGTH = 8
TEACHER_PASSWORD_HINT = "密码至少 8 位。"

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TeacherAccountConflict(ValueError):
    """The edit was prepared against an older account or membership version."""


def teacher_account_revision(teacher: dict[str, Any]) -> str:
    fields = (
        "id", "name", "email", "phone", "wechat", "qq", "homepage_url", "description",
        "school_code", "school_name", "college", "department", "employee_number",
        "is_active", "is_super_admin", "updated_at", "password_updated_at", "deactivated_at",
        "cancelled_at", "cancelled_by_teacher_id", "memberships",
    )
    payload = {key: teacher.get(key) for key in fields}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def require_teacher_account_revision(conn, teacher_id: int, expected_revision: str) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    teacher = get_teacher_account(conn, teacher_id)
    if not isinstance(expected_revision, str) or teacher["revision"] != expected_revision:
        raise TeacherAccountConflict("教师资料或任教归属已变化，请重新载入最新资料后核对草稿再保存。")
    return teacher


def lock_teacher_account_management(conn) -> None:
    """Serialize account/role/membership invariants in the caller transaction.

    Both Web and Agent use this lock. In particular, checking the last active
    administrator and changing it must never race another administrator change.
    PostgreSQL advisory locks are transaction scoped; there is no runtime DDL.
    """
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (76125662298437715,))


def _now_iso() -> str:
    return datetime.now().isoformat()


def normalize_teacher_email(email: str) -> str:
    return str(email or "").strip().lower()


def validate_teacher_password(password: str) -> None:
    if len(str(password or "")) < TEACHER_PASSWORD_MIN_LENGTH:
        raise ValueError(TEACHER_PASSWORD_HINT)


def _validate_teacher_identity(name: str, email: str) -> tuple[str, str]:
    normalized_name = " ".join(str(name or "").split())
    normalized_email = normalize_teacher_email(email)
    if not normalized_name:
        raise ValueError("教师姓名不能为空。")
    if not normalized_email or not _EMAIL_PATTERN.match(normalized_email):
        raise ValueError("请填写有效的教师邮箱。")
    return normalized_name, normalized_email


def _active_super_admin_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM teachers
        WHERE COALESCE(is_active, 1) = 1
          AND COALESCE(is_super_admin, 0) = 1
        """
    ).fetchone()
    return int((row["cnt"] if row else 0) or 0)


def _teacher_exists_by_email(
    conn: sqlite3.Connection,
    email: str,
    *,
    exclude_teacher_id: int | None = None,
):
    params: list[Any] = [normalize_teacher_email(email)]
    sql = "SELECT id, is_active, cancelled_at FROM teachers WHERE lower(email) = ?"
    if exclude_teacher_id is not None:
        sql += " AND id != ?"
        params.append(int(exclude_teacher_id))
    sql += " LIMIT 1"
    return conn.execute(sql, params).fetchone()


def _get_teacher_account_row(conn: sqlite3.Connection, teacher_id: int | str):
    return conn.execute(
        """
        SELECT id, name, email, phone, wechat, qq, homepage_url, description,
               school_code, school_name, college, department, employee_number,
               COALESCE(is_super_admin, 0) AS is_super_admin,
               COALESCE(is_active, 1) AS is_active,
               created_at, updated_at, password_updated_at, deactivated_at,
               cancelled_at, cancelled_by_teacher_id
        FROM teachers
        WHERE id = ?
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()


def _serialize_teacher_account(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "email": str(row["email"] or ""),
        "phone": str(row["phone"] or ""),
        "wechat": str(row["wechat"] or ""),
        "qq": str(row["qq"] or ""),
        "homepage_url": str(row["homepage_url"] or ""),
        "description": str(row["description"] or ""),
        "school_code": str(row["school_code"] or ""),
        "school_name": str(row["school_name"] or ""),
        "college": str(row["college"] or ""),
        "department": str(row["department"] or ""),
        "employee_number": str(row["employee_number"] or ""),
        "organization_label": " / ".join(
            part
            for part in (
                str(row["school_name"] or ""),
                str(row["college"] or ""),
                str(row["department"] or ""),
            )
            if part
        ),
        "is_super_admin": bool(row["is_super_admin"]),
        "is_active": bool(row["is_active"]),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "password_updated_at": str(row["password_updated_at"] or ""),
        "deactivated_at": str(row["deactivated_at"] or ""),
        "cancelled_at": str(row["cancelled_at"] or ""),
        "cancelled_by_teacher_id": row["cancelled_by_teacher_id"],
        "account_status": "cancelled" if row["cancelled_at"] else ("active" if row["is_active"] else "inactive"),
        "class_count": int(row["class_count"] or 0) if "class_count" in row.keys() else 0,
        "course_count": int(row["course_count"] or 0) if "course_count" in row.keys() else 0,
        "offering_count": int(row["offering_count"] or 0) if "offering_count" in row.keys() else 0,
        "material_count": int(row["material_count"] or 0) if "material_count" in row.keys() else 0,
        "pending_reset_count": int(row["pending_reset_count"] or 0) if "pending_reset_count" in row.keys() else 0,
    }


def _serialize_teacher_membership(row) -> dict[str, Any]:
    scope = build_org_scope(
        school_code=row["school_code"],
        school_name=row["school_name"],
        college=row["college"],
        department=row["department"],
    )
    return {
        "id": int(row["id"]),
        "teacher_id": int(row["teacher_id"]),
        "employee_number": str(row["employee_number"] or ""),
        **scope,
        "organization_label": organization_label(scope),
        "is_primary": bool(row["is_primary"]),
        "is_active": bool(row["is_active"]),
        "source": str(row["source"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "deactivated_at": str(row["deactivated_at"] or ""),
    }


def list_teacher_memberships(
    conn: sqlite3.Connection,
    teacher_id: int | str,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    where = ["teacher_id = ?"]
    params: list[Any] = [int(teacher_id)]
    if not include_inactive:
        where.append("COALESCE(is_active, 1) = 1")
    rows = conn.execute(
        f"""
        SELECT *
        FROM teacher_organization_memberships
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(is_primary, 0) DESC,
                 COALESCE(is_active, 1) DESC,
                 school_name COLLATE NOCASE,
                 department COLLATE NOCASE,
                 id DESC
        """,
        params,
    ).fetchall()
    return [_serialize_teacher_membership(row) for row in rows]


def _sync_teacher_primary_scope(conn: sqlite3.Connection, teacher_id: int) -> None:
    primary = conn.execute(
        """
        SELECT *
        FROM teacher_organization_memberships
        WHERE teacher_id = ?
          AND COALESCE(is_active, 1) = 1
          AND COALESCE(is_primary, 0) = 1
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()
    if not primary:
        primary = conn.execute(
            """
            SELECT *
            FROM teacher_organization_memberships
            WHERE teacher_id = ?
              AND COALESCE(is_active, 1) = 1
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (int(teacher_id),),
        ).fetchone()
        if primary:
            conn.execute(
                "UPDATE teacher_organization_memberships SET is_primary = 1, updated_at = ? WHERE id = ?",
                (_now_iso(), int(primary["id"])),
            )
    if not primary:
        return
    scope = build_org_scope(
        school_code=primary["school_code"],
        school_name=primary["school_name"],
        college=primary["college"],
        department=primary["department"],
    )
    conn.execute(
        """
        UPDATE teachers
        SET school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            employee_number = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            scope["school_code"],
            scope["school_name"],
            scope["college"],
            scope["department"],
            str(primary["employee_number"] or ""),
            _now_iso(),
            int(teacher_id),
        ),
    )


def _ensure_membership_catalog_scope(
    conn: sqlite3.Connection,
    scope: dict[str, str],
    *,
    actor_teacher_id: int | None = None,
) -> None:
    timestamp = _now_iso()
    conn.execute(
        """
        INSERT INTO organization_schools (
            school_code, school_name, source, created_by_teacher_id,
            updated_by_teacher_id, updated_at, deactivated_at
        )
        VALUES (?, ?, 'teacher_membership', ?, ?, ?, NULL)
        ON CONFLICT(school_code) DO UPDATE SET
            school_name = excluded.school_name,
            is_active = 1,
            updated_by_teacher_id = excluded.updated_by_teacher_id,
            updated_at = excluded.updated_at,
            deactivated_at = NULL
        """,
        (
            scope["school_code"],
            scope["school_name"],
            actor_teacher_id,
            actor_teacher_id,
            timestamp,
        ),
    )
    if scope["college"]:
        conn.execute(
            """
            INSERT INTO organization_colleges (
                school_code, college_name, source, created_by_teacher_id,
                updated_by_teacher_id, updated_at, deactivated_at
            )
            VALUES (?, ?, 'teacher_membership', ?, ?, ?, NULL)
            ON CONFLICT(school_code, college_name) DO UPDATE SET
                is_active = 1,
                updated_by_teacher_id = excluded.updated_by_teacher_id,
                updated_at = excluded.updated_at,
                deactivated_at = NULL
            """,
            (
                scope["school_code"],
                scope["college"],
                actor_teacher_id,
                actor_teacher_id,
                timestamp,
            ),
        )
    if scope["department"]:
        conn.execute(
            """
            INSERT INTO organization_departments (
                school_code, college_name, department_name, source, created_by_teacher_id,
                updated_by_teacher_id, updated_at, deactivated_at
            )
            VALUES (?, ?, ?, 'teacher_membership', ?, ?, ?, NULL)
            ON CONFLICT(school_code, college_name, department_name) DO UPDATE SET
                is_active = 1,
                updated_by_teacher_id = excluded.updated_by_teacher_id,
                updated_at = excluded.updated_at,
                deactivated_at = NULL
            """,
            (
                scope["school_code"],
                scope["college"],
                scope["department"],
                actor_teacher_id,
                actor_teacher_id,
                timestamp,
            ),
        )


def upsert_teacher_membership(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    school_code: str = "",
    school_name: str = "",
    college: str = "",
    department: str = "",
    is_primary: bool = False,
    actor_teacher_id: int | None = None,
    source: str = "manual",
    employee_number: str | None = None,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target or not bool(target["is_active"]):
        raise ValueError("教师账号不存在或已停用。")
    requested_school_name = normalize_org_text(school_name)
    requested_school_code = normalize_org_text(school_code)
    scope = canonicalize_membership_scope(
        conn,
        school_code=requested_school_code or ("" if requested_school_name else target["school_code"]),
        school_name=requested_school_name or target["school_name"],
        college=college if normalize_org_text(college) else target["college"],
        department=department if normalize_org_text(department) else target["department"],
    )
    if not scope["department"]:
        raise ValueError("教师任教归属必须包含系部。")
    existing = conn.execute(
        "SELECT employee_number FROM teacher_organization_memberships WHERE teacher_id = ? AND school_code = ?",
        (int(teacher_id), scope["school_code"]),
    ).fetchone()
    number = normalize_employee_number(
        employee_number if employee_number is not None else (existing["employee_number"] if existing else ""),
        required=employee_number is not None,
    )
    assert_employee_number_available(
        conn, teacher_id=teacher_id, school_code=scope["school_code"], employee_number=number,
    )
    timestamp = _now_iso()
    _ensure_membership_catalog_scope(conn, scope, actor_teacher_id=actor_teacher_id)
    if is_primary:
        conn.execute(
            "UPDATE teacher_organization_memberships SET is_primary = 0, updated_at = ? WHERE teacher_id = ?",
            (timestamp, int(teacher_id)),
        )
    conn.execute(
        """
        INSERT INTO teacher_organization_memberships (
            teacher_id, school_code, school_name, college, department, employee_number,
            is_primary, is_active, source, created_by_teacher_id, updated_by_teacher_id, updated_at, deactivated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)
        ON CONFLICT(teacher_id, school_code) DO UPDATE SET
            school_name = excluded.school_name,
            college = excluded.college,
            department = excluded.department,
            employee_number = excluded.employee_number,
            is_primary = CASE WHEN excluded.is_primary = 1 THEN 1 ELSE teacher_organization_memberships.is_primary END,
            is_active = 1,
            source = excluded.source,
            updated_by_teacher_id = excluded.updated_by_teacher_id,
            updated_at = excluded.updated_at,
            deactivated_at = NULL
        """,
        (
            int(teacher_id),
            scope["school_code"],
            scope["school_name"],
            scope["college"],
            scope["department"],
            number,
            1 if is_primary else 0,
            str(source or "manual"),
            actor_teacher_id,
            actor_teacher_id,
            timestamp,
        ),
    )
    _sync_teacher_primary_scope(conn, int(teacher_id))
    invalidate_organization_suggestions(conn)
    row = conn.execute(
        """
        SELECT *
        FROM teacher_organization_memberships
        WHERE teacher_id = ? AND school_code = ?
        LIMIT 1
        """,
        (int(teacher_id), scope["school_code"]),
    ).fetchone()
    return _serialize_teacher_membership(row)


def set_teacher_primary_membership(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    membership_id: int,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target or not bool(target["is_active"]) or target["cancelled_at"]:
        raise ValueError("教师账号不存在、已停用或已注销。")
    row = conn.execute(
        """
        SELECT *
        FROM teacher_organization_memberships
        WHERE id = ? AND teacher_id = ? AND COALESCE(is_active, 1) = 1
        LIMIT 1
        """,
        (int(membership_id), int(teacher_id)),
    ).fetchone()
    if not row:
        raise ValueError("教师任教归属不存在或已停用。")
    timestamp = _now_iso()
    conn.execute(
        "UPDATE teacher_organization_memberships SET is_primary = 0, updated_at = ? WHERE teacher_id = ?",
        (timestamp, int(teacher_id)),
    )
    conn.execute(
        "UPDATE teacher_organization_memberships SET is_primary = 1, updated_at = ? WHERE id = ?",
        (timestamp, int(membership_id)),
    )
    _sync_teacher_primary_scope(conn, int(teacher_id))
    invalidate_organization_suggestions(conn)
    return _serialize_teacher_membership(conn.execute(
        "SELECT * FROM teacher_organization_memberships WHERE id = ?",
        (int(membership_id),),
    ).fetchone())


def deactivate_teacher_membership(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    membership_id: int,
    actor_teacher_id: int | None = None,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target or not bool(target["is_active"]) or target["cancelled_at"]:
        raise ValueError("教师账号不存在、已停用或已注销。")
    active_count = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM teacher_organization_memberships
        WHERE teacher_id = ? AND COALESCE(is_active, 1) = 1
        """,
        (int(teacher_id),),
    ).fetchone()
    if int((active_count["cnt"] if active_count else 0) or 0) <= 1:
        raise ValueError("至少需要保留一个启用状态的教师任教归属。")
    row = conn.execute(
        """
        SELECT *
        FROM teacher_organization_memberships
        WHERE id = ? AND teacher_id = ?
        LIMIT 1
        """,
        (int(membership_id), int(teacher_id)),
    ).fetchone()
    if not row:
        raise ValueError("教师任教归属不存在。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE teacher_organization_memberships
        SET is_active = 0,
            is_primary = 0,
            updated_by_teacher_id = ?,
            updated_at = ?,
            deactivated_at = COALESCE(deactivated_at, ?)
        WHERE id = ?
        """,
        (actor_teacher_id, timestamp, timestamp, int(membership_id)),
    )
    _sync_teacher_primary_scope(conn, int(teacher_id))
    invalidate_organization_suggestions(conn)
    return _serialize_teacher_membership(conn.execute(
        "SELECT * FROM teacher_organization_memberships WHERE id = ?",
        (int(membership_id),),
    ).fetchone())


def _attach_teacher_memberships(conn: sqlite3.Connection, teacher: dict[str, Any]) -> dict[str, Any]:
    memberships = list_teacher_memberships(conn, teacher["id"], include_inactive=True)
    active_memberships = [item for item in memberships if item["is_active"]]
    teacher["memberships"] = memberships
    teacher["active_memberships"] = active_memberships
    teacher["membership_count"] = len(active_memberships)
    teacher["membership_labels"] = [item["organization_label"] for item in active_memberships if item["organization_label"]]
    if teacher["membership_labels"]:
        teacher["organization_label"] = teacher["membership_labels"][0]
    teacher["revision"] = teacher_account_revision(teacher)
    return teacher


def list_teacher_accounts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.email, t.phone, t.wechat, t.qq, t.homepage_url, t.description,
               t.school_code, t.school_name, t.college, t.department, t.employee_number,
               COALESCE(t.is_super_admin, 0) AS is_super_admin,
               COALESCE(t.is_active, 1) AS is_active,
               t.created_at, t.updated_at, t.password_updated_at, t.deactivated_at,
               t.cancelled_at, t.cancelled_by_teacher_id,
               (SELECT COUNT(*) FROM classes c WHERE c.created_by_teacher_id = t.id) AS class_count,
               (SELECT COUNT(*) FROM courses c WHERE c.created_by_teacher_id = t.id) AS course_count,
               (SELECT COUNT(*) FROM class_offerings o WHERE o.teacher_id = t.id) AS offering_count,
               (SELECT COUNT(*) FROM course_materials m WHERE m.teacher_id = t.id AND m.name != '.git') AS material_count,
               (
                   SELECT COUNT(*)
                   FROM student_password_reset_requests r
                   JOIN classes c ON c.id = r.class_id
                   WHERE r.teacher_id = t.id
                     AND c.created_by_teacher_id = t.id
                     AND r.status = 'pending'
               ) AS pending_reset_count
        FROM teachers t
        ORDER BY COALESCE(t.is_active, 1) DESC,
                 COALESCE(t.is_super_admin, 0) DESC,
                 t.created_at DESC,
                 t.id DESC
        """
    ).fetchall()
    return [_attach_teacher_memberships(conn, _serialize_teacher_account(row)) for row in rows]


def build_teacher_account_summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN COALESCE(is_active, 1) = 1 THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN COALESCE(is_active, 1) = 0 AND COALESCE(cancelled_at, '') = '' THEN 1 ELSE 0 END) AS inactive_count,
            SUM(CASE WHEN COALESCE(cancelled_at, '') <> '' THEN 1 ELSE 0 END) AS cancelled_count,
            SUM(CASE WHEN COALESCE(is_active, 1) = 1 AND COALESCE(is_super_admin, 0) = 1 THEN 1 ELSE 0 END) AS super_admin_count
        FROM teachers
        """
    ).fetchone()
    return {
        "total_count": int((row["total_count"] if row else 0) or 0),
        "active_count": int((row["active_count"] if row else 0) or 0),
        "inactive_count": int((row["inactive_count"] if row else 0) or 0),
        "cancelled_count": int((row["cancelled_count"] if row else 0) or 0),
        "super_admin_count": int((row["super_admin_count"] if row else 0) or 0),
    }


def create_teacher_account(
    conn: sqlite3.Connection,
    *,
    actor_teacher_id: int,
    name: str,
    email: str,
    password: str,
    is_super_admin: bool = False,
    school_code: str = "",
    school_name: str = "",
    college: str = "",
    department: str = "",
    employee_number: str = "",
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    normalized_name, normalized_email = _validate_teacher_identity(name, email)
    validate_teacher_password(password)
    number = normalize_employee_number(employee_number, required=True)
    org_scope = canonicalize_membership_scope(
        conn,
        school_code=school_code,
        school_name=school_name,
        college=college,
        department=department,
    )
    existing = _teacher_exists_by_email(conn, normalized_email)
    if existing and existing["cancelled_at"]:
        raise ValueError("该邮箱对应的教师账号已注销，不能通过创建账号恢复；历史资料及原身份已保留。")
    if existing:
        if int(existing["is_active"] or 0) == 1:
            raise ValueError("该教师邮箱已存在。")
        raise ValueError("该邮箱对应的教师账号已停用，请从原教师卡片明确启用，不能通过新增覆盖历史账号。")
    assert_employee_number_available(
        conn, teacher_id=0,
        school_code=org_scope["school_code"], employee_number=number,
    )
    timestamp = _now_iso()
    teacher_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO teachers (
            name, email, hashed_password, password_updated_at,
            school_code, school_name, college, department,
            is_super_admin, is_active, created_by_teacher_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            normalized_name, normalized_email, get_password_hash(password), timestamp,
            org_scope["school_code"], org_scope["school_name"], org_scope["college"], org_scope["department"],
            1 if is_super_admin else 0, int(actor_teacher_id), timestamp,
        ),
    )

    upsert_teacher_membership(
        conn,
        teacher_id=teacher_id,
        school_code=org_scope["school_code"],
        school_name=org_scope["school_name"],
        college=org_scope["college"],
        department=org_scope["department"],
        is_primary=True,
        actor_teacher_id=actor_teacher_id,
        source="account_primary",
        employee_number=number,
    )
    return get_teacher_account(conn, teacher_id)


def get_teacher_account(
    conn: sqlite3.Connection, teacher_id: int | str, *, include_counts: bool = False,
) -> dict[str, Any]:
    row = _get_teacher_account_row(conn, teacher_id)
    if not row:
        raise ValueError("教师账号不存在。")
    teacher = _attach_teacher_memberships(conn, _serialize_teacher_account(row))
    if include_counts:
        counts = conn.execute(
            """SELECT
                (SELECT COUNT(*) FROM classes WHERE created_by_teacher_id = ?) AS class_count,
                (SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?) AS course_count,
                (SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?) AS offering_count,
                (SELECT COUNT(*) FROM course_materials WHERE teacher_id = ? AND name != '.git') AS material_count,
                (SELECT COUNT(*) FROM student_password_reset_requests r JOIN classes c ON c.id = r.class_id
                    WHERE r.teacher_id = ? AND c.created_by_teacher_id = r.teacher_id AND r.status = 'pending') AS pending_reset_count""",
            (int(teacher_id),) * 5,
        ).fetchone()
        teacher.update({name: int(counts[name] or 0) for name in counts.keys()})
    return teacher


def update_teacher_account(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    name: str,
    email: str,
    phone: str = "",
    wechat: str = "",
    qq: str = "",
    homepage_url: str = "",
    description: str = "",
    school_code: str = "",
    school_name: str = "",
    college: str = "",
    department: str = "",
    employee_number: str | None = None,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if not bool(target["is_active"]):
        raise ValueError("该教师账号已停用，不能修改资料。")
    number = normalize_employee_number(
        employee_number if employee_number is not None else target["employee_number"], required=True,
    )

    normalized_name, normalized_email = _validate_teacher_identity(name, email)
    email_conflict = _teacher_exists_by_email(conn, normalized_email, exclude_teacher_id=teacher_id)
    if email_conflict:
        raise ValueError("该教师邮箱已被其他账号使用。")
    current_scope = {
        "school_code": target["school_code"],
        "school_name": target["school_name"],
        "college": target["college"],
        "department": target["department"],
    }
    org_scope = canonicalize_membership_scope(
        conn,
        school_code=school_code or ("" if school_name and school_name != current_scope["school_name"] else current_scope["school_code"]),
        school_name=school_name or current_scope["school_name"],
        college=college if normalize_org_text(college) else current_scope["college"],
        department=department if normalize_org_text(department) else current_scope["department"],
    )

    conn.execute(
        """
        UPDATE teachers
        SET name = ?,
            email = ?,
            phone = ?,
            wechat = ?,
            qq = ?,
            homepage_url = ?,
            description = ?,
            school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            normalized_name,
            normalized_email,
            str(phone or "").strip(),
            str(wechat or "").strip(),
            str(qq or "").strip(),
            str(homepage_url or "").strip(),
            str(description or "").strip(),
            org_scope["school_code"],
            org_scope["school_name"],
            org_scope["college"],
            org_scope["department"],
            _now_iso(),
            int(teacher_id),
        ),
    )
    upsert_teacher_membership(
        conn,
        teacher_id=teacher_id,
        school_code=org_scope["school_code"],
        school_name=org_scope["school_name"],
        college=org_scope["college"],
        department=org_scope["department"],
        is_primary=True,
        source="account_primary",
        employee_number=number,
    )
    return get_teacher_account(conn, teacher_id)


def reset_teacher_password(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    password: str,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if not bool(target["is_active"]):
        raise ValueError("该教师账号已停用，不能重置密码。")
    validate_teacher_password(password)
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE teachers
        SET hashed_password = ?,
            password_updated_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (get_password_hash(password), timestamp, timestamp, int(teacher_id)),
    )
    return get_teacher_account(conn, teacher_id)


def grant_teacher_super_admin(conn: sqlite3.Connection, *, teacher_id: int) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if not bool(target["is_active"]):
        raise ValueError("已停用的教师账号不能授予超管权限。")
    conn.execute(
        "UPDATE teachers SET is_super_admin = 1, updated_at = ? WHERE id = ?",
        (_now_iso(), int(teacher_id)),
    )
    return get_teacher_account(conn, teacher_id)


def revoke_teacher_super_admin(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if not bool(target["is_super_admin"]):
        return get_teacher_account(conn, teacher_id)
    if _active_super_admin_count(conn) <= 1:
        raise ValueError("至少需要保留一名启用状态的超管教师。")
    conn.execute(
        "UPDATE teachers SET is_super_admin = 0, updated_at = ? WHERE id = ?",
        (_now_iso(), int(teacher_id)),
    )
    return get_teacher_account(conn, teacher_id)


def deactivate_teacher_account(
    conn: sqlite3.Connection,
    *,
    teacher_id: int,
    actor_teacher_id: int,
) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    if int(teacher_id) == int(actor_teacher_id):
        raise ValueError("不能停用当前登录的教师账号。")
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if not bool(target["is_active"]):
        return get_teacher_account(conn, teacher_id)
    if bool(target["is_super_admin"]) and _active_super_admin_count(conn) <= 1:
        raise ValueError("不能停用最后一名启用状态的超管教师。")
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE teachers
        SET is_active = 0,
            is_super_admin = 0,
            deactivated_at = ?,
            deactivated_by_teacher_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (timestamp, int(actor_teacher_id), timestamp, int(teacher_id)),
    )
    # Suspend login at the account boundary. Keeping membership flags preserves
    # precisely which schools were enabled when this account is reactivated.
    invalidate_organization_suggestions(conn)
    return get_teacher_account(conn, teacher_id)


def activate_teacher_account(conn, *, teacher_id: int, actor_teacher_id: int) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if target["cancelled_at"]:
        raise ValueError("教师账号已注销，不能重新启用。")
    if bool(target["is_active"]):
        return get_teacher_account(conn, teacher_id)
    timestamp = _now_iso()
    active = conn.execute(
        "SELECT id FROM teacher_organization_memberships WHERE teacher_id=? AND is_active=1 LIMIT 1",
        (int(teacher_id),),
    ).fetchone()
    if not active and target["deactivated_at"]:
        # Compatibility for the former account-delete path: only recover rows
        # that it deactivated, never a previously manually disabled membership.
        conn.execute(
            """UPDATE teacher_organization_memberships
               SET is_active=1, deactivated_at=NULL, updated_at=?, updated_by_teacher_id=?
               WHERE teacher_id=? AND deactivated_at=?""",
            (timestamp, int(actor_teacher_id), int(teacher_id), target["deactivated_at"]),
        )
    primary = conn.execute(
        """SELECT id FROM teacher_organization_memberships WHERE teacher_id=? AND is_active=1
           ORDER BY CASE WHEN school_code=? THEN 0 ELSE 1 END, is_primary DESC, id LIMIT 1""",
        (int(teacher_id), target["school_code"]),
    ).fetchone()
    if not primary:
        raise ValueError("没有可恢复的任教归属，请由管理员核对历史归属后处理。")
    conn.execute(
        """UPDATE teacher_organization_memberships
           SET is_primary=CASE WHEN id=? THEN 1 ELSE 0 END, updated_at=?
           WHERE teacher_id=?""",
        (primary["id"], timestamp, int(teacher_id)),
    )
    conn.execute(
        """UPDATE teachers SET is_active=1, is_super_admin=0, deactivated_at=NULL,
           deactivated_by_teacher_id=NULL, updated_at=? WHERE id=?""",
        (timestamp, int(teacher_id)),
    )
    _sync_teacher_primary_scope(conn, int(teacher_id))
    invalidate_organization_suggestions(conn)
    return get_teacher_account(conn, teacher_id)


def cancel_teacher_account(conn, *, teacher_id: int, actor_teacher_id: int) -> dict[str, Any]:
    lock_teacher_account_management(conn)
    if int(teacher_id) == int(actor_teacher_id):
        raise ValueError("不能注销当前登录的教师账号。")
    target = _get_teacher_account_row(conn, teacher_id)
    if not target:
        raise ValueError("教师账号不存在。")
    if target["cancelled_at"]:
        return get_teacher_account(conn, teacher_id)
    deactivate_teacher_account(conn, teacher_id=teacher_id, actor_teacher_id=actor_teacher_id)
    timestamp = _now_iso()
    conn.execute(
        """UPDATE teachers SET is_active=0, is_super_admin=0, cancelled_at=?,
           cancelled_by_teacher_id=?, updated_at=? WHERE id=?""",
        (timestamp, int(actor_teacher_id), timestamp, int(teacher_id)),
    )
    conn.execute(
        """UPDATE teacher_organization_memberships SET is_active=0,is_primary=0,
           deactivated_at=COALESCE(deactivated_at,?),updated_at=?,updated_by_teacher_id=?
           WHERE teacher_id=?""",
        (timestamp, timestamp, int(actor_teacher_id), int(teacher_id)),
    )
    invalidate_organization_suggestions(conn)
    return get_teacher_account(conn, teacher_id)
