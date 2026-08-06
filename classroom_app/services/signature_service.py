from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import aiofiles
from fastapi import UploadFile

from ..config import SIGNATURES_DIR, SIGNATURES_LEGACY_DIRS
from ..db.connection import execute_insert_returning_id
from ..storage_paths import unique_paths
from . import signature_identity_service, signature_image_service
from .message_center_service import is_super_admin_teacher
from .organization_management_service import list_school_options
from .organization_scope_service import (
    build_org_scope,
    load_teacher_org_memberships,
    load_teacher_org_scope,
    normalize_college,
    normalize_department,
    normalize_org_text,
    normalize_school_code,
    normalize_school_name,
)


MAX_SIGNATURE_FILE_BYTES = 5 * 1024 * 1024
ALLOWED_SIGNATURE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
VALID_SUBJECT_ROLES = {"teacher", "student", "other", "system"}
VALID_SCOPE_LEVELS = {"personal", "department", "college", "platform"}


class SignatureServiceError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = message


def _clean_text(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _safe_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return "{}"
        return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "{}"


def _normalize_subject_role(value: Any, fallback: str = "teacher") -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_SUBJECT_ROLES else fallback


def _normalize_scope_level(value: Any, fallback: str = "college") -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "college":
        return "department"
    return normalized if normalized in VALID_SCOPE_LEVELS else fallback


def _actor_identity(actor: dict[str, Any]) -> tuple[str, int]:
    return str(actor.get("role") or ""), int(actor.get("id") or 0)


def build_signature_actor(conn: sqlite3.Connection, user: dict[str, Any]) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    try:
        user_id = int(user.get("id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if role not in {"teacher", "student"} or user_id <= 0:
        raise SignatureServiceError(403, "当前登录身份无效，请重新登录。")

    if role == "teacher":
        row = conn.execute(
            """
            SELECT id, name, email, school_code, school_name, college, department
            FROM teachers
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if not row:
            raise SignatureServiceError(403, "当前教师账号不存在或已失效。")
        memberships = load_teacher_org_memberships(conn, user_id)
        scope = load_teacher_org_scope(conn, user_id)
        name = _clean_text(row["name"] or user.get("name") or "教师", 80)
        return {
            "role": "teacher",
            "id": user_id,
            "name": name,
            "is_super_admin": is_super_admin_teacher(conn, user_id),
            "scope": scope,
            "memberships": memberships,
        }

    row = conn.execute(
        """
        SELECT id, name, school_code, school_name, college, department
        FROM students
        WHERE id = ?
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        raise SignatureServiceError(403, "当前学生账号不存在或已失效。")
    return {
        "role": "student",
        "id": user_id,
        "name": _clean_text(row["name"] or user.get("name") or "学生", 80),
        "is_super_admin": False,
        "scope": build_org_scope(
            school_code=row["school_code"],
            school_name=row["school_name"],
            college=row["college"],
            department=row["department"],
        ),
        "memberships": [],
    }


def _actor_memberships(actor: dict[str, Any]) -> list[dict[str, str]]:
    memberships = actor.get("memberships")
    if isinstance(memberships, list) and memberships:
        return [
            build_org_scope(
                school_code=item.get("school_code"),
                school_name=item.get("school_name"),
                college=item.get("college"),
                department=item.get("department"),
            )
            for item in memberships
            if isinstance(item, dict)
        ]
    scope = actor.get("scope") or {}
    return [
        build_org_scope(
            school_code=scope.get("school_code"),
            school_name=scope.get("school_name"),
            college=scope.get("college"),
            department=scope.get("department"),
        )
    ]


def _actor_membership_for_school(actor: dict[str, Any], school_code: Any) -> dict[str, str]:
    normalized_school = normalize_school_code(school_code)
    for scope in _actor_memberships(actor):
        if normalize_school_code(scope.get("school_code")) == normalized_school:
            return scope
    scope = actor.get("scope") or {}
    return build_org_scope(
        school_code=scope.get("school_code"),
        school_name=scope.get("school_name"),
        college=scope.get("college"),
        department=scope.get("department"),
    )


def _same_college(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    row_school = normalize_school_code(row["school_code"] if "school_code" in row.keys() else "")
    row_college = normalize_college(row["college"] if "college" in row.keys() else "")
    for scope in _actor_memberships(actor):
        if normalize_school_code(scope.get("school_code")) != row_school:
            continue
        actor_college = normalize_college(scope.get("college"))
        if actor_college and row_college and actor_college == row_college:
            return True
    return False


def _same_department(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    row_school = normalize_school_code(row["school_code"] if "school_code" in row.keys() else "")
    row_department = normalize_department(row["department"] if "department" in row.keys() else "")
    for scope in _actor_memberships(actor):
        if normalize_school_code(scope.get("school_code")) != row_school:
            continue
        actor_department = normalize_department(scope.get("department"))
        if actor_department and row_department and actor_department == row_department:
            return True
    return False


def _same_school(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    row_school = normalize_school_code(row["school_code"] if "school_code" in row.keys() else "")
    return any(normalize_school_code(scope.get("school_code")) == row_school for scope in _actor_memberships(actor))


def _is_owner(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    role, user_id = _actor_identity(actor)
    try:
        owner_id = int(row["owner_id"] or 0)
    except (TypeError, ValueError):
        owner_id = 0
    return str(row["owner_role"] or "") == role and owner_id == user_id


def _is_subject(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    role, user_id = _actor_identity(actor)
    try:
        subject_id = int(row["subject_id"] or 0)
    except (TypeError, ValueError, KeyError, IndexError):
        subject_id = 0
    return str(row["subject_role"] or "") == role and subject_id == user_id


def can_view_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    if bool(actor.get("is_super_admin")):
        return True
    if _is_owner(actor, row) or _is_subject(actor, row):
        return True
    role, _ = _actor_identity(actor)
    if not _same_school(actor, row):
        return False
    if role == "student":
        return _is_owner(actor, row)
    if role == "teacher":
        if str(row["owner_role"] or "") == "system" or str(row["scope_level"] or "") == "platform":
            return True
        # 组织字段越空可见范围越大：有系部→同系可见；无系部（院长等超系部
        # 身份）→同学院可见；连学院也空（校级）→全校教师可见。
        row_department = normalize_department(row["department"] if "department" in row.keys() else "")
        if row_department:
            return _same_department(actor, row)
        row_college = normalize_college(row["college"] if "college" in row.keys() else "")
        if row_college:
            return _same_college(actor, row)
        return _same_school(actor, row)
    return False


def _signature_request_state(
    conn: sqlite3.Connection | None,
    actor: dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
) -> dict[str, Any]:
    if conn is None or actor.get("role") not in {"teacher", "student"}:
        return {}
    requester_id = int(actor.get("id") or 0)
    if requester_id <= 0:
        return {}
    request_row = conn.execute(
        """
        SELECT id, status, requested_at, reviewed_at, review_note
        FROM signature_access_requests
        WHERE signature_id = ?
          AND requester_role = ?
          AND requester_id = ?
        ORDER BY requested_at DESC, id DESC
        LIMIT 1
        """,
        (int(row["id"]), actor.get("role") or "teacher", requester_id),
    ).fetchone()
    if not request_row:
        return {}
    return {
        "request_id": int(request_row["id"]),
        "request_status": request_row["status"] or "",
        "requested_at": request_row["requested_at"] or "",
        "reviewed_at": request_row["reviewed_at"] or "",
        "review_note": request_row["review_note"] or "",
    }


def _bulk_request_states(
    conn: sqlite3.Connection,
    actor: dict[str, Any],
    signature_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Latest access-request state per signature for this actor, one query."""
    if not signature_ids or actor.get("role") not in {"teacher", "student"}:
        return {}
    requester_id = int(actor.get("id") or 0)
    if requester_id <= 0:
        return {}
    placeholders = ",".join("?" for _ in signature_ids)
    rows = conn.execute(
        f"""
        SELECT id, signature_id, status, requested_at, reviewed_at, review_note
        FROM signature_access_requests
        WHERE signature_id IN ({placeholders})
          AND requester_role = ?
          AND requester_id = ?
        ORDER BY requested_at DESC, id DESC
        """,
        (*signature_ids, actor.get("role") or "teacher", requester_id),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        signature_id = int(row["signature_id"])
        if signature_id in result:
            continue  # ordered newest-first: first row per signature wins
        result[signature_id] = {
            "request_id": int(row["id"]),
            "request_status": row["status"] or "",
            "requested_at": row["requested_at"] or "",
            "reviewed_at": row["reviewed_at"] or "",
            "review_note": row["review_note"] or "",
        }
    return result


def is_stamp_signature(row: sqlite3.Row | dict[str, Any]) -> bool:
    """批语章（同意/已阅…）：共享文字签章，不属于任何个人。

    显式 kind='stamp' 之外，system-owned 且签名主体不是真人的行也按批语章
    对待（运行时兜底，不依赖启动迁移时机）；system-owned 的 autoCorrecting
    个人签名（subject 为师生）绝不算批语章。
    """
    kind = row["signature_kind"] if "signature_kind" in row.keys() else "personal"
    if str(kind or "personal") == "stamp":
        return True
    return (
        str(row["owner_role"] or "") == "system"
        and str(row["subject_role"] or "") not in {"teacher", "student"}
    )


def is_subject_bound(row: sqlite3.Row | dict[str, Any]) -> bool:
    """The signature is tied to a registered account that can review requests."""
    try:
        subject_id = int(row["subject_id"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        subject_id = 0
    return str(row["subject_role"] or "").strip().lower() in {"teacher", "student"} and subject_id > 0


def can_claim_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    """A registered account may claim an unbound signature bearing its own name."""
    role, user_id = _actor_identity(actor)
    if role not in {"teacher", "student"} or user_id <= 0:
        return False
    # autoCorrecting 迁入的个人签名虽是 system-owned，也应允许本人认领；
    # 只有批语章绝对不可认领。
    if is_subject_bound(row) or is_stamp_signature(row):
        return False
    subject_role = str(row["subject_role"] or "").strip().lower()
    if subject_role not in {role, "", "other"}:
        return False
    subject_name = _clean_text(row["subject_name"], 80)
    return bool(subject_name) and subject_name == _clean_text(actor.get("name"), 80)


def can_use_signature(
    actor: dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> bool:
    # Global/broad grants are intentionally forbidden.  A feature-specific
    # approved item is evaluated by signature_workflow_service at the exact
    # binding location.  This helper only represents unconditional direct use.
    if _is_owner(actor, row) or _is_subject(actor, row):
        return True
    # 仅批语章（同意/已阅…）对教师免申请直用。收紧点：system-owned 的
    # autoCorrecting 个人签名过去也全员直用——那是漏洞，现在必须走申请。
    return actor.get("role") == "teacher" and is_stamp_signature(row)


def can_request_signature_use(
    actor: dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> bool:
    if actor.get("role") not in {"teacher", "student"}:
        return False
    if can_use_signature(actor, row, conn):
        return False
    if not can_view_signature(actor, row):
        return False
    return _signature_request_state(conn, actor, row).get("request_status") != "pending"


def can_unbind_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    """The bound signer (or a super admin) may detach a wrong binding.

    Owner-and-signer-in-one keeps full control anyway, so unbinding is only
    surfaced when the binding could have been someone else's mistake.
    """
    if not is_subject_bound(row):
        return False
    if bool(actor.get("is_super_admin")):
        return True
    return _is_subject(actor, row) and not _is_owner(actor, row)


def can_delete_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    return bool(actor.get("is_super_admin")) or _is_owner(actor, row)


def can_edit_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    return bool(actor.get("is_super_admin")) or _is_owner(actor, row)


def _resolve_selected_school(conn: sqlite3.Connection, actor: dict[str, Any], school_code: str = "") -> dict[str, str]:
    actor_scope = actor.get("scope") or {}
    requested_code = normalize_school_code(school_code) if normalize_org_text(school_code) else ""
    if not bool(actor.get("is_super_admin")):
        if requested_code:
            for scope in _actor_memberships(actor):
                if normalize_school_code(scope.get("school_code")) == requested_code:
                    return build_org_scope(
                        school_code=scope.get("school_code"),
                        school_name=scope.get("school_name"),
                    )
            raise SignatureServiceError(403, "当前教师无权查看该学校的签名。")
        return build_org_scope(
            school_code=actor_scope.get("school_code"),
            school_name=actor_scope.get("school_name"),
        )

    if requested_code:
        row = conn.execute(
            """
            SELECT school_code, school_name
            FROM organization_schools
            WHERE school_code = ?
            LIMIT 1
            """,
            (requested_code,),
        ).fetchone()
        if row:
            return build_org_scope(school_code=row["school_code"], school_name=row["school_name"])
        signature_row = conn.execute(
            """
            SELECT school_code, school_name
            FROM electronic_signatures
            WHERE school_code = ?
            LIMIT 1
            """,
            (requested_code,),
        ).fetchone()
        if signature_row:
            return build_org_scope(school_code=signature_row["school_code"], school_name=signature_row["school_name"])
        raise SignatureServiceError(404, "学校不存在或尚未纳入组织目录。")

    actor_school = normalize_school_code(actor_scope.get("school_code"))
    if actor_school:
        return build_org_scope(
            school_code=actor_school,
            school_name=actor_scope.get("school_name"),
        )
    options = list_school_options(conn, limit=1)
    if options:
        return build_org_scope(
            school_code=options[0]["school_code"],
            school_name=options[0]["school_name"],
        )
    return build_org_scope()


def _visibility_sql(actor: dict[str, Any], selected_school_code: str = "") -> tuple[str, list[Any]]:
    if bool(actor.get("is_super_admin")):
        selected_school_code = normalize_school_code(selected_school_code)
        return "s.school_code = ?", [selected_school_code]

    role, user_id = _actor_identity(actor)
    scope = actor.get("scope") or {}
    school_code = normalize_school_code(scope.get("school_code"))
    if role == "student":
        # Students see the signatures they hold, every signature that IS theirs
        # (teacher-harvested images keep the student as subject), plus unbound
        # same-school signatures bearing their name so they can claim them.
        actor_name = _clean_text(actor.get("name"), 80)
        return (
            "((s.owner_role = 'student' AND s.owner_id = ?)"
            " OR (s.subject_role = 'student' AND s.subject_id = ?)"
            " OR (s.school_code = ? AND COALESCE(s.subject_id, 0) <= 0"
            "     AND s.subject_role IN ('student', '', 'other') AND s.subject_name = ?))",
            [user_id, user_id, school_code, actor_name],
        )

    memberships = _actor_memberships(actor)
    if normalize_org_text(selected_school_code):
        selected = normalize_school_code(selected_school_code)
        memberships = [item for item in memberships if normalize_school_code(item.get("school_code")) == selected]
    school_codes = sorted(
        {
            normalize_school_code(item.get("school_code"))
            for item in memberships
            if normalize_school_code(item.get("school_code"))
        }
    )
    department_pairs = sorted(
        {
            (normalize_school_code(item.get("school_code")), normalize_department(item.get("department")))
            for item in memberships
            if normalize_school_code(item.get("school_code")) and normalize_department(item.get("department"))
        }
    )

    clauses: list[str] = []
    params: list[Any] = []
    if school_codes:
        placeholders = ", ".join("?" for _ in school_codes)
        clauses.append(f"(s.owner_role = 'teacher' AND s.owner_id = ? AND s.school_code IN ({placeholders}))")
        params.extend([user_id, *school_codes])
    else:
        clauses.append("(s.owner_role = 'teacher' AND s.owner_id = ?)")
        params.append(user_id)
    college_pairs = sorted(
        {
            (normalize_school_code(item.get("school_code")), normalize_college(item.get("college")))
            for item in memberships
            if normalize_school_code(item.get("school_code")) and normalize_college(item.get("college"))
        }
    )
    for item_school_code, department in department_pairs:
        clauses.append("(s.school_code = ? AND s.department = ? AND s.owner_role IN ('teacher', 'student', 'system'))")
        params.extend([item_school_code, department])
    # 无系部的签名（院长等超系部身份）按学院可见；连学院也空的按学校可见。
    for item_school_code, college in college_pairs:
        clauses.append(
            "(s.school_code = ? AND COALESCE(s.department, '') = '' AND s.college = ?"
            " AND s.owner_role IN ('teacher', 'student', 'system'))"
        )
        params.extend([item_school_code, college])
    for item_school_code in school_codes:
        clauses.append(
            "(s.school_code = ? AND COALESCE(s.department, '') = '' AND COALESCE(s.college, '') = ''"
            " AND s.owner_role IN ('teacher', 'student', 'system'))"
        )
        params.append(item_school_code)
    # can_view_signature grants teachers same-school access to platform assets;
    # the list query must agree or platform stamps vanish from pickers.
    for item_school_code in school_codes:
        clauses.append("(s.school_code = ? AND (s.owner_role = 'system' OR s.scope_level = 'platform'))")
        params.append(item_school_code)
    return "(" + " OR ".join(clauses) + ")", params


def _base_signature_select() -> str:
    return """
        SELECT
            s.*,
            COALESCE(
                CASE
                    WHEN s.owner_role = 'teacher' THEN ot.name
                    WHEN s.owner_role = 'student' THEN os.name
                    ELSE NULL
                END,
                NULLIF(s.owner_name_snapshot, ''),
                '平台导入'
            ) AS owner_display_name,
            COALESCE(
                CASE
                    WHEN s.uploaded_by_role = 'teacher' THEN ut.name
                    WHEN s.uploaded_by_role = 'student' THEN us.name
                    ELSE NULL
                END,
                NULLIF(s.uploaded_by_name_snapshot, ''),
                NULLIF(s.owner_name_snapshot, ''),
                '平台导入'
            ) AS uploaded_by_display_name,
            COALESCE(usage_stats.usage_count, 0) AS usage_count,
            usage_stats.last_used_at AS last_used_at
        FROM electronic_signatures s
        LEFT JOIN teachers ot ON s.owner_role = 'teacher' AND ot.id = s.owner_id
        LEFT JOIN students os ON s.owner_role = 'student' AND os.id = s.owner_id
        LEFT JOIN teachers ut ON s.uploaded_by_role = 'teacher' AND ut.id = s.uploaded_by_id
        LEFT JOIN students us ON s.uploaded_by_role = 'student' AND us.id = s.uploaded_by_id
        LEFT JOIN (
            SELECT signature_id, COUNT(*) AS usage_count, MAX(created_at) AS last_used_at
            FROM signature_usage_logs
            GROUP BY signature_id
        ) usage_stats ON usage_stats.signature_id = s.id
    """


def _signature_school_options(conn: sqlite3.Connection, actor: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    if bool(actor.get("is_super_admin")):
        return [
            {
                "school_code": item["school_code"],
                "school_name": item["school_name"],
                "is_active": item.get("is_active", True),
                "reference_count": item.get("reference_count", 0),
            }
            for item in list_school_options(conn, query=query, limit=120)
        ]
    query_text = normalize_org_text(query).casefold()
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope in _actor_memberships(actor):
        school = build_org_scope(
            school_code=scope.get("school_code"),
            school_name=scope.get("school_name"),
        )
        school_code = normalize_school_code(school.get("school_code"))
        if school_code in seen:
            continue
        if query_text and query_text not in school_code.casefold() and query_text not in school["school_name"].casefold():
            continue
        seen.add(school_code)
        options.append({
            "school_code": school["school_code"],
            "school_name": school["school_name"],
            "is_active": True,
            "reference_count": 0,
        })
    return options


def list_signatures(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    search: str = "",
    school_code: str = "",
    owner_role: str = "",
    subject_role: str = "",
    scope: str = "",
    identity_category: str = "",
    function_point_key: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    selected_school = _resolve_selected_school(conn, actor, school_code)
    explicit_school_filter = selected_school.get("school_code") if bool(actor.get("is_super_admin")) or normalize_org_text(school_code) else ""
    visibility_sql, params = _visibility_sql(actor, explicit_school_filter)
    where = ["s.status = 'active'", "s.deleted_at IS NULL", visibility_sql]

    query = _clean_text(search, 80)
    if query:
        like = f"%{query}%"
        where.append(
            """
            (
                s.name LIKE ?
                OR s.subject_name LIKE ?
                OR s.owner_name_snapshot LIKE ?
                OR s.uploaded_by_name_snapshot LIKE ?
                OR ot.name LIKE ?
                OR os.name LIKE ?
                OR ut.name LIKE ?
                OR us.name LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like, like, like])

    normalized_owner_role = str(owner_role or "").strip().lower()
    if normalized_owner_role in {"teacher", "student", "system"}:
        where.append("s.owner_role = ?")
        params.append(normalized_owner_role)

    normalized_subject_role = str(subject_role or "").strip().lower()
    if normalized_subject_role in VALID_SUBJECT_ROLES:
        where.append("s.subject_role = ?")
        params.append(normalized_subject_role)

    identity_keys = signature_identity_service.expand_identity_filter(identity_category)
    if identity_keys:
        placeholders = ", ".join("?" for _ in identity_keys)
        where.append(f"COALESCE(s.identity_category, '') IN ({placeholders})")
        params.extend(identity_keys)

    normalized_scope = str(scope or "").strip().lower()
    actor_role, actor_id = _actor_identity(actor)
    if normalized_scope == "mine":
        where.append("s.owner_role = ? AND s.owner_id = ?")
        params.extend([actor_role, actor_id])
    elif normalized_scope in {"college", "department"} and actor_role == "teacher":
        department_pairs = [
            (normalize_school_code(item.get("school_code")), normalize_department(item.get("department")))
            for item in _actor_memberships(actor)
            if normalize_school_code(item.get("school_code")) and normalize_department(item.get("department"))
        ]
        if department_pairs:
            where.append(
                "("
                + " OR ".join("(s.school_code = ? AND s.department = ?)" for _ in department_pairs)
                + ")"
            )
            for item_school_code, department in department_pairs:
                params.extend([item_school_code, department])
    elif normalized_scope == "system":
        where.append("(s.owner_role = 'system' OR s.scope_level = 'platform')")

    where_sql = " AND ".join(f"({item})" for item in where)
    total = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM electronic_signatures s
            LEFT JOIN teachers ot ON s.owner_role = 'teacher' AND ot.id = s.owner_id
            LEFT JOIN students os ON s.owner_role = 'student' AND os.id = s.owner_id
            LEFT JOIN teachers ut ON s.uploaded_by_role = 'teacher' AND ut.id = s.uploaded_by_id
            LEFT JOIN students us ON s.uploaded_by_role = 'student' AND us.id = s.uploaded_by_id
            WHERE
            """
            + where_sql,
            list(params),
        ).fetchone()[0]
        or 0
    )

    bounded_limit = max(1, min(int(limit or 200), 500))
    sql = (
        _base_signature_select()
        + " WHERE "
        + where_sql
        + " ORDER BY s.created_at DESC, s.id DESC LIMIT ?"
    )
    params.append(bounded_limit)
    rows = conn.execute(sql, params).fetchall()
    request_states = _bulk_request_states(conn, actor, [int(row["id"]) for row in rows])
    items = [
        serialize_signature(
            row,
            actor,
            conn,
            function_point_key=function_point_key,
            request_state=request_states.get(int(row["id"]), {}),
        )
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "actor": serialize_signature_actor(actor),
        "selected_school": selected_school,
        "school_options": _signature_school_options(conn, actor),
        "stats": _build_signature_stats(items, actor),
    }


def serialize_signature_actor(actor: dict[str, Any]) -> dict[str, Any]:
    scope = actor.get("scope") or {}
    return {
        "role": actor.get("role"),
        "id": actor.get("id"),
        "name": actor.get("name"),
        "is_super_admin": bool(actor.get("is_super_admin")),
        "school_code": scope.get("school_code") or "",
        "school_name": scope.get("school_name") or "",
        "college": scope.get("college") or "",
        "department": scope.get("department") or "",
    }


def _build_signature_stats(items: list[dict[str, Any]], actor: dict[str, Any]) -> dict[str, Any]:
    department_total = sum(1 for item in items if item.get("scope_level") in {"college", "department"})
    return {
        "visible_total": len(items),
        "mine": sum(1 for item in items if item.get("is_owner")),
        "college": department_total,
        "department": department_total,
        "system": sum(1 for item in items if item.get("owner_role") == "system" or item.get("scope_level") == "platform"),
        "usage_total": sum(int(item.get("usage_count") or 0) for item in items),
        "can_upload": actor.get("role") in {"teacher", "student"},
    }


def _role_label(role: str) -> str:
    return {
        "teacher": "教师",
        "student": "学生",
        "system": "平台",
        "other": "其他",
    }.get(str(role or ""), "未分类")


def _scope_label(scope_level: str) -> str:
    return {
        "personal": "个人",
        "department": "系部可见",
        "college": "学院可用",
        "platform": "平台可用",
    }.get(str(scope_level or ""), "未分类")


def serialize_signature(
    row: sqlite3.Row,
    actor: dict[str, Any],
    conn: sqlite3.Connection | None = None,
    *,
    function_point_key: str = "",
    request_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row_id = int(row["id"])
    owner_role = str(row["owner_role"] or "")
    subject_role = str(row["subject_role"] or "")
    identity_category = signature_identity_service.normalize_identity_category(
        row["identity_category"] if "identity_category" in row.keys() else ""
    )
    scope_level = str(row["scope_level"] or "")
    subject_name = row["subject_name"] or row["name"]
    is_owner = _is_owner(actor, row)
    can_delete = can_delete_signature(actor, row)
    can_edit = can_edit_signature(actor, row)
    # List pages pass a prefetched state (one query per page, not per row).
    if request_state is None:
        request_state = _signature_request_state(conn, actor, row)
    can_view = can_view_signature(actor, row)
    can_use = can_use_signature(actor, row, conn)
    feature_access: dict[str, Any] = {}
    if function_point_key and conn is not None:
        from . import signature_workflow_service

        feature_access = signature_workflow_service.access_state(conn, actor, row, function_point_key)
        can_use = bool(feature_access.get("can_use"))
    return {
        "id": row_id,
        "name": row["name"],
        "subject_name": subject_name,
        "subject_role": subject_role,
        "subject_id": row["subject_id"],
        "subject_role_label": _role_label(subject_role),
        "identity_category": identity_category,
        "identity_label": signature_identity_service.identity_label(identity_category),
        "identity_verified": bool(row["identity_verified"] if "identity_verified" in row.keys() else 0),
        "signature_kind": "stamp" if is_stamp_signature(row) else "personal",
        "kind_label": "批语章" if is_stamp_signature(row) else "",
        "owner_role": owner_role,
        "owner_role_label": _role_label(owner_role),
        "owner_id": row["owner_id"],
        "owner_name": row["owner_display_name"],
        "uploaded_by_role": row["uploaded_by_role"] or owner_role,
        "uploaded_by_role_label": _role_label(row["uploaded_by_role"] or owner_role),
        "uploaded_by_id": row["uploaded_by_id"] if row["uploaded_by_id"] is not None else row["owner_id"],
        "uploaded_by_name": row["uploaded_by_display_name"],
        "scope_level": scope_level,
        "scope_label": _scope_label(scope_level),
        "school_code": row["school_code"],
        "school_name": row["school_name"],
        "college": row["college"],
        "department": row["department"],
        "file_hash": row["file_hash"],
        "file_ext": row["file_ext"],
        "mime_type": row["mime_type"],
        "file_size": int(row["file_size"] or 0),
        "description": row["description"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "usage_count": int(row["usage_count"] or 0),
        "last_used_at": row["last_used_at"] or "",
        "is_owner": is_owner,
        "subject_bound": is_subject_bound(row),
        "can_claim": can_claim_signature(actor, row),
        "can_edit": can_edit,
        "can_delete": can_delete,
        "can_view": can_view,
        "can_use": can_use,
        "can_request_use": bool(feature_access.get("can_request")) if feature_access else (
            # Inlined can_request_signature_use with the prefetched request
            # state, so listing 500 rows costs one query instead of 500.
            actor.get("role") in {"teacher", "student"}
            and not can_use
            and can_view
            and request_state.get("request_status") != "pending"
        ),
        "can_unbind": can_unbind_signature(actor, row),
        "function_point_access": feature_access,
        "request_id": request_state.get("request_id"),
        "request_status": request_state.get("request_status", ""),
        "requested_at": request_state.get("requested_at", ""),
        "reviewed_at": request_state.get("reviewed_at", ""),
        "request_review_note": request_state.get("review_note", ""),
        "image_url": f"/api/signatures/{row_id}/image",
        "download_url": f"/api/signatures/{row_id}/image?download=1",
        "legacy_source": row["legacy_source"] or "",
    }


def get_signature_row_for_actor(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
    *,
    require_use: bool = True,
) -> tuple[sqlite3.Row, dict[str, Any]]:
    actor = build_signature_actor(conn, user)
    row = conn.execute(
        _base_signature_select()
        + """
        WHERE s.id = ?
          AND s.status = 'active'
          AND s.deleted_at IS NULL
        LIMIT 1
        """,
        (int(signature_id),),
    ).fetchone()
    if not row:
        raise SignatureServiceError(404, "签名不存在或已删除。")
    allowed = can_use_signature(actor, row, conn) if require_use else can_view_signature(actor, row)
    if not allowed:
        raise SignatureServiceError(403, "当前账号无权访问此签名。")
    return row, actor


def _get_signature_row(conn: sqlite3.Connection, signature_id: int) -> sqlite3.Row:
    row = conn.execute(
        _base_signature_select()
        + """
        WHERE s.id = ?
          AND s.status = 'active'
          AND s.deleted_at IS NULL
        LIMIT 1
        """,
        (int(signature_id),),
    ).fetchone()
    if not row:
        raise SignatureServiceError(404, "签名不存在或已删除。")
    return row


def _teacher_owner_row(conn: sqlite3.Connection, teacher_id: int | str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, name, email, school_code, school_name, college, department
        FROM teachers
        WHERE id = ?
          AND COALESCE(is_active, 1) = 1
        LIMIT 1
        """,
        (int(teacher_id),),
    ).fetchone()
    if not row:
        raise SignatureServiceError(400, "目标归属教师不存在或已停用。")
    return row


def _resolve_subject_id(
    conn: sqlite3.Connection,
    *,
    subject_role: str,
    subject_name: str,
    explicit_id: Any,
    school_code: str,
) -> int | None:
    if subject_role not in {"teacher", "student"}:
        return None
    table = "teachers" if subject_role == "teacher" else "students"
    if explicit_id not in (None, ""):
        try:
            subject_id = int(explicit_id)
        except (TypeError, ValueError) as exc:
            raise SignatureServiceError(400, "签名者账号 ID 无效。") from exc
        row = conn.execute(
            f"SELECT id, name, school_code FROM {table} WHERE id = ? LIMIT 1",
            (subject_id,),
        ).fetchone()
        if not row:
            raise SignatureServiceError(400, "未找到对应的签名者账号。")
        if school_code and normalize_school_code(row["school_code"]) != normalize_school_code(school_code):
            raise SignatureServiceError(400, "签名者账号与签名所属学校不一致。")
        return int(row["id"])
    clean_name = _clean_text(subject_name, 80)
    if not clean_name:
        return None
    rows = conn.execute(
        f"""
        SELECT id FROM {table}
        WHERE LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(?))
          AND LOWER(TRIM(COALESCE(school_code, ''))) = LOWER(TRIM(?))
        ORDER BY id LIMIT 2
        """,
        (clean_name, normalize_school_code(school_code)),
    ).fetchall()
    return int(rows[0]["id"]) if len(rows) == 1 else None


def _subject_name_by_id(conn: sqlite3.Connection, subject_role: str, subject_id: int | None) -> str:
    table = "teachers" if subject_role == "teacher" else "students" if subject_role == "student" else ""
    if not table or not subject_id:
        return ""
    row = conn.execute(f"SELECT name FROM {table} WHERE id = ? LIMIT 1", (int(subject_id),)).fetchone()
    return _clean_text(row["name"] if row else "", 80)


def list_signature_teacher_options(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    q: str = "",
    school_code: str = "",
    limit: int = 60,
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    selected_school = _resolve_selected_school(conn, actor, school_code)
    params: list[Any] = [selected_school["school_code"]]
    where = ["COALESCE(is_active, 1) = 1", "school_code = ?"]
    query = _clean_text(q, 80)
    if query:
        like = f"%{query}%"
        where.append("(name LIKE ? OR email LIKE ? OR college LIKE ? OR department LIKE ?)")
        params.extend([like, like, like, like])
    rows = conn.execute(
        """
        SELECT id, name, email, school_code, school_name, college, department
        FROM teachers
        WHERE
        """
        + " AND ".join(f"({item})" for item in where)
        + """
        ORDER BY name COLLATE NOCASE ASC, id ASC
        LIMIT ?
        """,
        (*params, max(1, min(int(limit or 60), 120))),
    ).fetchall()
    return {
        "items": [
            {
                "id": int(row["id"]),
                "name": row["name"] or "",
                "email": row["email"] or "",
                "school_code": row["school_code"] or "",
                "school_name": row["school_name"] or "",
                "college": row["college"] or "",
                "department": row["department"] or "",
            }
            for row in rows
        ],
        "selected_school": selected_school,
        "actor": serialize_signature_actor(actor),
    }


def list_signature_school_options(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    q: str = "",
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    return {
        "items": _signature_school_options(conn, actor, query=q),
        "actor": serialize_signature_actor(actor),
    }


def update_signature_metadata(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    row = _get_signature_row(conn, signature_id)
    if not can_edit_signature(actor, row):
        raise SignatureServiceError(403, "只有签名归属人或超管可以修改此签名。")

    actor_role, actor_id = _actor_identity(actor)
    is_super_admin = bool(actor.get("is_super_admin"))
    owner_role = str(row["owner_role"] or "")
    owner_id = int(row["owner_id"] or 0) if row["owner_id"] is not None else None
    owner_name_snapshot = str(row["owner_name_snapshot"] or "")
    ownership_changed = False

    target_owner_id = payload.get("owner_teacher_id", payload.get("owner_id"))
    if target_owner_id not in (None, ""):
        target_teacher = _teacher_owner_row(conn, int(target_owner_id))
        target_scope = build_org_scope(
            school_code=target_teacher["school_code"],
            school_name=target_teacher["school_name"],
            college=target_teacher["college"],
            department=target_teacher["department"],
        )
        if not is_super_admin and target_scope["school_code"] != normalize_school_code((actor.get("scope") or {}).get("school_code")):
            matching_scope = next(
                (
                    scope
                    for scope in _actor_memberships(actor)
                    if normalize_school_code(scope.get("school_code")) == target_scope["school_code"]
                ),
                None,
            )
            if matching_scope:
                actor["scope"] = matching_scope
        if not is_super_admin:
            actor_school = normalize_school_code((actor.get("scope") or {}).get("school_code"))
            if target_scope["school_code"] != actor_school:
                raise SignatureServiceError(403, "只能把签名归属权转给同一学校的教师。")
        new_owner_role = "teacher"
        new_owner_id = int(target_teacher["id"])
        if owner_role != new_owner_role or int(owner_id or 0) != new_owner_id:
            ownership_changed = True
        owner_role = new_owner_role
        owner_id = new_owner_id
        owner_name_snapshot = _clean_text(target_teacher["name"], 80)
    else:
        target_teacher = None
        target_scope = None

    clean_name = _clean_text(payload.get("name", row["name"]), 80) or row["name"]
    clean_subject_name = _clean_text(payload.get("subject_name", row["subject_name"]), 80) or clean_name
    clean_description = _clean_text(payload.get("description", row["description"]), 300)

    if is_super_admin or actor_role == "teacher":
        subject_role = _normalize_subject_role(payload.get("subject_role", row["subject_role"]), row["subject_role"])
    else:
        subject_role = str(row["subject_role"] or actor_role)

    requested_scope_level = _normalize_scope_level(payload.get("scope_level", row["scope_level"]), row["scope_level"])
    if not is_super_admin and requested_scope_level == "platform":
        requested_scope_level = "department" if actor_role == "teacher" else "personal"

    current_org = build_org_scope(
        school_code=row["school_code"],
        school_name=row["school_name"],
        college=row["college"],
        department=row["department"],
    )
    if is_super_admin:
        requested_school_code = normalize_school_code(
            payload.get("school_code")
            or (target_scope["school_code"] if target_scope else "")
            or current_org["school_code"]
        )
        school_row = conn.execute(
            """
            SELECT school_code, school_name
            FROM organization_schools
            WHERE school_code = ?
            LIMIT 1
            """,
            (requested_school_code,),
        ).fetchone()
        school_name = normalize_school_name(
            payload.get("school_name")
            or (school_row["school_name"] if school_row else "")
            or (target_scope["school_name"] if target_scope else "")
            or current_org["school_name"]
        )
        org_scope = build_org_scope(
            school_code=requested_school_code,
            school_name=school_name,
            college=payload.get("college", target_scope["college"] if target_scope else current_org["college"]),
            department=payload.get("department", target_scope["department"] if target_scope else current_org["department"]),
        )
    else:
        actor_scope = target_scope or _actor_membership_for_school(actor, current_org["school_code"])
        org_scope = build_org_scope(
            school_code=actor_scope.get("school_code") or current_org["school_code"],
            school_name=actor_scope.get("school_name") or current_org["school_name"],
            college=actor_scope.get("college") or current_org["college"],
            department=actor_scope.get("department") or current_org["department"],
        )

    if requested_scope_level == "platform" and is_super_admin:
        org_scope["college"] = normalize_org_text(payload.get("college", org_scope["college"]))
        org_scope["department"] = normalize_org_text(payload.get("department", org_scope["department"]))

    # 超系部身份（院长/校长/教务老师…）不挂系部；只有教师/系主任/副系主任
    # 保留系部归属。清空后可见范围按组织层级放大（同学院/全校）。
    if not signature_identity_service.identity_requires_department(new_identity):
        org_scope["department"] = ""

    previous_identity = signature_identity_service.normalize_identity_category(
        row["identity_category"] if "identity_category" in row.keys() else ""
    )
    if "identity_category" in payload:
        new_identity = signature_identity_service.normalize_identity_category(payload.get("identity_category"))
    else:
        new_identity = previous_identity
    identity_changed = new_identity != previous_identity
    previous_verified = int(row["identity_verified"] if "identity_verified" in row.keys() else 0)
    if is_super_admin and "identity_category" in payload and new_identity:
        # A super admin stating the identity counts as verification.
        new_verified = 1
    elif identity_changed or not new_identity:
        new_verified = 0
    else:
        new_verified = previous_verified

    explicit_subject_id = payload.get("subject_id", payload.get("subject_teacher_id"))
    if (
        explicit_subject_id in (None, "")
        and subject_role == str(row["subject_role"] or "")
        and clean_subject_name == str(row["subject_name"] or "")
    ):
        subject_id = row["subject_id"]
    elif subject_role == actor_role and clean_subject_name == _clean_text(actor.get("name"), 80):
        subject_id = actor_id
    else:
        subject_id = _resolve_subject_id(
            conn,
            subject_role=subject_role,
            subject_name=clean_subject_name,
            explicit_id=explicit_subject_id,
            school_code=org_scope["school_code"],
        )
    if explicit_subject_id not in (None, "") and subject_id:
        clean_subject_name = _subject_name_by_id(conn, subject_role, int(subject_id)) or clean_subject_name

    conn.execute(
        """
        UPDATE electronic_signatures
        SET name = ?,
            subject_name = ?,
            subject_role = ?,
            subject_id = ?,
            identity_category = ?,
            identity_verified = ?,
            scope_level = ?,
            owner_role = ?,
            owner_id = ?,
            owner_name_snapshot = ?,
            ownership_updated_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE ownership_updated_at END,
            ownership_updated_by_teacher_id = CASE WHEN ? = 1 THEN ? ELSE ownership_updated_by_teacher_id END,
            school_code = ?,
            school_name = ?,
            college = ?,
            department = ?,
            description = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            clean_name,
            clean_subject_name,
            subject_role,
            subject_id,
            new_identity,
            new_verified,
            requested_scope_level,
            owner_role,
            owner_id,
            owner_name_snapshot,
            1 if ownership_changed else 0,
            1 if ownership_changed else 0,
            actor_id if actor_role == "teacher" else None,
            org_scope["school_code"],
            org_scope["school_name"],
            org_scope["college"],
            org_scope["department"],
            clean_description,
            int(signature_id),
        ),
    )
    if identity_changed and subject_id:
        # The signature side was edited last: push identity to the account.
        signature_identity_service.set_account_identity(conn, subject_role, subject_id, new_identity)
    else:
        signature_identity_service.sync_identity_for_signature(conn, int(signature_id))
    refreshed = _get_signature_row(conn, signature_id)
    return serialize_signature(refreshed, actor, conn)


async def _read_upload_bytes(file: UploadFile) -> bytes:
    data = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_SIGNATURE_FILE_BYTES:
            raise SignatureServiceError(400, "签名图片不能超过 5 MB。")
    await file.seek(0)
    if not data:
        raise SignatureServiceError(400, "请选择有效的签名图片。")
    return bytes(data)


def _detect_mime(data: bytes, ext: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    expected = ALLOWED_SIGNATURE_EXTENSIONS.get(ext)
    if expected:
        raise SignatureServiceError(400, "文件内容不是有效的 PNG/JPG 签名图片。")
    raise SignatureServiceError(400, "仅支持 PNG、JPG、JPEG 格式的签名图片。")


def _normalize_upload_extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_SIGNATURE_EXTENSIONS:
        raise SignatureServiceError(400, "仅支持 PNG、JPG、JPEG 格式的签名图片。")
    return ".jpg" if ext == ".jpeg" else ext


def signature_relative_path(file_hash: str, ext: str) -> Path:
    normalized_hash = str(file_hash or "").strip().lower()
    normalized_ext = ext if str(ext or "").startswith(".") else f".{ext}"
    if len(normalized_hash) >= 4:
        return Path(normalized_hash[:2]) / normalized_hash[2:4] / f"{normalized_hash}{normalized_ext}"
    return Path(f"{normalized_hash}{normalized_ext}")


def signature_write_path(file_hash: str, ext: str) -> Path:
    return SIGNATURES_DIR / signature_relative_path(file_hash, ext)


async def _store_signature_bytes(file_hash: str, ext: str, data: bytes) -> Path:
    target = signature_write_path(file_hash, ext)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return target
    temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        async with aiofiles.open(temp_path, "wb") as out_file:
            await out_file.write(data)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
    return target


def _owner_scope_for_upload(actor: dict[str, Any], scope_level: str) -> dict[str, str]:
    scope = dict(actor.get("scope") or {})
    if scope_level == "platform" and actor.get("is_super_admin"):
        scope["college"] = ""
        scope["department"] = ""
    return build_org_scope(
        school_code=scope.get("school_code"),
        school_name=scope.get("school_name"),
        college=scope.get("college"),
        department=scope.get("department"),
    )


async def create_signature_from_upload(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    file: UploadFile,
    *,
    name: str = "",
    subject_role: str = "",
    subject_name: str = "",
    subject_id: int | None = None,
    scope_level: str = "",
    identity_category: str = "",
    signature_kind: str = "",
    description: str = "",
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    # 批语章只有超管能登记；它是共享资产，不绑定个人。
    normalized_kind = "stamp" if (
        str(signature_kind or "").strip().lower() == "stamp" and actor.get("is_super_admin")
    ) else "personal"
    original_filename = file.filename or "signature.png"
    ext = _normalize_upload_extension(original_filename)
    data = await _read_upload_bytes(file)
    mime_type = _detect_mime(data, ext)
    if ALLOWED_SIGNATURE_EXTENSIONS[ext] != mime_type:
        raise SignatureServiceError(400, "文件扩展名与图片内容不一致。")
    # 自助上传统一规范化：校验非空白、裁掉留白、白底转透明、重编码 PNG。
    try:
        data, ext, mime_type = signature_image_service.normalize_upload_image(data)
    except signature_image_service.SignatureImageError as exc:
        raise SignatureServiceError(400, str(exc)) from exc

    actor_role, actor_id = _actor_identity(actor)

    if actor.get("is_super_admin"):
        normalized_scope = _normalize_scope_level(scope_level, "department")
        normalized_subject_role = _normalize_subject_role(subject_role, "teacher")
    else:
        normalized_subject_role = actor_role
        normalized_scope = "department" if actor_role == "teacher" else "personal"

    clean_name = _clean_text(name, 80) or _clean_text(Path(original_filename).stem, 80) or "电子签名"
    if not actor.get("is_super_admin"):
        # 自助上传只登记本人签名：签名人=账号真实姓名，自动绑定本人账号。
        # 他人签名必须走认领申请或超管上传。
        subject_id = actor_id
        clean_subject_name = _clean_text(actor.get("name"), 80) or clean_name
    else:
        clean_subject_name = _clean_text(subject_name, 80) or actor.get("name") or clean_name
    owner_scope = _owner_scope_for_upload(actor, normalized_scope)

    if not actor.get("is_super_admin"):
        duplicate = conn.execute(
            """
            SELECT id FROM electronic_signatures
            WHERE status = 'active' AND deleted_at IS NULL
              AND COALESCE(signature_kind, 'personal') <> 'stamp'
              AND LOWER(TRIM(COALESCE(subject_name, ''))) = LOWER(TRIM(?))
              AND LOWER(TRIM(COALESCE(school_code, ''))) = LOWER(TRIM(?))
              AND NOT (subject_role = ? AND COALESCE(subject_id, 0) = ?)
              AND NOT (owner_role = ? AND COALESCE(owner_id, 0) = ?)
            ORDER BY id LIMIT 1
            """,
            (
                clean_subject_name, normalize_school_code(owner_scope["school_code"]),
                actor_role, actor_id, actor_role, actor_id,
            ),
        ).fetchone()
        if duplicate:
            raise SignatureServiceError(
                409,
                f"签名库中已存在“{clean_subject_name}”的签名，请通过“认领签名”申请归属，认领成功后可更换签名图片，无需重复上传。",
            )

    file_hash = hashlib.sha256(data).hexdigest()
    target_path = await _store_signature_bytes(file_hash, ext, data)
    normalized_subject_id = (
        actor_id
        if normalized_subject_role == actor_role and clean_subject_name == _clean_text(actor.get("name"), 80)
        else _resolve_subject_id(
            conn,
            subject_role=normalized_subject_role,
            subject_name=clean_subject_name,
            explicit_id=subject_id,
            school_code=owner_scope["school_code"],
        )
    )
    if subject_id not in (None, "") and normalized_subject_id:
        clean_subject_name = _subject_name_by_id(conn, normalized_subject_role, normalized_subject_id) or clean_subject_name
    normalized_identity = signature_identity_service.normalize_identity_category(identity_category)
    identity_verified = 1 if (actor.get("is_super_admin") and normalized_identity) else 0
    if not normalized_identity and normalized_subject_id:
        normalized_identity = signature_identity_service.get_account_identity(
            conn, normalized_subject_role, normalized_subject_id
        )
    if normalized_kind == "stamp":
        # 批语章不属于任何人：不绑定账号、不挂身份，签名人即批语文字本身。
        normalized_subject_role = "other"
        normalized_subject_id = None
        clean_subject_name = clean_name
        normalized_identity = ""
        identity_verified = 0
    if not signature_identity_service.identity_requires_department(normalized_identity):
        owner_scope = {**owner_scope, "department": ""}

    signature_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO electronic_signatures (
            name, subject_name, subject_role, subject_id, identity_category, identity_verified, signature_kind, scope_level,
            owner_role, owner_id, owner_name_snapshot,
            uploaded_by_role, uploaded_by_id, uploaded_by_name_snapshot,
            school_code, school_name, college, department,
            file_hash, file_ext, mime_type, stored_path, file_size,
            description, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_name,
            clean_subject_name,
            normalized_subject_role,
            normalized_subject_id,
            normalized_identity,
            identity_verified,
            normalized_kind,
            normalized_scope,
            actor_role,
            actor_id,
            actor.get("name") or "",
            actor_role,
            actor_id,
            actor.get("name") or "",
            owner_scope["school_code"],
            owner_scope["school_name"],
            owner_scope["college"],
            owner_scope["department"],
            file_hash,
            ext,
            mime_type,
            str(signature_relative_path(file_hash, ext)).replace("\\", "/"),
            int(target_path.stat().st_size),
            _clean_text(description, 300),
            _safe_json({"original_filename": original_filename}),
        ),
    )
    signature_identity_service.sync_identity_for_signature(conn, signature_id)
    row, refreshed_actor = get_signature_row_for_actor(conn, user, signature_id)
    return serialize_signature(row, refreshed_actor, conn)


def find_owned_signature_by_hash(
    conn: sqlite3.Connection,
    actor: dict[str, Any],
    file_hash: str,
) -> sqlite3.Row | None:
    """Return an active signature with this hash already owned by the actor (dedup)."""
    actor_role, actor_id = _actor_identity(actor)
    if not file_hash or actor_id <= 0:
        return None
    return conn.execute(
        _base_signature_select()
        + """
        WHERE s.file_hash = ?
          AND s.owner_role = ?
          AND s.owner_id = ?
          AND s.status = 'active'
          AND s.deleted_at IS NULL
        ORDER BY s.created_at ASC, s.id ASC
        LIMIT 1
        """,
        (str(file_hash).strip().lower(), actor_role, actor_id),
    ).fetchone()


async def create_signature_from_bytes(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    data: bytes,
    *,
    ext: str = ".png",
    name: str = "",
    subject_role: str = "teacher",
    subject_name: str = "",
    scope_level: str = "",
    description: str = "",
    original_filename: str = "signature.png",
) -> dict[str, Any]:
    """Create a signature from raw bytes, **deduping by SHA-256 hash per owner**.

    Used by document importers (e.g. 考核计划表) to harvest embedded signature
    images into the library without producing duplicate rows on re-import.
    Returns the serialized signature plus a ``deduped`` flag.
    """
    actor = build_signature_actor(conn, user)
    normalized_ext = ".jpg" if str(ext or "").lower() in {".jpg", ".jpeg"} else str(ext or "").lower()
    if normalized_ext not in ALLOWED_SIGNATURE_EXTENSIONS:
        normalized_ext = ".png"
    if not data:
        raise SignatureServiceError(400, "签名图片数据为空。")
    if len(data) > MAX_SIGNATURE_FILE_BYTES:
        raise SignatureServiceError(400, "签名图片不能超过 5 MB。")
    mime_type = _detect_mime(data, normalized_ext)
    if ALLOWED_SIGNATURE_EXTENSIONS[normalized_ext] != mime_type:
        normalized_ext = ".png" if mime_type == "image/png" else ".jpg"

    file_hash = hashlib.sha256(data).hexdigest()
    existing = find_owned_signature_by_hash(conn, actor, file_hash)
    if existing is not None:
        return {**serialize_signature(existing, actor, conn), "deduped": True}

    target_path = await _store_signature_bytes(file_hash, normalized_ext, data)
    actor_role, actor_id = _actor_identity(actor)

    if actor.get("is_super_admin"):
        normalized_scope = _normalize_scope_level(scope_level, "department")
        normalized_subject_role = _normalize_subject_role(subject_role, "teacher")
    else:
        normalized_subject_role = _normalize_subject_role(subject_role, actor_role)
        normalized_scope = "department" if actor_role == "teacher" else "personal"

    clean_name = _clean_text(name, 80) or "导入签名"
    clean_subject_name = _clean_text(subject_name, 80) or clean_name
    owner_scope = _owner_scope_for_upload(actor, normalized_scope)
    normalized_subject_id = (
        actor_id
        if normalized_subject_role == actor_role and clean_subject_name == _clean_text(actor.get("name"), 80)
        else _resolve_subject_id(
            conn,
            subject_role=normalized_subject_role,
            subject_name=clean_subject_name,
            explicit_id=None,
            school_code=owner_scope["school_code"],
        )
    )

    signature_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO electronic_signatures (
            name, subject_name, subject_role, subject_id, scope_level,
            owner_role, owner_id, owner_name_snapshot,
            uploaded_by_role, uploaded_by_id, uploaded_by_name_snapshot,
            school_code, school_name, college, department,
            file_hash, file_ext, mime_type, stored_path, file_size,
            description, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_name,
            clean_subject_name,
            normalized_subject_role,
            normalized_subject_id,
            normalized_scope,
            actor_role,
            actor_id,
            actor.get("name") or "",
            actor_role,
            actor_id,
            actor.get("name") or "",
            owner_scope["school_code"],
            owner_scope["school_name"],
            owner_scope["college"],
            owner_scope["department"],
            file_hash,
            normalized_ext,
            mime_type,
            str(signature_relative_path(file_hash, normalized_ext)).replace("\\", "/"),
            int(target_path.stat().st_size),
            _clean_text(description, 300),
            _safe_json({"original_filename": original_filename, "source": "document_import"}),
        ),
    )
    row, refreshed_actor = get_signature_row_for_actor(conn, user, signature_id)
    return {**serialize_signature(row, refreshed_actor, conn), "deduped": False}


def _candidate_signature_paths(row: sqlite3.Row | dict[str, Any]) -> tuple[Path, ...]:
    roots = unique_paths((SIGNATURES_DIR, *SIGNATURES_LEGACY_DIRS))
    stored_path = str(row["stored_path"] or "").strip()
    candidates: list[Path] = []
    if stored_path:
        direct_path = Path(stored_path)
        candidates.append(direct_path)
        normalized = stored_path.replace("\\", "/").strip("/")
        if normalized and not direct_path.is_absolute():
            relative_parts = PurePosixPath(normalized).parts
            candidates.extend(root.joinpath(*relative_parts) for root in roots)

    file_hash = str(row["file_hash"] or "").strip().lower()
    file_ext = str(row["file_ext"] or "").strip().lower()
    if file_hash and file_ext:
        rel_path = signature_relative_path(file_hash, file_ext)
        candidates.extend(root / rel_path for root in roots)
        candidates.extend(root / f"{file_hash}{file_ext}" for root in roots)
    return unique_paths(candidates)


def resolve_signature_file_path(row: sqlite3.Row | dict[str, Any]) -> Path | None:
    for candidate in _candidate_signature_paths(row):
        if candidate.is_file():
            return candidate
    return None


def delete_signature(conn: sqlite3.Connection, user: dict[str, Any], signature_id: int) -> dict[str, Any]:
    row, actor = get_signature_row_for_actor(conn, user, signature_id)
    if not can_delete_signature(actor, row):
        raise SignatureServiceError(403, "只有签名归属人或超管可以删除此签名。")
    conn.execute(
        """
        UPDATE electronic_signatures
        SET status = 'deleted',
            deleted_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (int(signature_id),),
    )
    active_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM electronic_signatures
            WHERE file_hash = ?
              AND status = 'active'
              AND deleted_at IS NULL
            """,
            (row["file_hash"],),
        ).fetchone()[0]
        or 0
    )
    removed_file = False
    if active_count == 0:
        file_path = resolve_signature_file_path(row)
        if file_path and file_path.is_file():
            try:
                file_path.unlink()
                removed_file = True
            except OSError:
                removed_file = False
    return {"id": int(signature_id), "removed_file": removed_file}


async def replace_signature_image(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
    file: UploadFile,
) -> dict[str, Any]:
    """Swap the image behind an owned/bound signature (认领后可更换签名图片)."""
    actor = build_signature_actor(conn, user)
    row = _get_signature_row(conn, signature_id)
    if not (bool(actor.get("is_super_admin")) or _is_owner(actor, row) or _is_subject(actor, row)):
        raise SignatureServiceError(403, "只有签名归属人、签名者本人或超管可以更换签名图片。")
    original_filename = file.filename or "signature.png"
    ext = _normalize_upload_extension(original_filename)
    data = await _read_upload_bytes(file)
    mime_type = _detect_mime(data, ext)
    if ALLOWED_SIGNATURE_EXTENSIONS[ext] != mime_type:
        raise SignatureServiceError(400, "文件扩展名与图片内容不一致。")
    try:
        data, ext, mime_type = signature_image_service.normalize_upload_image(data)
    except signature_image_service.SignatureImageError as exc:
        raise SignatureServiceError(400, str(exc)) from exc
    file_hash = hashlib.sha256(data).hexdigest()
    target_path = await _store_signature_bytes(file_hash, ext, data)
    active_bindings = count_active_signature_bindings(conn, signature_id)
    conn.execute(
        """
        INSERT INTO signature_image_versions (
            signature_id, old_file_hash, old_file_ext, new_file_hash, new_file_ext,
            active_binding_count, changed_by_role, changed_by_id, changed_by_name_snapshot
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(signature_id),
            row["file_hash"] or "",
            row["file_ext"] or "",
            file_hash,
            ext,
            active_bindings,
            actor.get("role") or "",
            int(actor.get("id") or 0),
            _clean_text(actor.get("name"), 80),
        ),
    )
    conn.execute(
        """
        UPDATE electronic_signatures
        SET file_hash = ?, file_ext = ?, mime_type = ?, stored_path = ?, file_size = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            file_hash,
            ext,
            mime_type,
            str(signature_relative_path(file_hash, ext)).replace("\\", "/"),
            int(target_path.stat().st_size),
            int(signature_id),
        ),
    )
    _notify_image_replaced(conn, actor, row, active_bindings)
    refreshed = _get_signature_row(conn, signature_id)
    return {**serialize_signature(refreshed, actor, conn), "active_binding_count": active_bindings}


def count_active_signature_bindings(conn: sqlite3.Connection, signature_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM signature_point_bindings WHERE signature_id = ?",
            (int(signature_id),),
        ).fetchone()[0]
        or 0
    )


def _notify_image_replaced(
    conn: sqlite3.Connection,
    actor: dict[str, Any],
    row: sqlite3.Row,
    active_bindings: int,
) -> None:
    """Tell the signer/owner and every binder that re-exports now embed a new image."""
    from . import signature_workflow_service

    recipients = signature_workflow_service._reviewer_identities(conn, row)
    binder_rows = conn.execute(
        """
        SELECT DISTINCT bound_by_role AS role, bound_by_id AS id
        FROM signature_point_bindings
        WHERE signature_id = ?
        """,
        (int(row["id"]),),
    ).fetchall()
    recipients.extend(
        {"role": str(binder["role"] or ""), "id": int(binder["id"] or 0)} for binder in binder_rows
    )
    suffix = f"；该签名当前被 {active_bindings} 处材料签名点引用，重新导出将使用新图片" if active_bindings else ""
    signature_workflow_service._notify(
        conn,
        recipients=recipients,
        actor=actor,
        title="签名图片已更换",
        body=f"{actor.get('name')} 更换了签名“{row['subject_name'] or row['name']}”的图片{suffix}。",
        ref_type="signature_image_replaced",
        ref_id=str(int(row["id"])),
        metadata={"signature_id": int(row["id"]), "active_binding_count": active_bindings},
    )


def get_signature_refs(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
) -> dict[str, Any]:
    """Reference counts shown before destructive actions (delete / replace image)."""
    row, _actor = get_signature_row_for_actor(conn, user, signature_id, require_use=False)
    pending_requests = int(
        conn.execute(
            "SELECT COUNT(*) FROM signature_access_requests WHERE signature_id = ? AND status = 'pending'",
            (int(signature_id),),
        ).fetchone()[0]
        or 0
    )
    return {
        "signature_id": int(signature_id),
        "active_binding_count": count_active_signature_bindings(conn, signature_id),
        "pending_request_count": pending_requests,
    }


def list_claim_candidates(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    q: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Name-only listing of same-school signatures a user may apply to claim.

    Deliberately returns no image URLs: the claim picker shows names, identity
    and binding state only, so browsing it never exposes autograph images.
    """
    actor = build_signature_actor(conn, user)
    actor_role, actor_id = _actor_identity(actor)
    scope = actor.get("scope") or {}
    school_code = normalize_school_code(scope.get("school_code"))
    where = [
        "s.status = 'active'",
        "s.deleted_at IS NULL",
        # system-owned personal autographs (autoCorrecting import) belong in
        # the claim list — they are exactly the rows people need to claim.
        "COALESCE(s.signature_kind, 'personal') <> 'stamp'",
        "COALESCE(s.scope_level, '') <> 'platform'",
        "LOWER(TRIM(COALESCE(s.school_code, ''))) = LOWER(TRIM(?))",
        "NOT (s.subject_role = ? AND COALESCE(s.subject_id, 0) = ?)",
    ]
    params: list[Any] = [school_code, actor_role, actor_id]
    query = _clean_text(q, 80)
    if query:
        like = f"%{query}%"
        where.append("(s.subject_name LIKE ? OR s.name LIKE ?)")
        params.extend([like, like])
    rows = conn.execute(
        _base_signature_select()
        + " WHERE "
        + " AND ".join(f"({item})" for item in where)
        + " ORDER BY s.subject_name COLLATE NOCASE ASC, s.id ASC LIMIT ?",
        (*params, max(1, min(int(limit or 200), 500))),
    ).fetchall()
    pending_rows = conn.execute(
        """
        SELECT signature_id FROM signature_access_requests
        WHERE requester_role = ? AND requester_id = ?
          AND COALESCE(request_kind, 'use') = 'claim' AND status = 'pending'
        """,
        (actor_role, actor_id),
    ).fetchall()
    pending_ids = {int(row["signature_id"]) for row in pending_rows}
    items = []
    for row in rows:
        identity = signature_identity_service.normalize_identity_category(
            row["identity_category"] if "identity_category" in row.keys() else ""
        )
        items.append(
            {
                "id": int(row["id"]),
                "subject_name": row["subject_name"] or row["name"] or "",
                "identity_category": identity,
                "identity_label": signature_identity_service.identity_label(identity),
                "subject_bound": is_subject_bound(row),
                "owner_name": row["owner_display_name"] or "",
                "is_owner": _is_owner(actor, row),
                "can_direct_claim": can_claim_signature(actor, row),
                "has_pending_claim": int(row["id"]) in pending_ids,
                "created_at": row["created_at"] or "",
            }
        )
    return {"items": items, "actor": serialize_signature_actor(actor)}


def merge_duplicate_signatures(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    primary_id: int,
    duplicate_ids: list[int],
) -> dict[str, Any]:
    """Fold duplicate same-name signature rows into one primary (超管工具).

    Document imports keep producing new rows for the same person. Merging
    repoints bindings / flow items / usage logs to the primary, cancels the
    duplicates' pending requests (the unique pending index would collide on a
    repoint), migrates an account binding the primary lacks, and soft-deletes
    the duplicates with a ``merged:<primary>`` marker. Image files stay on
    disk — exported documents may still reference them.
    """
    actor = build_signature_actor(conn, user)
    if not bool(actor.get("is_super_admin")):
        raise SignatureServiceError(403, "只有超级管理员可以归并签名。")
    primary = _get_signature_row(conn, primary_id)
    if str(primary["owner_role"] or "") == "system" or is_stamp_signature(primary):
        raise SignatureServiceError(400, "平台公共签章/批语章不参与归并。")
    primary_name = _clean_text(primary["subject_name"] or primary["name"], 80)
    normalized_ids: list[int] = []
    seen: set[int] = {int(primary_id)}
    for value in duplicate_ids or []:
        try:
            duplicate_id = int(value)
        except (TypeError, ValueError):
            continue
        if duplicate_id > 0 and duplicate_id not in seen:
            seen.add(duplicate_id)
            normalized_ids.append(duplicate_id)
    if not normalized_ids:
        raise SignatureServiceError(400, "请选择至少一个要并入的重复签名。")
    if len(normalized_ids) > 20:
        raise SignatureServiceError(400, "一次最多归并 20 个签名。")

    from . import signature_workflow_service

    merged = 0
    for duplicate_id in normalized_ids:
        duplicate = _get_signature_row(conn, duplicate_id)
        if str(duplicate["owner_role"] or "") == "system" or is_stamp_signature(duplicate):
            raise SignatureServiceError(400, "平台公共签章/批语章不参与归并。")
        duplicate_name = _clean_text(duplicate["subject_name"] or duplicate["name"], 80)
        if duplicate_name != primary_name:
            raise SignatureServiceError(400, f"“{duplicate_name}”与主签名姓名不一致，仅同名签名可归并。")
        if normalize_school_code(duplicate["school_code"]) != normalize_school_code(primary["school_code"]):
            raise SignatureServiceError(400, "仅同一学校的签名可归并。")
        if (
            is_subject_bound(duplicate)
            and is_subject_bound(primary)
            and (
                str(duplicate["subject_role"]) != str(primary["subject_role"])
                or int(duplicate["subject_id"] or 0) != int(primary["subject_id"] or 0)
            )
        ):
            raise SignatureServiceError(
                422, "主签名与重复签名绑定了不同账号，归并会造成归属混乱，请先解绑其一。"
            )
        if is_subject_bound(duplicate) and not is_subject_bound(primary):
            conn.execute(
                """
                UPDATE electronic_signatures
                SET subject_role = ?, subject_id = ?, subject_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(duplicate["subject_role"]),
                    int(duplicate["subject_id"]),
                    duplicate["subject_name"] or primary_name,
                    int(primary_id),
                ),
            )
            signature_identity_service.sync_identity_for_signature(conn, int(primary_id))
            primary = _get_signature_row(conn, primary_id)

        # Pending requests cannot be repointed (unique pending index) — end them.
        pending_rows = conn.execute(
            "SELECT id FROM signature_access_requests WHERE signature_id = ? AND status = 'pending'",
            (duplicate_id,),
        ).fetchall()
        for pending in pending_rows:
            pending_id = int(pending["id"])
            conn.execute(
                """
                UPDATE signature_access_requests
                SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP,
                    review_note = '签名已归并到同名主签名，请对主签名重新申请。'
                WHERE id = ? AND status = 'pending'
                """,
                (pending_id,),
            )
            conn.execute(
                "UPDATE signature_access_request_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE request_id = ? AND status = 'pending'",
                (pending_id,),
            )
            conn.execute(
                "UPDATE signature_access_request_reviewers SET status = 'cancelled' WHERE request_id = ? AND status = 'pending'",
                (pending_id,),
            )
        conn.execute(
            "UPDATE signature_access_requests SET signature_id = ? WHERE signature_id = ? AND status <> 'cancelled'",
            (int(primary_id), duplicate_id),
        )
        conn.execute(
            "UPDATE signature_point_bindings SET signature_id = ? WHERE signature_id = ?",
            (int(primary_id), duplicate_id),
        )
        conn.execute(
            "UPDATE signature_point_flow_items SET signature_id = ? WHERE signature_id = ?",
            (int(primary_id), duplicate_id),
        )
        conn.execute(
            "UPDATE signature_usage_logs SET signature_id = ? WHERE signature_id = ?",
            (int(primary_id), duplicate_id),
        )
        conn.execute(
            """
            UPDATE electronic_signatures
            SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP,
                legacy_source = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (f"merged:{int(primary_id)}", duplicate_id),
        )
        recipients = signature_workflow_service._reviewer_identities(conn, duplicate)
        signature_workflow_service._notify(
            conn,
            recipients=recipients,
            actor=actor,
            title="同名签名已归并",
            body=f"管理员已将签名“{duplicate_name}”并入同名主签名；原有材料绑定与授权已自动迁移。",
            ref_type="signature_merge",
            ref_id=f"{duplicate_id}->{int(primary_id)}",
            metadata={"primary_id": int(primary_id), "duplicate_id": duplicate_id},
        )
        merged += 1
    refreshed = _get_signature_row(conn, primary_id)
    return {"status": "success", "merged": merged, "signature": serialize_signature(refreshed, actor, conn)}


def unbind_signature(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
) -> dict[str, Any]:
    """Detach a signature from its bound account (误绑/认领错了的正规出口).

    Keeps subject_name/identity so the record stays meaningful; only the
    account linkage is removed. Pending requests keep their reviewer rows —
    approvals recorded before the unbind stay valid history.
    """
    actor = build_signature_actor(conn, user)
    row = _get_signature_row(conn, signature_id)
    if not can_unbind_signature(actor, row):
        raise SignatureServiceError(403, "只有绑定的签名者本人或超管可以解除绑定。")
    subject_role = str(row["subject_role"] or "")
    subject_id = int(row["subject_id"] or 0)
    cursor = conn.execute(
        """
        UPDATE electronic_signatures
        SET subject_id = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND subject_role = ? AND COALESCE(subject_id, 0) = ?
        """,
        (int(signature_id), subject_role, subject_id),
    )
    if int(cursor.rowcount or 0) != 1:
        raise SignatureServiceError(409, "绑定状态已变化，请刷新后重试。")
    from . import signature_workflow_service

    owner_role = str(row["owner_role"] or "").strip().lower()
    owner_id = int(row["owner_id"] or 0)
    recipients = []
    if owner_role in {"teacher", "student"} and owner_id > 0:
        recipients.append({"role": owner_role, "id": owner_id})
    if subject_role in {"teacher", "student"} and subject_id > 0:
        recipients.append({"role": subject_role, "id": subject_id})
    signature_workflow_service._notify(
        conn,
        recipients=recipients,
        actor=actor,
        title="签名绑定已解除",
        body=f"{actor.get('name')} 解除了签名“{row['subject_name'] or row['name']}”与账号的绑定；后续使用申请将由归属人或管理员审批。",
        ref_type="signature_unbind",
        ref_id=str(int(signature_id)),
        metadata={"signature_id": int(signature_id)},
    )
    refreshed = _get_signature_row(conn, signature_id)
    return serialize_signature(refreshed, actor, conn)


def create_signature_access_request(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
    *,
    note: str = "",
    context_type: str = "",
    context_id: str = "",
    context_label: str = "",
    function_point_keys: list[str] | None = None,
) -> dict[str, Any]:
    if not function_point_keys:
        raise SignatureServiceError(400, "签名申请必须绑定至少一个已登记功能点。")
    from . import signature_workflow_service

    return signature_workflow_service.create_access_request(
        conn,
        user,
        signature_id,
        function_point_keys=function_point_keys,
        note=note,
    )


def list_signature_access_requests(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    direction: str = "incoming",
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    from . import signature_workflow_service

    return signature_workflow_service.list_access_requests(
        conn,
        user,
        direction=direction,
        status=status,
        limit=limit,
    )


def review_signature_access_request(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    request_id: int,
    *,
    action: str,
    note: str = "",
) -> dict[str, Any]:
    from . import signature_workflow_service

    return signature_workflow_service.review_access_request(
        conn,
        user,
        request_id,
        action=action,
        note=note,
    )


def record_signature_usage(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
    *,
    action: str = "use",
    context_type: str = "",
    context_id: str = "",
    context_label: str = "",
    metadata: dict[str, Any] | None = None,
    ip: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    if _clean_text(action, 40) == "use":
        raise SignatureServiceError(400, "签名插入必须通过已登记功能点执行，不能记录无挂钩调用。")
    # Callers gate the action themselves (e.g. the image route allows admin
    # downloads); this helper only records the audit trail.
    row, actor = get_signature_row_for_actor(conn, user, signature_id, require_use=False)
    actor_role, actor_id = _actor_identity(actor)
    conn.execute(
        """
        INSERT INTO signature_usage_logs (
            signature_id, signature_name_snapshot,
            actor_role, actor_id, actor_name_snapshot,
            action, context_type, context_id, context_label,
            metadata_json, ip, user_agent
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(signature_id),
            row["name"],
            actor_role,
            actor_id,
            actor.get("name") or "",
            _clean_text(action, 40) or "use",
            _clean_text(context_type, 60),
            _clean_text(context_id, 80),
            _clean_text(context_label, 120),
            _safe_json(metadata or {}),
            _clean_text(ip, 80),
            _clean_text(user_agent, 240),
        ),
    )
    return {"status": "success", "signature_id": int(signature_id)}


def build_signature_dashboard_context(conn: sqlite3.Connection, user: dict[str, Any]) -> dict[str, Any]:
    payload = list_signatures(conn, user, limit=500)
    from . import signature_workflow_service

    pending_requests = signature_workflow_service.list_access_requests(
        conn, user, direction="incoming", status="pending", limit=20
    )
    return {
        "signature_actor": payload["actor"],
        "signature_stats": payload["stats"],
        "signature_pending_requests": pending_requests["items"],
    }
