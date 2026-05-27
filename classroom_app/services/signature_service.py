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
from .organization_scope_service import build_org_scope, load_teacher_org_scope, normalize_college, normalize_school_code


MAX_SIGNATURE_FILE_BYTES = 5 * 1024 * 1024
ALLOWED_SIGNATURE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
VALID_SUBJECT_ROLES = {"teacher", "student", "other", "system"}
VALID_SCOPE_LEVELS = {"personal", "college", "platform"}


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
        scope = load_teacher_org_scope(conn, user_id)
        name = _clean_text(row["name"] or user.get("name") or "教师", 80)
        return {
            "role": "teacher",
            "id": user_id,
            "name": name,
            "is_super_admin": is_super_admin_teacher(conn, user_id),
            "scope": scope,
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
    }


def _same_college(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    scope = actor.get("scope") or {}
    row_school = normalize_school_code(row["school_code"] if "school_code" in row.keys() else "")
    actor_school = normalize_school_code(scope.get("school_code"))
    if row_school != actor_school:
        return False
    actor_college = normalize_college(scope.get("college"))
    row_college = normalize_college(row["college"] if "college" in row.keys() else "")
    return bool(actor_college and row_college and actor_college == row_college)


def _is_owner(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    role, user_id = _actor_identity(actor)
    try:
        owner_id = int(row["owner_id"] or 0)
    except (TypeError, ValueError):
        owner_id = 0
    return str(row["owner_role"] or "") == role and owner_id == user_id


def can_use_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    if bool(actor.get("is_super_admin")):
        return True
    role, _ = _actor_identity(actor)
    if role == "student":
        return _is_owner(actor, row)
    if role == "teacher":
        if _is_owner(actor, row):
            return True
        if str(row["scope_level"] or "") == "platform":
            row_school = normalize_school_code(row["school_code"] if "school_code" in row.keys() else "")
            return row_school == normalize_school_code((actor.get("scope") or {}).get("school_code"))
        return _same_college(actor, row)
    return False


def can_delete_signature(actor: dict[str, Any], row: sqlite3.Row | dict[str, Any]) -> bool:
    return bool(actor.get("is_super_admin")) or _is_owner(actor, row)


def _visibility_sql(actor: dict[str, Any]) -> tuple[str, list[Any]]:
    if bool(actor.get("is_super_admin")):
        return "1 = 1", []

    role, user_id = _actor_identity(actor)
    if role == "student":
        return "(s.owner_role = 'student' AND s.owner_id = ?)", [user_id]

    scope = actor.get("scope") or {}
    school_code = normalize_school_code(scope.get("school_code"))
    college = normalize_college(scope.get("college"))
    clauses = ["(s.owner_role = 'teacher' AND s.owner_id = ?)"]
    params: list[Any] = [user_id]
    if school_code and college:
        clauses.append("(s.school_code = ? AND s.college = ? AND s.owner_role IN ('teacher', 'student', 'system'))")
        params.extend([school_code, college])
    if school_code:
        clauses.append("(s.scope_level = 'platform' AND s.school_code = ?)")
        params.append(school_code)
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
            COALESCE(usage_stats.usage_count, 0) AS usage_count,
            usage_stats.last_used_at AS last_used_at
        FROM electronic_signatures s
        LEFT JOIN teachers ot ON s.owner_role = 'teacher' AND ot.id = s.owner_id
        LEFT JOIN students os ON s.owner_role = 'student' AND os.id = s.owner_id
        LEFT JOIN (
            SELECT signature_id, COUNT(*) AS usage_count, MAX(created_at) AS last_used_at
            FROM signature_usage_logs
            GROUP BY signature_id
        ) usage_stats ON usage_stats.signature_id = s.id
    """


def list_signatures(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    search: str = "",
    owner_role: str = "",
    subject_role: str = "",
    scope: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    actor = build_signature_actor(conn, user)
    visibility_sql, params = _visibility_sql(actor)
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
                OR ot.name LIKE ?
                OR os.name LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like])

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
    elif normalized_scope == "college" and actor_role == "teacher":
        actor_scope = actor.get("scope") or {}
        where.append("s.school_code = ? AND s.college = ?")
        params.extend([
            normalize_school_code(actor_scope.get("school_code")),
            normalize_college(actor_scope.get("college")),
        ])
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
    items = [serialize_signature(row, actor) for row in rows]
    return {
        "items": items,
        "total": total,
        "actor": serialize_signature_actor(actor),
        "stats": _build_signature_stats(items, actor),
    }


def serialize_signature_actor(actor: dict[str, Any]) -> dict[str, Any]:
    scope = actor.get("scope") or {}
    return {
        "role": actor.get("role"),
        "id": actor.get("id"),
        "name": actor.get("name"),
        "is_super_admin": bool(actor.get("is_super_admin")),
        "school_name": scope.get("school_name") or "",
        "college": scope.get("college") or "",
        "department": scope.get("department") or "",
    }


def _build_signature_stats(items: list[dict[str, Any]], actor: dict[str, Any]) -> dict[str, Any]:
    return {
        "visible_total": len(items),
        "mine": sum(1 for item in items if item.get("is_owner")),
        "college": sum(1 for item in items if item.get("scope_level") == "college"),
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
        "college": "学院可用",
        "platform": "平台可用",
    }.get(str(scope_level or ""), "未分类")


def serialize_signature(row: sqlite3.Row, actor: dict[str, Any]) -> dict[str, Any]:
    row_id = int(row["id"])
    owner_role = str(row["owner_role"] or "")
    subject_role = str(row["subject_role"] or "")
    scope_level = str(row["scope_level"] or "")
    subject_name = row["subject_name"] or row["name"]
    is_owner = _is_owner(actor, row)
    can_delete = can_delete_signature(actor, row)
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
        "scope_level": scope_level,
        "scope_label": _scope_label(scope_level),
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
        "can_delete": can_delete,
        "can_use": can_use_signature(actor, row),
        "image_url": f"/api/signatures/{row_id}/image",
        "download_url": f"/api/signatures/{row_id}/image?download=1",
        "legacy_source": row["legacy_source"] or "",
    }


def get_signature_row_for_actor(conn: sqlite3.Connection, user: dict[str, Any], signature_id: int) -> tuple[sqlite3.Row, dict[str, Any]]:
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
    if not can_use_signature(actor, row):
        raise SignatureServiceError(403, "当前账号无权访问此签名。")
    return row, actor


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
        normalized_scope = _normalize_scope_level(scope_level, "college")
        normalized_subject_role = _normalize_subject_role(subject_role, "teacher")
    else:
        normalized_subject_role = actor_role
        normalized_scope = "college" if actor_role == "teacher" else "personal"

    clean_name = _clean_text(name, 80) or _clean_text(Path(original_filename).stem, 80) or "电子签名"
    clean_subject_name = _clean_text(subject_name, 80) or actor.get("name") or clean_name
    owner_scope = _owner_scope_for_upload(actor, normalized_scope)

    cursor = conn.execute(
        """
        INSERT INTO electronic_signatures (
            name, subject_name, subject_role, scope_level,
            owner_role, owner_id, owner_name_snapshot,
            school_code, school_name, college, department,
            file_hash, file_ext, mime_type, stored_path, file_size,
            description, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_name,
            clean_subject_name,
            normalized_subject_role,
            normalized_scope,
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
    return serialize_signature(row, refreshed_actor)


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
        raise SignatureServiceError(403, "只有签名上传者或超管可以删除此签名。")
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
    return {
        "signature_actor": payload["actor"],
        "signature_stats": payload["stats"],
    }
