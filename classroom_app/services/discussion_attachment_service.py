from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile

from ..config import MAX_UPLOAD_SIZE_BYTES
from ..database import get_db_connection
from ..services.chat_image_derivatives import (
    CHAT_IMAGE_DERIVATIVE_MIME_TYPE,
    CHAT_IMAGE_TYPES,
    ChatImageDerivativeError,
    ChatImageTooLargeError,
    build_chat_image_derivative_sync,
    prepare_chat_image_derivatives,
    run_chat_image_processing,
)
from ..services.file_service import (
    resolve_global_file_path,
    save_file_globally,
)

ALLOWED_DISCUSSION_IMAGE_TYPES = CHAT_IMAGE_TYPES
DISCUSSION_ATTACHMENT_MAX_BYTES = min(MAX_UPLOAD_SIZE_BYTES, 10 * 1024 * 1024)
MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE = 4
DISCUSSION_DERIVATIVE_MIME_TYPE = CHAT_IMAGE_DERIVATIVE_MIME_TYPE

_derivative_locks: dict[str, asyncio.Lock] = {}
_derivative_locks_guard = asyncio.Lock()

DISCUSSION_DERIVATIVE_COLUMNS = {
    "thumbnail_file_hash": "TEXT",
    "thumbnail_mime_type": "TEXT",
    "thumbnail_file_size": "INTEGER NOT NULL DEFAULT 0",
    "thumbnail_width": "INTEGER",
    "thumbnail_height": "INTEGER",
    "preview_file_hash": "TEXT",
    "preview_mime_type": "TEXT",
    "preview_file_size": "INTEGER NOT NULL DEFAULT 0",
    "preview_width": "INTEGER",
    "preview_height": "INTEGER",
}

