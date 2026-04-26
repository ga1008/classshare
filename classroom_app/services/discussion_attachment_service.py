from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from ..config import MAX_UPLOAD_SIZE_BYTES
from ..services.file_service import resolve_global_file_path, save_file_globally

ALLOWED_DISCUSSION_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
DISCUSSION_ATTACHMENT_MAX_BYTES = min(MAX_UPLOAD_SIZE_BYTES, 10 * 1024 * 1024)
MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE = 4


def ensure_discussion_attachment_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discussion_attachments
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_offering_id INTEGER NOT NULL,
            uploaded_by_user_id TEXT NOT NULL,
            uploaded_by_role TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            image_width INTEGER,
            image_height INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_attachments_room_created "
        "ON discussion_attachments (class_offering_id, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discussion_attachments_owner "
        "ON discussion_attachments (class_offering_id, uploaded_by_role, uploaded_by_user_id, created_at DESC, id DESC)"
    )


def _coerce_attachment_ids(attachment_ids: Iterable[object] | None) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_value in attachment_ids or []:
        try:
            attachment_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if attachment_id <= 0 or attachment_id in seen:
            continue
        normalized.append(attachment_id)
        seen.add(attachment_id)
    return normalized


def _detect_upload_size(file: UploadFile) -> int | None:
    try:
        file.file.seek(0, 2)
        size = int(file.file.tell())
        file.file.seek(0)
        return size
    except Exception:
        return None


def _get_image_dimensions(file_path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(file_path) as image:
            image.load()
            width, height = image.size
            return int(width or 0), int(height or 0)
    except (UnidentifiedImageError, OSError):
        return None, None


def build_discussion_attachment_payload(row, class_offering_id: int) -> dict:
    return {
        "attachment_id": int(row["id"]),
        "type": "image",
        "name": row["original_filename"],
        "mime_type": row["mime_type"],
        "file_size": int(row["file_size"] or 0),
        "width": int(row["image_width"] or 0),
        "height": int(row["image_height"] or 0),
        "url": f"/api/classrooms/{class_offering_id}/discussion-attachments/{int(row['id'])}",
        "created_at": row["created_at"],
    }


def build_attachment_image_inputs_from_payloads(
    conn,
    class_offering_id: int,
    attachments: Iterable[dict] | None,
) -> list[dict]:
    ensure_discussion_attachment_schema(conn)
    attachment_ids = _coerce_attachment_ids(
        item.get("attachment_id")
        for item in (attachments or [])
        if isinstance(item, dict)
    )
    if not attachment_ids:
        return []

    placeholders = ", ".join(["?"] * len(attachment_ids))
    rows = conn.execute(
        f"""
        SELECT id, file_hash, mime_type, original_filename
        FROM discussion_attachments
        WHERE class_offering_id = ?
          AND id IN ({placeholders})
        """,
        (int(class_offering_id), *attachment_ids),
    ).fetchall()
    row_map = {int(row["id"]): row for row in rows}

    image_inputs: list[dict] = []
    for attachment_id in attachment_ids:
        row = row_map.get(attachment_id)
        if row is None:
            continue

        file_path = resolve_global_file_path(str(row["file_hash"]))
        if not file_path:
            continue

        mime_type = str(row["mime_type"] or "").strip().lower()
        if mime_type not in ALLOWED_DISCUSSION_IMAGE_TYPES:
            guessed_type = mimetypes.guess_type(str(row["original_filename"] or ""))[0]
            mime_type = guessed_type or "application/octet-stream"

        try:
            binary = file_path.read_bytes()
        except OSError:
            continue

        encoded = base64.b64encode(binary).decode("utf-8")
        image_inputs.append({
            "attachment_id": attachment_id,
            "name": str(row["original_filename"] or ""),
            "mime_type": mime_type,
            "url": f"data:{mime_type};base64,{encoded}",
        })

    return image_inputs


async def create_discussion_attachment(conn, class_offering_id: int, user: dict, file: UploadFile) -> dict:
    ensure_discussion_attachment_schema(conn)

    content_type = str(file.content_type or "").lower()
    if content_type not in ALLOWED_DISCUSSION_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="讨论区仅支持 PNG、JPG、GIF 或 WebP 图片")

    file_size = _detect_upload_size(file)
    if file_size is not None and file_size > DISCUSSION_ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="讨论区图片大小不能超过 10MB")

    save_result = await save_file_globally(file)
    if not save_result:
        raise HTTPException(status_code=500, detail="讨论区图片保存失败")

    saved_size = int(save_result.get("size") or 0)
    if saved_size > DISCUSSION_ATTACHMENT_MAX_BYTES:
        try:
            Path(save_result["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=413, detail="讨论区图片大小不能超过 10MB")

    file_path = Path(save_result["path"])
    width, height = _get_image_dimensions(file_path)
    if width is None or height is None:
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="上传文件不是有效图片")
    original_filename = str(file.filename or "image")

    cursor = conn.execute(
        """
        INSERT INTO discussion_attachments (
            class_offering_id,
            uploaded_by_user_id,
            uploaded_by_role,
            file_hash,
            original_filename,
            mime_type,
            file_size,
            image_width,
            image_height
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(class_offering_id),
            str(user.get("id") or ""),
            str(user.get("role") or ""),
            str(save_result["hash"]),
            original_filename,
            content_type,
            saved_size,
            width,
            height,
        ),
    )
    row = conn.execute(
        "SELECT * FROM discussion_attachments WHERE id = ?",
        (int(cursor.lastrowid),),
    ).fetchone()
    return build_discussion_attachment_payload(row, class_offering_id)


def load_discussion_attachment_row(conn, class_offering_id: int, attachment_id: int):
    ensure_discussion_attachment_schema(conn)
    return conn.execute(
        """
        SELECT *
        FROM discussion_attachments
        WHERE class_offering_id = ?
          AND id = ?
        LIMIT 1
        """,
        (int(class_offering_id), int(attachment_id)),
    ).fetchone()


def resolve_discussion_attachment_payloads(
    conn,
    class_offering_id: int,
    attachment_ids: Iterable[object] | None,
    user: dict,
) -> list[dict]:
    ensure_discussion_attachment_schema(conn)
    normalized_ids = _coerce_attachment_ids(attachment_ids)
    if not normalized_ids:
        return []

    if len(normalized_ids) > MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(
            status_code=400,
            detail=f"单条讨论消息最多只能发送 {MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE} 张图片",
        )

    placeholders = ", ".join(["?"] * len(normalized_ids))
    rows = conn.execute(
        f"""
        SELECT *
        FROM discussion_attachments
        WHERE class_offering_id = ?
          AND uploaded_by_user_id = ?
          AND uploaded_by_role = ?
          AND id IN ({placeholders})
        ORDER BY id ASC
        """,
        (
            int(class_offering_id),
            str(user.get("id") or ""),
            str(user.get("role") or ""),
            *normalized_ids,
        ),
    ).fetchall()
    row_map = {int(row["id"]): row for row in rows}
    return [
        build_discussion_attachment_payload(row_map[attachment_id], class_offering_id)
        for attachment_id in normalized_ids
        if attachment_id in row_map
    ]


def build_attachment_data_urls_from_payloads(conn, class_offering_id: int, attachments: Iterable[dict] | None) -> list[str]:
    return [
        str(item.get("url") or "")
        for item in build_attachment_image_inputs_from_payloads(conn, class_offering_id, attachments)
        if str(item.get("url") or "").strip()
    ]
