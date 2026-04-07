from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from ..config import GLOBAL_FILES_DIR
from .file_service import save_file_globally

MAX_CUSTOM_EMOJI_BYTES = 5 * 1024 * 1024
MAX_CUSTOM_EMOJIS_PER_USER = 60
FREQUENT_EMOJI_LIMIT = 8
ALLOWED_EMOJI_FORMATS = {"PNG", "JPEG", "GIF"}
ALLOWED_EMOJI_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif"}


def build_custom_emoji_url(class_offering_id: int, emoji_id: int) -> str:
    return f"/api/classrooms/{class_offering_id}/custom-emojis/{emoji_id}/file"


def sanitize_custom_emoji_name(filename: str) -> str:
    stem = Path(filename or "emoji").stem.strip() or "emoji"
    stem = re.sub(r"\s+", " ", stem)
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff _-]", "", stem).strip(" _-")
    return (stem or "emoji")[:32]


def make_unique_custom_emoji_name(
    conn,
    class_offering_id: int,
    owner_user_id: int,
    owner_user_role: str,
    desired_name: str,
) -> str:
    candidate = desired_name
    suffix = 2
    while True:
        exists = conn.execute(
            """
            SELECT 1
            FROM custom_emojis
            WHERE class_offering_id = ?
              AND owner_user_id = ?
              AND owner_user_role = ?
              AND display_name = ?
            LIMIT 1
            """,
            (class_offering_id, owner_user_id, owner_user_role, candidate),
        ).fetchone()
        if exists is None:
            return candidate
        candidate = f"{desired_name} ({suffix})"
        suffix += 1


def serialize_custom_emoji_row(class_offering_id: int, row) -> dict:
    return {
        "id": int(row["id"]),
        "type": "custom",
        "name": row["display_name"],
        "image_url": build_custom_emoji_url(class_offering_id, int(row["id"])),
        "file_size": int(row["file_size"]),
        "mime_type": row["mime_type"],
        "width": int(row["image_width"] or 0),
        "height": int(row["image_height"] or 0),
        "created_at": row["created_at"],
    }


async def validate_and_store_custom_emoji(file: UploadFile) -> dict:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EMOJI_EXTENSIONS:
        raise HTTPException(400, "仅支持上传 PNG、JPG、JPEG 或 GIF 表情。")

    file.file.seek(0, 2)
    file_size = int(file.file.tell())
    file.file.seek(0)
    if file_size <= 0:
        raise HTTPException(400, "上传文件为空。")
    if file_size > MAX_CUSTOM_EMOJI_BYTES:
        raise HTTPException(400, "表情文件不能超过 5MB。")

    try:
        with Image.open(file.file) as image:
            image.load()
            image_format = (image.format or "").upper()
            width, height = image.size
    except UnidentifiedImageError as exc:
        raise HTTPException(400, "上传文件不是有效的图片。") from exc
    except Exception as exc:
        raise HTTPException(400, f"图片校验失败: {exc}") from exc
    finally:
        await file.seek(0)

    if image_format not in ALLOWED_EMOJI_FORMATS:
        raise HTTPException(400, "仅支持 PNG、JPG、JPEG 或 GIF 表情。")

    file_info = await save_file_globally(file)
    if not file_info:
        raise HTTPException(500, "表情文件保存失败。")

    mime_type = file.content_type or {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "GIF": "image/gif",
    }.get(image_format, "application/octet-stream")

    return {
        "hash": file_info["hash"],
        "path": file_info["path"],
        "size": int(file_info["size"]),
        "mime_type": mime_type,
        "width": int(width),
        "height": int(height),
    }


def get_custom_emoji_path(file_hash: str) -> Path:
    file_path = Path(GLOBAL_FILES_DIR) / file_hash
    if not file_path.exists():
        raise HTTPException(404, "表情文件不存在。")
    return file_path


