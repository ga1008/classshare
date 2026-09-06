"""Image attachments for resume cert / skill / experience items.

Reuses the platform's SHA-256 global file store (``file_service``) — the same
storage primitive behind homework / discussion uploads. Constraints per the
spec: image-only, ≤5 MB each, ≤5 attachments per owner item. Originals are
served back through a dedicated route; the browser handles preview sizing.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from ...db.schema_resume import ensure_resume_schema
from ...db.connection import execute_insert_returning_id
from ..chat_image_derivatives import CHAT_IMAGE_TYPES
from ..file_service import resolve_global_file_path, save_file_globally

RESUME_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS_PER_OWNER = 5
ALLOWED_IMAGE_TYPES = CHAT_IMAGE_TYPES
OWNER_KINDS = ("certificate", "skill", "experience")


def _normalize_owner_kind(owner_kind: str) -> str:
    kind = str(owner_kind or "").strip().replace("-", "_")
    if kind not in OWNER_KINDS:
        raise HTTPException(status_code=400, detail="不支持的附件归属类型")
    return kind


def _detect_upload_size(file: UploadFile) -> int | None:
    try:
        position = file.file.tell()
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(position)
        return int(size)
    except Exception:
        return None


def list_attachments(conn, student_id: int, owner_kind: str, owner_id: int) -> list[dict[str, Any]]:
    ensure_resume_schema(conn)
    owner_kind = _normalize_owner_kind(owner_kind)
    rows = conn.execute(
        "SELECT * FROM resume_attachments WHERE student_id = ? AND owner_kind = ? AND owner_id = ? "
        "ORDER BY id ASC",
        (int(student_id), owner_kind, int(owner_id)),
    ).fetchall()
    return [_serialize(dict(row)) for row in rows]


def list_attachments_for_owners(conn, student_id: int, owner_kind: str, owner_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Batch lookup: {owner_id: [attachments]} for list pages (avoids N+1)."""
    ensure_resume_schema(conn)
    owner_kind = _normalize_owner_kind(owner_kind)
    result: dict[int, list[dict[str, Any]]] = {int(oid): [] for oid in owner_ids}
    if not owner_ids:
        return result
    placeholders = ", ".join("?" for _ in owner_ids)
    rows = conn.execute(
        f"SELECT * FROM resume_attachments WHERE student_id = ? AND owner_kind = ? "
        f"AND owner_id IN ({placeholders}) ORDER BY id ASC",
        (int(student_id), owner_kind, *[int(oid) for oid in owner_ids]),
    ).fetchall()
    for row in rows:
        item = _serialize(dict(row))
        result.setdefault(int(item["owner_id"]), []).append(item)
    return result


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "owner_kind": row.get("owner_kind"),
        "owner_id": int(row.get("owner_id") or 0),
        "file_hash": row.get("file_hash"),
        "original_filename": row.get("original_filename"),
        "mime_type": row.get("mime_type"),
        "file_size": int(row.get("file_size") or 0),
        "url": f"/api/resume/attachments/{int(row['id'])}",
    }


def check_attachment_owner(conn, student_id: int, owner_kind: str, owner_id: int) -> None:
    from . import resume_profile_service as profile
    kind = _normalize_owner_kind(owner_kind)
    table = profile.LIST_SECTIONS[kind]["table"]
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ? AND student_id = ?", (int(owner_id), int(student_id))).fetchone():
        raise HTTPException(404, "附件归属材料不存在或无权访问")
    count = conn.execute("SELECT COUNT(*) AS n FROM resume_attachments WHERE student_id = ? AND owner_kind = ? AND owner_id = ?", (int(student_id), kind, int(owner_id))).fetchone()
    if int(count["n"]) >= MAX_ATTACHMENTS_PER_OWNER:
        raise HTTPException(400, "每项最多上传5张图片")


async def create_attachment(conn, student_id: int, owner_kind: str, owner_id: int, file: UploadFile) -> dict[str, Any]:
    """Compatibility adapter; request routes prepare the file before opening DB."""
    check_attachment_owner(conn, student_id, owner_kind, owner_id)
    saved = await prepare_attachment_upload(file)
    return bind_attachment(conn, student_id, owner_kind, owner_id, saved)


