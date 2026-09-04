"""Per-offering group QR metadata, membership checks and verified image storage."""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from .file_service import store_file_object_globally
from .offering_membership_service import student_offering_where_by_student_id

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_DESCRIPTION_LENGTH = 1000
IMAGE_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
CONFLICT_MESSAGE = "班群信息已在其他页面更新，请查看最新内容后再保存。"


def _normalize_description(description: str) -> str:
    # Multipart browsers send CRLF while textarea.value exposes LF. Keep both
    # newly saved settings and legacy API responses in the same canonical form.
    return description.replace("\r\n", "\n").replace("\r", "\n")


def load_group_qr_offering(conn, offering_id: int, user: dict, *, edit: bool = False) -> dict:
    role = user.get("role")
    if edit and role != "teacher":
        raise HTTPException(403, "仅本课堂教师可以设置班群二维码。")
    if role == "teacher":
        access_sql = "o.teacher_id = ?"
    elif role == "student":
        access_sql = student_offering_where_by_student_id(require_active=True)
    else:
        raise HTTPException(403, "无权访问该课堂。")
    # Both supported databases store IDs as signed 64-bit integers. Reject
    # unbounded URL integers before binding them to a database query.
    if not 0 < offering_id <= 9223372036854775807:
        raise HTTPException(404, "课堂不存在或无权访问。")
    row = conn.execute(
        f"SELECT o.* FROM class_offerings o WHERE o.id = ? AND {access_sql}",
        (offering_id, int(user["id"])),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "课堂不存在或无权访问。")
    return dict(row)


def serialize_group_qr(offering: dict) -> dict:
    file_hash = str(offering.get("group_qr_file_hash") or "")
    return {
        "image_url": (
            f"/api/classrooms/{offering['id']}/group-qr/image?v={file_hash}" if file_hash else ""
        ),
        "description": _normalize_description(str(offering.get("group_qr_description") or "")),
        "revision": str(offering.get("group_qr_revision") or ""),
    }


def validate_group_qr_image(file: UploadFile) -> str:
    stream = file.file
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if not 0 < size <= MAX_IMAGE_BYTES:
        raise HTTPException(400, "请选择不超过 5 MB 的二维码图片。")
    try:
        with Image.open(stream) as image:
            mime_type = IMAGE_MIME_TYPES.get(image.format)
            if not mime_type or getattr(image, "is_animated", False):
                raise HTTPException(400, "请上传 PNG、JPG 或 WebP 静态图片。")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(400, "图片分辨率过大，请使用不超过 1200 万像素的图片。")
            # Structural verification catches damaged PNG chunks that decoding
            # alone can ignore. Reopen afterwards to also fully decode pixels.
            image.verify()
        stream.seek(0)
        with Image.open(stream) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "图片无法读取，请重新选择有效的二维码图片。") from exc
    finally:
        stream.seek(0)
    return mime_type


def update_group_qr(
    conn,
    offering_id: int,
    user: dict,
    *,
    description: str,
    revision: str,
    file: UploadFile | None = None,
    remove_image: bool = False,
) -> dict:
    offering = load_group_qr_offering(conn, offering_id, user, edit=True)
    description = _normalize_description(description)
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(400, "班群简介不能超过 1000 字。")
    if remove_image and file is not None:
        raise HTTPException(400, "不能同时移除和上传二维码，请选择一种操作。")
    if revision != str(offering.get("group_qr_revision") or ""):
        raise HTTPException(409, CONFLICT_MESSAGE)
    file_hash = str(offering.get("group_qr_file_hash") or "")
    mime_type = str(offering.get("group_qr_mime_type") or "")
    if remove_image:
        # Images use shared content-addressed storage. Only detach the reference;
        # another classroom or resource can still reference the same bytes.
        file_hash, mime_type = "", ""
    elif file is not None:
        mime_type = validate_group_qr_image(file)
        # Use the shared atomic, content-addressed store. Preserve the original
        # pixels and quiet zone; QR codes must never be cropped or recompressed.
        file_hash = store_file_object_globally(file.file)["hash"]
    new_revision = uuid4().hex
    cursor = conn.execute(
        """UPDATE class_offerings
           SET group_qr_file_hash = ?, group_qr_mime_type = ?,
               group_qr_description = ?, group_qr_revision = ?
           WHERE id = ? AND teacher_id = ? AND COALESCE(group_qr_revision, '') = ?""",
        (file_hash, mime_type, description.strip(), new_revision, offering_id, int(user["id"]), revision),
    )
    if cursor.rowcount != 1:
        raise HTTPException(409, CONFLICT_MESSAGE)
    offering.update(
        group_qr_file_hash=file_hash,
        group_qr_mime_type=mime_type,
        group_qr_description=description.strip(),
        group_qr_revision=new_revision,
    )
    return serialize_group_qr(offering)
