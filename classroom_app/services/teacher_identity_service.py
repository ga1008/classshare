"""School-scoped teacher employee identities; employee numbers are always text."""
from __future__ import annotations

import re
import unicodedata


class TeacherIdentityConflict(ValueError):
    """A stale profile must not write an employee number to a different school."""


def normalize_employee_number(value, *, required: bool = False) -> str:
    number = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    if not number:
        if required:
            raise ValueError("请填写职工号。")
        return ""
    if len(number) > 64 or not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]*", number):
        raise ValueError("职工号须为不超过 64 位的字母、数字或 . _ / -，请勿填写空格。")
    return number


def assert_employee_number_available(conn, *, teacher_id: int, school_code: str, employee_number: str) -> None:
    if not employee_number:
        return
    conflict = conn.execute(
        """SELECT teacher_id FROM teacher_organization_memberships
           WHERE school_code = ? AND employee_number = ? AND teacher_id <> ?
           LIMIT 1""",
        (school_code, employee_number, int(teacher_id)),
    ).fetchone()
    if conflict:
        raise ValueError("该学校的职工号已被其他教师账号使用，请核对任教归属或联系管理员。")


def teacher_login_candidates(conn, identifier: str, *, school_name: str = "", school_code: str = ""):
    """A password never chooses among accounts sharing an employee number."""
    identifier = str(identifier or "").strip()
    if "@" in identifier:
        return conn.execute(
            "SELECT * FROM teachers WHERE lower(email) = ? AND COALESCE(is_active, 1) = 1",
            (identifier.lower(),),
        ).fetchall()
    try:
        number = normalize_employee_number(identifier, required=True)
    except ValueError:
        return []
    params = [number]
    scope_filter = ""
    if school_code.strip():
        scope_filter = " AND lower(m.school_code) = ?"
        params.append(school_code.strip().casefold())
    elif school_name.strip():
        scope_filter = " AND (lower(m.school_name) = ? OR lower(m.school_code) = ?)"
        params.extend([school_name.strip().casefold()] * 2)
    return conn.execute(
        f"""SELECT DISTINCT t.* FROM teachers t
            JOIN teacher_organization_memberships m ON m.teacher_id = t.id
            WHERE m.employee_number = ?
              AND COALESCE(t.is_active, 1) = 1 AND COALESCE(m.is_active, 1) = 1
              {scope_filter}""",
        params,
    ).fetchall()


def update_primary_employee_number(
    conn, *, teacher_id: int, employee_number: str, expected_school_code: str | None = None,
) -> None:
    """Update only the account's existing default school, never its authority."""
    from .teacher_account_service import lock_teacher_account_management
    lock_teacher_account_management(conn)
    number = normalize_employee_number(employee_number, required=True)
    teacher = conn.execute(
        "SELECT school_code FROM teachers WHERE id = ? AND COALESCE(is_active, 1) = 1",
        (int(teacher_id),),
    ).fetchone()
    if not teacher:
        raise ValueError("教师账号不存在或已停用。")
    if expected_school_code is not None and str(expected_school_code).strip() != str(teacher["school_code"]):
        raise TeacherIdentityConflict("默认任教学校已变化，请刷新页面核对学校后重新填写职工号。")
    membership = conn.execute(
        """SELECT id FROM teacher_organization_memberships
           WHERE teacher_id = ? AND school_code = ? AND COALESCE(is_active, 1) = 1""",
        (int(teacher_id), teacher["school_code"]),
    ).fetchone()
    if not membership:
        raise ValueError("默认任教归属尚未建立，请联系管理员补全学校信息。")
    assert_employee_number_available(
        conn, teacher_id=teacher_id, school_code=teacher["school_code"], employee_number=number,
    )
    conn.execute(
        "UPDATE teacher_organization_memberships SET employee_number = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (number, membership["id"]),
    )
    conn.execute(
        "UPDATE teachers SET employee_number = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (number, int(teacher_id)),
    )
