"""The social content-addressed media store (social doc §0, §6).

Bytes live under ``DATA_ROOT/media/social/sha256/ab/cd/<hash>`` and are counted
only by social tables (``social_media_blobs.ref_count``), which is what makes
the 1-year retention and the disk guard safe to physically delete. Bytes that
already exist in the global store (``GLOBAL_FILES_DIR``) are referenced
read-only and are NEVER deleted here (docs/global-file-reference-protocol.md):
every unlink in this module is checked to sit under ``SOCIAL_STORE_ROOT``.

Publication reuses the global store's pattern: write a same-directory
temporary file, then hard-link (or replace) it onto the hash path, so no
reader ever sees a partial file.
"""
from __future__ import annotations

import errno
import hashlib
import io
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..storage_paths import DATA_ROOT
from .social_common import ensure_schema, now_iso

SOCIAL_STORE_ROOT = DATA_ROOT / "media" / "social" / "sha256"
UPLOAD_TEMP_ROOT = DATA_ROOT / "tmp" / "social_uploads"
HASH_CHUNK = 1024 * 1024
THUMB_LONG_SIDE = 480
THUMB_QUALITY = 80
WEBP_LONG_SIDE = 2048
WEBP_QUALITY = 70

STATE_READY = "ready"
STATE_COMPRESSED = "compressed"
STATE_DELETED = "deleted"


def valid_hash(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def blob_path(file_hash: str) -> Path:
    normalized = str(file_hash or "").strip().lower()
    if not valid_hash(normalized):
        raise ValueError("文件标识无效。")
    return Path(SOCIAL_STORE_ROOT) / normalized[:2] / normalized[2:4] / normalized


def is_inside_social_store(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(SOCIAL_STORE_ROOT).resolve())
    except (ValueError, OSError):
        return False
    return True


def upload_temp_path(upload_id: str) -> Path:
    safe = "".join(ch for ch in str(upload_id) if ch.isalnum())
    if not safe:
        raise ValueError("上传会话无效。")
    return Path(UPLOAD_TEMP_ROOT) / f"{safe}.part"


def hash_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _link_or_replace(temporary: str, target: Path, size: int, *, replace: bool) -> None:
    if replace:
        os.replace(temporary, target)
        return
    try:
        os.link(temporary, target)
    except FileExistsError:
        return
    except OSError as exc:
        if exc.errno not in {errno.ENOSYS, errno.ENOTSUP, errno.EXDEV} and getattr(exc, "winerror", None) not in {1, 50}:
            raise
        try:
            os.replace(temporary, target)
        except PermissionError:
            if not target.is_file() or target.stat().st_size != size:
                raise


def publish_file(source: Path, file_hash: str, *, replace: bool = False) -> Path:
    """Atomically publish a verified file onto its hash path (source is left untouched)."""
    target = blob_path(file_hash)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".social-", delete=False) as output:
            temporary = output.name
            with open(source, "rb") as handle:
                shutil.copyfileobj(handle, output, HASH_CHUNK)
        _link_or_replace(temporary, target, Path(source).stat().st_size, replace=replace)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target


def publish_bytes(data: bytes) -> tuple[str, Path]:
    file_hash = hashlib.sha256(data).hexdigest()
    target = blob_path(file_hash)
    if target.is_file():
        return file_hash, target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".social-", delete=False) as output:
            temporary = output.name
            output.write(data)
        _link_or_replace(temporary, target, len(data), replace=False)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return file_hash, target


def delete_blob_file(file_hash: str) -> bool:
    """Unlink a social-store blob. Refuses anything outside the social store."""
    try:
        path = blob_path(file_hash)
    except ValueError:
        return False
    if not is_inside_social_store(path):
        print(f"[SOCIAL_MEDIA] refused to delete outside the social store: {path}")
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[SOCIAL_MEDIA] delete failed for {file_hash}: {exc!r}")
        return False
    return True


# ---------------------------------------------------------------------------
# Blob rows (social store only)
# ---------------------------------------------------------------------------