def load_custom_emojis_for_user(conn, class_offering_id: int, user: dict) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM custom_emojis
        WHERE class_offering_id = ?
          AND owner_user_id = ?
          AND owner_user_role = ?
        ORDER BY created_at DESC, id DESC
        """,
        (class_offering_id, int(user["id"]), user["role"]),
    ).fetchall()
    return [serialize_custom_emoji_row(class_offering_id, row) for row in rows]


def resolve_custom_emoji_payloads(conn, class_offering_id: int, emoji_ids: Iterable[int], user: dict) -> list[dict]:
    normalized_ids = []
    for emoji_id in emoji_ids:
        try:
            normalized_ids.append(int(emoji_id))
        except (TypeError, ValueError):
            continue

    if not normalized_ids:
        return []

    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM custom_emojis
        WHERE class_offering_id = ?
          AND owner_user_id = ?
          AND owner_user_role = ?
          AND id IN ({placeholders})
        """,
        (class_offering_id, int(user["id"]), user["role"], *normalized_ids),
    ).fetchall()
    row_map = {int(row["id"]): serialize_custom_emoji_row(class_offering_id, row) for row in rows}
    return [row_map[emoji_id] for emoji_id in normalized_ids if emoji_id in row_map]


def record_emoji_usage(
    conn,
    class_offering_id: int,
    user_id: int,
    user_role: str,
    emoji_type: str,
    emoji_key: str,
    used_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO emoji_usage_stats
        (class_offering_id, user_id, user_role, emoji_type, emoji_key, usage_count, last_used_at, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT (class_offering_id, user_id, user_role, emoji_type, emoji_key)
        DO UPDATE SET
            usage_count = usage_count + 1,
            last_used_at = excluded.last_used_at
        """,
        (class_offering_id, user_id, user_role, emoji_type, emoji_key, used_at, used_at),
    )


def increment_emoji_usage(
    conn,
    class_offering_id: int,
    user: dict,
    unicode_emojis: Iterable[str] | None = None,
    custom_emoji_ids: Iterable[int] | None = None,
    used_at: str | None = None,
) -> None:
    now_value = used_at or datetime.now().isoformat()
    user_id = int(user["id"])
    user_role = str(user["role"])

    for emoji_char in unicode_emojis or []:
        emoji_value = str(emoji_char or "").strip()
        if emoji_value:
            record_emoji_usage(conn, class_offering_id, user_id, user_role, "unicode", emoji_value, now_value)

    for emoji_id in custom_emoji_ids or []:
        try:
            emoji_key = str(int(emoji_id))
        except (TypeError, ValueError):
            continue
        record_emoji_usage(conn, class_offering_id, user_id, user_role, "custom", emoji_key, now_value)


def load_frequent_emojis(conn, class_offering_id: int, user: dict) -> list[dict]:
    rows = conn.execute(
        """
        SELECT emoji_type, emoji_key, usage_count, last_used_at
        FROM emoji_usage_stats
        WHERE class_offering_id = ?
          AND user_id = ?
          AND user_role = ?
        ORDER BY usage_count DESC, last_used_at DESC, id DESC
        LIMIT ?
        """,
        (class_offering_id, int(user["id"]), user["role"], FREQUENT_EMOJI_LIMIT),
    ).fetchall()

    if not rows:
        return []

    custom_ids = [
        int(row["emoji_key"])
        for row in rows
        if row["emoji_type"] == "custom" and str(row["emoji_key"]).isdigit()
    ]
    custom_map = {}
    if custom_ids:
        placeholders = ",".join("?" for _ in custom_ids)
        custom_rows = conn.execute(
            f"""
            SELECT *
            FROM custom_emojis
            WHERE class_offering_id = ?
              AND owner_user_id = ?
              AND owner_user_role = ?
              AND id IN ({placeholders})
            """,
            (class_offering_id, int(user["id"]), user["role"], *custom_ids),
        ).fetchall()
        custom_map = {
            str(int(row["id"])): serialize_custom_emoji_row(class_offering_id, row)
            for row in custom_rows
        }

    frequent_items = []
    for row in rows:
        if row["emoji_type"] == "unicode":
            frequent_items.append({
                "type": "unicode",
                "value": row["emoji_key"],
            })
            continue

        custom_payload = custom_map.get(str(row["emoji_key"]))
        if custom_payload:
            frequent_items.append(custom_payload)

    return frequent_items[:FREQUENT_EMOJI_LIMIT]
