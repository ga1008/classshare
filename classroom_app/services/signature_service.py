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
from ..storage_paths import unique_paths
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


def can_view_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    if bool(actor.get("is_super_admin")):
        return True
    role, _ = _actor_identity(actor)
    if not _same_school(actor, row):
        return False
    if role == "student":
        return _is_owner(actor, row)
    if role == "teacher":
        if _is_owner(actor, row):
            return True
        return _same_department(actor, row)
    return False


def _signature_request_state(
    conn: sqlite3.Connection | None,
    actor: dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
) -> dict[str, Any]:
    if conn is None or actor.get("role") != "teacher":
        return {}
    requester_id = int(actor.get("id") or 0)
    if requester_id <= 0:
        return {}
    request_row = conn.execute(
        """
        SELECT id, status, requested_at, reviewed_at, review_note
        FROM signature_access_requests
        WHERE signature_id = ?
          AND requester_teacher_id = ?
        ORDER BY requested_at DESC, id DESC
        LIMIT 1
        """,
        (int(row["id"]), requester_id),
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


def can_use_signature(
    actor: dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> bool:
    if bool(actor.get("is_super_admin")) or _is_owner(actor, row):
        return True
    if actor.get("role") != "teacher":
        return False
    if not can_view_signature(actor, row):
        return False
    return _signature_request_state(conn, actor, row).get("request_status") == "approved"


def can_request_signature_use(
    actor: dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> bool:
    if actor.get("role") != "teacher":
        return False
    if bool(actor.get("is_super_admin")) or _is_owner(actor, row):
        return False
    if not can_view_signature(actor, row):
        return False
    return _signature_request_state(conn, actor, row).get("request_status") not in {"pending", "approved"}


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
        return "(s.school_code = ? AND s.owner_role = 'student' AND s.owner_id = ?)", [school_code, user_id]

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
    for item_school_code, department in department_pairs:
        clauses.append("(s.school_code = ? AND s.department = ? AND s.owner_role IN ('teacher', 'student', 'system'))")
        params.extend([item_school_code, department])
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
    items = [serialize_signature(row, actor, conn) for row in rows]
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
) -> dict[str, Any]:
    row_id = int(row["id"])
    owner_role = str(row["owner_role"] or "")
    subject_role = str(row["subject_role"] or "")
    scope_level = str(row["scope_level"] or "")
    subject_name = row["subject_name"] or row["name"]
    is_owner = _is_owner(actor, row)
    can_delete = can_delete_signature(actor, row)
    can_edit = can_edit_signature(actor, row)
    request_state = _signature_request_state(conn, actor, row)
    can_view = can_view_signature(actor, row)
    can_use = can_use_signature(actor, row, conn)
    return {
        "id": row_id,
        "name": row["name"],
        "subject_name": subject_name,
        "subject_role": subject_role,
        "subject_role_label": _role_label(subject_role),
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
        "can_edit": can_edit,
        "can_delete": can_delete,
        "can_view": can_view,
        "can_use": can_use,
        "can_request_use": can_request_signature_use(actor, row, conn),
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

    conn.execute(
        """
        UPDATE electronic_signatures
        SET name = ?,
            subject_name = ?,
            subject_role = ?,
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
    scope_level: str = "",
    description: str = "",
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    original_filename = file.filename or "signature.png"
    ext = _normalize_upload_extension(original_filename)
    data = await _read_upload_bytes(file)
    mime_type = _detect_mime(data, ext)
    if ALLOWED_SIGNATURE_EXTENSIONS[ext] != mime_type:
        raise SignatureServiceError(400, "文件扩展名与图片内容不一致。")

    file_hash = hashlib.sha256(data).hexdigest()
    target_path = await _store_signature_bytes(file_hash, ext, data)
    actor_role, actor_id = _actor_identity(actor)

    if actor.get("is_super_admin"):
        normalized_scope = _normalize_scope_level(scope_level, "department")
        normalized_subject_role = _normalize_subject_role(subject_role, "teacher")
    else:
        normalized_subject_role = actor_role
        normalized_scope = "department" if actor_role == "teacher" else "personal"

    clean_name = _clean_text(name, 80) or _clean_text(Path(original_filename).stem, 80) or "电子签名"
    clean_subject_name = _clean_text(subject_name, 80) or actor.get("name") or clean_name
    owner_scope = _owner_scope_for_upload(actor, normalized_scope)

    cursor = conn.execute(
        """
        INSERT INTO electronic_signatures (
            name, subject_name, subject_role, scope_level,
            owner_role, owner_id, owner_name_snapshot,
            uploaded_by_role, uploaded_by_id, uploaded_by_name_snapshot,
            school_code, school_name, college, department,
            file_hash, file_ext, mime_type, stored_path, file_size,
            description, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_name,
            clean_subject_name,
            normalized_subject_role,
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
    signature_id = int(cursor.lastrowid)
    row, refreshed_actor = get_signature_row_for_actor(conn, user, signature_id)
    return serialize_signature(row, refreshed_actor, conn)


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


def _request_status_filter(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"pending", "approved", "rejected"} else ""


def _signature_request_select() -> str:
    return """
        SELECT
            r.*,
            s.name AS signature_name,
            s.subject_name AS signature_subject_name,
            s.scope_level AS signature_scope_level,
            s.school_code AS signature_school_code,
            s.school_name AS signature_school_name,
            s.college AS signature_college,
            s.department AS signature_department,
            rt.name AS requester_name,
            rt.email AS requester_email,
            ot.name AS owner_teacher_name,
            reviewer.name AS reviewer_name
        FROM signature_access_requests r
        JOIN electronic_signatures s ON s.id = r.signature_id
        LEFT JOIN teachers rt ON rt.id = r.requester_teacher_id
        LEFT JOIN teachers ot ON r.owner_role = 'teacher' AND ot.id = r.owner_id
        LEFT JOIN teachers reviewer ON reviewer.id = r.reviewed_by_teacher_id
    """


def _serialize_signature_access_request(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "signature_id": int(row["signature_id"]),
        "signature_name": row["signature_name"] or "",
        "signature_subject_name": row["signature_subject_name"] or row["signature_name"] or "",
        "signature_scope_level": row["signature_scope_level"] or "",
        "school_code": row["signature_school_code"] or "",
        "school_name": row["signature_school_name"] or "",
        "college": row["signature_college"] or "",
        "department": row["signature_department"] or "",
        "requester_teacher_id": int(row["requester_teacher_id"] or 0),
        "requester_name": row["requester_name"] or "",
        "requester_email": row["requester_email"] or "",
        "owner_role": row["owner_role"] or "",
        "owner_id": row["owner_id"],
        "owner_name": row["owner_teacher_name"] or "",
        "status": row["status"] or "",
        "request_note": row["request_note"] or "",
        "review_note": row["review_note"] or "",
        "context_type": row["context_type"] or "",
        "context_id": row["context_id"] or "",
        "context_label": row["context_label"] or "",
        "requested_at": row["requested_at"] or "",
        "reviewed_at": row["reviewed_at"] or "",
        "reviewed_by_teacher_id": row["reviewed_by_teacher_id"],
        "reviewer_name": row["reviewer_name"] or "",
    }


def create_signature_access_request(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    signature_id: int,
    *,
    note: str = "",
    context_type: str = "",
    context_id: str = "",
    context_label: str = "",
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    if actor.get("role") != "teacher":
        raise SignatureServiceError(403, "Only teachers can request signature usage.")
    row = _get_signature_row(conn, signature_id)
    if not can_view_signature(actor, row):
        raise SignatureServiceError(403, "Current account cannot view this signature.")
    if can_use_signature(actor, row, conn):
        raise SignatureServiceError(400, "Current account can already use this signature.")
    if not can_request_signature_use(actor, row, conn):
        state = _signature_request_state(conn, actor, row)
        status = state.get("request_status", "")
        if status == "pending":
            raise SignatureServiceError(409, "A pending request already exists.")
        if status == "approved":
            raise SignatureServiceError(409, "This request has already been approved.")
        raise SignatureServiceError(403, "Current account cannot request this signature.")
    try:
        cursor = conn.execute(
            """
            INSERT INTO signature_access_requests (
                signature_id, requester_teacher_id, owner_role, owner_id,
                request_note, context_type, context_id, context_label
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(signature_id),
                int(actor["id"]),
                row["owner_role"] or "",
                row["owner_id"],
                _clean_text(note, 300),
                _clean_text(context_type, 60),
                _clean_text(context_id, 80),
                _clean_text(context_label, 120),
            ),
        )
        request_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        state = _signature_request_state(conn, actor, row)
        if state:
            raise SignatureServiceError(409, "A request for this signature already exists.") from exc
        raise

    request_row = conn.execute(
        _signature_request_select()
        + """
        WHERE r.id = ?
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()
    return {"status": "success", "request": _serialize_signature_access_request(request_row)}


def list_signature_access_requests(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    direction: str = "incoming",
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    if actor.get("role") != "teacher":
        raise SignatureServiceError(403, "Only teachers can view signature requests.")
    normalized_direction = str(direction or "incoming").strip().lower()
    where: list[str] = []
    params: list[Any] = []
    if normalized_direction == "outgoing":
        where.append("r.requester_teacher_id = ?")
        params.append(int(actor["id"]))
    else:
        normalized_direction = "incoming"
        if not bool(actor.get("is_super_admin")):
            where.append("r.owner_role = 'teacher' AND r.owner_id = ?")
            params.append(int(actor["id"]))
    normalized_status = _request_status_filter(status)
    if normalized_status:
        where.append("r.status = ?")
        params.append(normalized_status)
    where_sql = " AND ".join(f"({item})" for item in where) if where else "1 = 1"
    bounded_limit = max(1, min(int(limit or 100), 500))
    rows = conn.execute(
        _signature_request_select()
        + """
        WHERE
        """
        + where_sql
        + """
        ORDER BY
            CASE r.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
            r.requested_at DESC,
            r.id DESC
        LIMIT ?
        """,
        (*params, bounded_limit),
    ).fetchall()
    return {
        "items": [_serialize_signature_access_request(row) for row in rows],
        "direction": normalized_direction,
        "status": normalized_status,
        "actor": serialize_signature_actor(actor),
    }


def review_signature_access_request(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    request_id: int,
    *,
    action: str,
    note: str = "",
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    if actor.get("role") != "teacher":
        raise SignatureServiceError(403, "Only teachers can review signature requests.")
    row = conn.execute(
        _signature_request_select()
        + """
        WHERE r.id = ?
        LIMIT 1
        """,
        (int(request_id),),
    ).fetchone()
    if not row:
        raise SignatureServiceError(404, "Signature request does not exist.")
    is_owner_teacher = row["owner_role"] == "teacher" and int(row["owner_id"] or 0) == int(actor["id"])
    if not bool(actor.get("is_super_admin")) and not is_owner_teacher:
        raise SignatureServiceError(403, "Only the signature owner or super admin can review this request.")
    if row["status"] != "pending":
        raise SignatureServiceError(409, "Only pending requests can be reviewed.")
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise SignatureServiceError(400, "Review action must be approve or reject.")
    new_status = "approved" if normalized_action == "approve" else "rejected"
    conn.execute(
        """
        UPDATE signature_access_requests
        SET status = ?,
            review_note = ?,
            reviewed_at = CURRENT_TIMESTAMP,
            reviewed_by_teacher_id = ?
        WHERE id = ?
        """,
        (new_status, _clean_text(note, 300), int(actor["id"]), int(request_id)),
    )
    refreshed = conn.execute(
        _signature_request_select()
        + """
        WHERE r.id = ?
        LIMIT 1
        """,
        (int(request_id),),
    ).fetchone()
    return {"status": "success", "request": _serialize_signature_access_request(refreshed)}


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
    row, actor = get_signature_row_for_actor(conn, user, signature_id)
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
    pending_requests = list_signature_access_requests(conn, user, direction="incoming", status="pending", limit=20)
    return {
        "signature_actor": payload["actor"],
        "signature_stats": payload["stats"],
        "signature_pending_requests": pending_requests["items"],
    }