def load_blob(conn: Any, file_hash: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM social_media_blobs WHERE blob_hash = ? LIMIT 1", (file_hash,)).fetchone()
    return {key: row[key] for key in row.keys()} if row is not None else None


def blob_available(conn: Any, file_hash: str, size: Optional[int] = None) -> bool:
    """Original (uncompressed) bytes are present in the social store."""
    blob = load_blob(conn, file_hash)
    if not blob or blob["storage_state"] != STATE_READY:
        return False
    path = blob_path(file_hash)
    return path.is_file() and (size is None or path.stat().st_size == int(size))


def upsert_blob_ready(conn: Any, file_hash: str, size: int, mime: str) -> None:
    timestamp = now_iso()
    conn.execute(
        "INSERT INTO social_media_blobs (blob_hash, file_size, mime_type, storage_state, ref_count, first_seen_at) "
        "VALUES (?, ?, ?, ?, 0, ?) ON CONFLICT (blob_hash) DO UPDATE SET file_size = excluded.file_size, "
        "mime_type = excluded.mime_type, storage_state = excluded.storage_state, deleted_at = NULL",
        (file_hash, int(size), mime, STATE_READY, timestamp),
    )


def upsert_blob(conn: Any, file_hash: str, size: int, mime: str, state: str) -> None:
    """Create or revive a blob row in ``state`` (ref_count is kept for an existing row)."""
    conn.execute(
        "INSERT INTO social_media_blobs (blob_hash, file_size, mime_type, storage_state, ref_count, first_seen_at) "
        "VALUES (?, ?, ?, ?, 0, ?) ON CONFLICT (blob_hash) DO UPDATE SET file_size = excluded.file_size, "
        "mime_type = excluded.mime_type, storage_state = excluded.storage_state, deleted_at = NULL",
        (file_hash, int(size), mime, state, now_iso()),
    )


def add_blob_ref(conn: Any, file_hash: str, delta: int) -> None:
    if not file_hash:
        return
    conn.execute(
        "UPDATE social_media_blobs SET ref_count = CASE WHEN ref_count + ? < 0 THEN 0 ELSE ref_count + ? END, "
        "last_referenced_at = ? WHERE blob_hash = ?",
        (int(delta), int(delta), now_iso(), file_hash),
    )


# ---------------------------------------------------------------------------
# Image derivatives
# ---------------------------------------------------------------------------

def build_thumbnail_bytes(source: Path) -> bytes:
    """JPEG, long side ≤ 480, EXIF-rotated; raises ChatImageDerivativeError for bad images."""
    from PIL import Image

    from .chat_image_derivatives import load_normalized_chat_image

    image, _width, _height = load_normalized_chat_image(source)
    try:
        image.thumbnail((THUMB_LONG_SIDE, THUMB_LONG_SIDE), getattr(Image, "Resampling", Image).LANCZOS)
        if image.mode not in {"RGB", "L"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        return buffer.getvalue()
    finally:
        image.close()


def webp_bytes(path: Path) -> bytes:
    """An image re-encoded as WebP (quality 70, long side 2048); the source is left untouched.

    The result is a different file with its own SHA-256: the disk guard publishes
    it as a new blob and repoints media rows to it (content addressing stays true).
    """
    from PIL import Image

    from .chat_image_derivatives import load_normalized_chat_image

    if not is_inside_social_store(path):
        raise ValueError("只能重压社交库中的文件。")
    image, _width, _height = load_normalized_chat_image(path)
    try:
        image.thumbnail((WEBP_LONG_SIDE, WEBP_LONG_SIDE), getattr(Image, "Resampling", Image).LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=WEBP_QUALITY)
    finally:
        image.close()
    return buffer.getvalue()


def lock_blobs(conn: Any, hashes: Any) -> None:
    """Per-blob mutual exclusion for attach / token issue / publish / purge (transaction-scoped)."""
    from .social_common import serialize_on

    serialize_on(conn, "social-blob", [str(item) for item in hashes or () if item])


def live_token_exists(conn: Any, file_hash: str) -> bool:
    """An unused, social-store token still points at ``file_hash`` (it may be redeemed any moment)."""
    row = conn.execute("SELECT 1 FROM social_media_tokens WHERE blob_hash = ? AND store = 'social' "
                       "AND used_at IS NULL LIMIT 1", (file_hash,)).fetchone()
    return row is not None