async def prepare_attachment_upload(file: UploadFile) -> dict[str, Any]:
    from .resume_import_service import validate_upload_stream
    await validate_upload_stream(file, max_bytes=RESUME_ATTACHMENT_MAX_BYTES)
    content_type = str(file.content_type or "").split(";", 1)[0].lower()
    guessed = mimetypes.guess_type(str(file.filename or ""))[0]
    if content_type not in ALLOWED_IMAGE_TYPES:
        content_type = guessed or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, "仅支持有效图片附件")
    result = await save_file_globally(file)
    if not result:
        raise HTTPException(500, "图片保存失败")
    return {**result, "filename": str(file.filename or "image")[:200], "mime_type": content_type}


def bind_attachment(conn, student_id: int, owner_kind: str, owner_id: int, saved: dict[str, Any]) -> dict[str, Any]:
    from . import resume_profile_service as profile
    from ..file_service import lock_global_file_references
    ensure_resume_schema(conn)
    kind = _normalize_owner_kind(owner_kind)
    table = profile.LIST_SECTIONS[kind]["table"]
    locked = conn.execute(f"UPDATE {table} SET revision = revision WHERE id = ? AND student_id = ?", (int(owner_id), int(student_id)))
    if locked.rowcount != 1:
        raise HTTPException(404, "附件归属材料不存在或无权访问")
    existing = conn.execute("SELECT COUNT(*) AS c FROM resume_attachments WHERE student_id=? AND owner_kind=? AND owner_id=?", (int(student_id), kind, int(owner_id))).fetchone()
    if int(existing["c"]) >= MAX_ATTACHMENTS_PER_OWNER:
        raise HTTPException(400, "每项最多上传5张图片")
    lock_global_file_references(conn, [saved["hash"]])
    new_id = execute_insert_returning_id(conn,
        "INSERT INTO resume_attachments(student_id,owner_kind,owner_id,file_hash,original_filename,mime_type,file_size) VALUES(?,?,?,?,?,?,?)",
        (int(student_id), kind, int(owner_id), saved["hash"], saved["filename"], saved["mime_type"], int(saved["size"])))
    return _serialize(dict(conn.execute("SELECT * FROM resume_attachments WHERE id=? AND student_id=?", (new_id, int(student_id))).fetchone()))


def delete_attachment(conn, student_id: int, attachment_id: int) -> None:
    ensure_resume_schema(conn)
    conn.execute(
        "DELETE FROM resume_attachments WHERE id = ? AND student_id = ?",
        (int(attachment_id), int(student_id)),
    )


def delete_owner_attachments(conn, student_id: int, owner_kind: str, owner_id: int) -> None:
    """Cascade: remove attachments when their owner item is deleted."""
    ensure_resume_schema(conn)
    conn.execute(
        "DELETE FROM resume_attachments WHERE student_id = ? AND owner_kind = ? AND owner_id = ?",
        (int(student_id), _normalize_owner_kind(owner_kind), int(owner_id)),
    )


def resolve_attachment_file(conn, student_id: int, attachment_id: int) -> tuple[Path, str, str]:
    """Return (path, mime_type, filename) for serving — owner-scoped."""
    ensure_resume_schema(conn)
    row = conn.execute(
        "SELECT * FROM resume_attachments WHERE id = ? AND student_id = ? LIMIT 1",
        (int(attachment_id), int(student_id)),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    item = dict(row)
    path = resolve_global_file_path(str(item.get("file_hash") or ""))
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="附件文件丢失")
    return Path(path), str(item.get("mime_type") or "image/png"), str(item.get("original_filename") or "image")


def attachment_data_uri(file_hash: str, mime_type: str) -> str | None:
    """Inline an attachment as a data URI for the render service (export-safe)."""
    path = resolve_global_file_path(str(file_hash or ""))
    if not path or not Path(path).exists():
        return None
    try:
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except Exception:
        return None
    return f"data:{mime_type or 'image/png'};base64,{encoded}"