DISCUSSION_VARIANT_COLUMNS = {
    "thumbnail": {
        "hash": "thumbnail_file_hash",
        "mime_type": "thumbnail_mime_type",
        "file_size": "thumbnail_file_size",
        "width": "thumbnail_width",
        "height": "thumbnail_height",
    },
    "preview": {
        "hash": "preview_file_hash",
        "mime_type": "preview_mime_type",
        "file_size": "preview_file_size",
        "width": "preview_width",
        "height": "preview_height",
    },
}


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
            thumbnail_file_hash TEXT,
            thumbnail_mime_type TEXT,
            thumbnail_file_size INTEGER NOT NULL DEFAULT 0,
            thumbnail_width INTEGER,
            thumbnail_height INTEGER,
            preview_file_hash TEXT,
            preview_mime_type TEXT,
            preview_file_size INTEGER NOT NULL DEFAULT 0,
            preview_width INTEGER,
            preview_height INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
        )
        """
    )
    existing_columns = {
        str(row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1])
        for row in conn.execute("PRAGMA table_info(discussion_attachments)").fetchall()
    }
    for column_name, column_type in DISCUSSION_DERIVATIVE_COLUMNS.items():
        if column_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE discussion_attachments ADD COLUMN {column_name} {column_type}")
            except Exception:
                pass

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


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


async def _get_derivative_lock(class_offering_id: int, attachment_id: int, variant: str) -> asyncio.Lock:
    key = f"{int(class_offering_id)}:{int(attachment_id)}:{variant}"
    async with _derivative_locks_guard:
        lock = _derivative_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _derivative_locks[key] = lock
        return lock


def _variant_url(class_offering_id: int, attachment_id: int, variant: str) -> str:
    base_url = f"/api/classrooms/{int(class_offering_id)}/discussion-attachments/{int(attachment_id)}"
    return f"{base_url}/{variant}"


def _normalize_discussion_attachment_payload(item: dict, class_offering_id: int | None) -> dict:
    payload = dict(item)
    try:
        attachment_id = int(payload.get("attachment_id") or payload.get("id") or 0)
    except (TypeError, ValueError):
        attachment_id = 0
    if attachment_id <= 0 or not class_offering_id:
        return payload

    thumbnail_url = _variant_url(int(class_offering_id), attachment_id, "thumbnail")
    preview_url = _variant_url(int(class_offering_id), attachment_id, "preview")
    original_url = _variant_url(int(class_offering_id), attachment_id, "original")
    payload.setdefault("attachment_id", attachment_id)
    payload.setdefault("id", attachment_id)
    payload["url"] = thumbnail_url
    payload["thumbnail_url"] = thumbnail_url
    payload["preview_url"] = preview_url
    payload["original_url"] = original_url
    payload.setdefault("download_url", f"{original_url}?download=1")
    return payload


def normalize_discussion_attachment_payloads(
    attachments: Iterable[dict] | None,
    class_offering_id: int | None,
) -> list[dict]:
    return [
        _normalize_discussion_attachment_payload(item, class_offering_id)
        for item in (attachments or [])
        if isinstance(item, dict)
    ]


def build_discussion_attachment_payload(row, class_offering_id: int) -> dict:
    attachment_id = int(row["id"])
    original_url = _variant_url(class_offering_id, attachment_id, "original")
    thumbnail_url = _variant_url(class_offering_id, attachment_id, "thumbnail")
    preview_url = _variant_url(class_offering_id, attachment_id, "preview")
    return {
        "attachment_id": attachment_id,
        "id": attachment_id,
        "type": "image",
        "name": row["original_filename"],
        "mime_type": row["mime_type"],
        "file_size": int(row["file_size"] or 0),
        "width": int(row["image_width"] or 0),
        "height": int(row["image_height"] or 0),
        "url": thumbnail_url,
        "thumbnail_url": thumbnail_url,
        "thumbnail_file_size": int(_row_value(row, "thumbnail_file_size", 0) or 0),
        "thumbnail_width": int(_row_value(row, "thumbnail_width", 0) or 0),
        "thumbnail_height": int(_row_value(row, "thumbnail_height", 0) or 0),
        "preview_url": preview_url,
        "preview_file_size": int(_row_value(row, "preview_file_size", 0) or 0),
        "preview_width": int(_row_value(row, "preview_width", 0) or 0),
        "preview_height": int(_row_value(row, "preview_height", 0) or 0),
        "original_url": original_url,
        "download_url": f"{original_url}?download=1",
        "created_at": row["created_at"],
    }


def _resolve_original_file_payload(row) -> dict | None:
    file_path = resolve_global_file_path(str(row["file_hash"]))
    if not file_path:
        return None
    return {
        "path": file_path,
        "mime_type": str(row["mime_type"] or "application/octet-stream"),
        "file_size": int(row["file_size"] or file_path.stat().st_size),
        "filename": str(row["original_filename"] or "image"),
        "width": int(row["image_width"] or 0),
        "height": int(row["image_height"] or 0),
        "variant": "original",
    }


def _resolve_variant_file_payload(row, variant: str) -> dict | None:
    columns = DISCUSSION_VARIANT_COLUMNS.get(variant)
    if not columns:
        return None

    file_hash = str(_row_value(row, columns["hash"], "") or "").strip()
    if not file_hash:
        return None
    file_path = resolve_global_file_path(file_hash)
    if not file_path:
        return None

    original_name = str(row["original_filename"] or "image")
    stem = Path(original_name).stem or "image"
    suffix = "thumb" if variant == "thumbnail" else "preview"
    return {
        "path": file_path,
        "mime_type": str(
            _row_value(row, columns["mime_type"], DISCUSSION_DERIVATIVE_MIME_TYPE)
            or DISCUSSION_DERIVATIVE_MIME_TYPE
        ),
        "file_size": int(_row_value(row, columns["file_size"], 0) or file_path.stat().st_size),
        "filename": f"{stem}-{suffix}.jpg",
        "width": int(_row_value(row, columns["width"], 0) or 0),
        "height": int(_row_value(row, columns["height"], 0) or 0),
        "variant": variant,
    }


def resolve_discussion_attachment_file_payload(row, variant: str) -> dict | None:
    normalized_variant = str(variant or "original").lower()
    if normalized_variant == "original":
        return _resolve_original_file_payload(row)
    if normalized_variant in DISCUSSION_VARIANT_COLUMNS:
        return _resolve_variant_file_payload(row, normalized_variant)
    return None


def _update_derivative_columns(conn, attachment_id: int, variant: str, derivative: dict) -> None:
    columns = DISCUSSION_VARIANT_COLUMNS[variant]
    conn.execute(
        f"""
        UPDATE discussion_attachments
        SET
            {columns["hash"]} = ?,
            {columns["mime_type"]} = ?,
            {columns["file_size"]} = ?,
            {columns["width"]} = ?,
            {columns["height"]} = ?
        WHERE id = ?
        """,
        (
            str(derivative["file_hash"]),
            str(derivative["mime_type"]),
            int(derivative["file_size"] or 0),
            int(derivative["width"] or 0),
            int(derivative["height"] or 0),
            int(attachment_id),
        ),
    )


def _ensure_discussion_attachment_derivative_sync(
    class_offering_id: int,
    attachment_id: int,
    variant: str,
) -> dict:
    with get_db_connection() as conn:
        row = load_discussion_attachment_row(conn, class_offering_id, attachment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discussion image not found")
        existing = resolve_discussion_attachment_file_payload(row, variant)
        if existing:
            return existing

        original_file = _resolve_original_file_payload(row)
        if not original_file:
            raise HTTPException(status_code=404, detail="Discussion image not found")

    try:
        derivative = build_chat_image_derivative_sync(original_file["path"], variant)
    except ChatImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail="Discussion image dimensions are too large") from exc
    except ChatImageDerivativeError as exc:
        raise HTTPException(status_code=400, detail="Invalid discussion image") from exc

    with get_db_connection() as conn:
        row = load_discussion_attachment_row(conn, class_offering_id, attachment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discussion image not found")
        existing = resolve_discussion_attachment_file_payload(row, variant)
        if existing:
            return existing
        _update_derivative_columns(conn, attachment_id, variant, derivative)
        conn.commit()
        row = load_discussion_attachment_row(conn, class_offering_id, attachment_id)
        payload = resolve_discussion_attachment_file_payload(row, variant)
        if not payload:
            raise HTTPException(status_code=500, detail="Discussion image derivative unavailable")
        return payload


async def ensure_discussion_attachment_file_payload(
    class_offering_id: int,
    attachment_id: int,
    variant: str = "original",
) -> dict:
    normalized_variant = str(variant or "original").lower()
    if normalized_variant == "original":
        with get_db_connection() as conn:
            row = load_discussion_attachment_row(conn, class_offering_id, attachment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Discussion image not found")
        payload = resolve_discussion_attachment_file_payload(row, "original")
        if not payload:
            raise HTTPException(status_code=404, detail="Discussion image not found")
        return payload
    if normalized_variant not in DISCUSSION_VARIANT_COLUMNS:
        raise HTTPException(status_code=404, detail="Discussion image variant not found")

    with get_db_connection() as conn:
        row = load_discussion_attachment_row(conn, class_offering_id, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Discussion image not found")
    payload = resolve_discussion_attachment_file_payload(row, normalized_variant)
    if payload:
        return payload

    lock = await _get_derivative_lock(class_offering_id, attachment_id, normalized_variant)
    async with lock:
        return await run_chat_image_processing(
            _ensure_discussion_attachment_derivative_sync,
            class_offering_id,
            attachment_id,
            normalized_variant,
        )


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
        SELECT *
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

        preview_payload = resolve_discussion_attachment_file_payload(row, "preview")
        if not preview_payload:
            try:
                preview_payload = _ensure_discussion_attachment_derivative_sync(
                    class_offering_id,
                    attachment_id,
                    "preview",
                )
            except HTTPException:
                preview_payload = _resolve_original_file_payload(row)
        if not preview_payload:
            continue

        file_path = preview_payload["path"]
        mime_type = str(preview_payload["mime_type"] or "").strip().lower()
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

    content_type = str(file.content_type or "").split(";", 1)[0].lower()
    if content_type not in ALLOWED_DISCUSSION_IMAGE_TYPES:
        guessed_type = mimetypes.guess_type(str(file.filename or ""))[0]
        if guessed_type in ALLOWED_DISCUSSION_IMAGE_TYPES:
            content_type = guessed_type
    if content_type not in ALLOWED_DISCUSSION_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Discussion room only supports PNG, JPG, GIF, or WebP images")

    file_size = _detect_upload_size(file)
    if file_size is not None and file_size > DISCUSSION_ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Discussion image cannot exceed 10MB")

    save_result = await save_file_globally(file)
    if not save_result:
        raise HTTPException(status_code=500, detail="Failed to save discussion image")

    saved_size = int(save_result.get("size") or 0)
    if saved_size > DISCUSSION_ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Discussion image cannot exceed 10MB")

    file_path = Path(save_result["path"])
    try:
        derivative_payload = await prepare_chat_image_derivatives(file_path)
    except ChatImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail="Discussion image dimensions are too large") from exc
    except ChatImageDerivativeError as exc:
        # Global blobs are hash-addressed and may be shared by other features.
        raise HTTPException(status_code=400, detail="Invalid discussion image") from exc

    width = int(derivative_payload["width"])
    height = int(derivative_payload["height"])
    original_filename = str(file.filename or "image")
    thumbnail = derivative_payload["thumbnail"]
    preview = derivative_payload["preview"]

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
            image_height,
            thumbnail_file_hash,
            thumbnail_mime_type,
            thumbnail_file_size,
            thumbnail_width,
            thumbnail_height,
            preview_file_hash,
            preview_mime_type,
            preview_file_size,
            preview_width,
            preview_height
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            str(thumbnail["file_hash"]),
            str(thumbnail["mime_type"]),
            int(thumbnail["file_size"] or 0),
            int(thumbnail["width"] or 0),
            int(thumbnail["height"] or 0),
            str(preview["file_hash"]),
            str(preview["mime_type"]),
            int(preview["file_size"] or 0),
            int(preview["width"] or 0),
            int(preview["height"] or 0),
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
            detail=f"Only {MAX_DISCUSSION_ATTACHMENTS_PER_MESSAGE} discussion images can be sent per message",
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
