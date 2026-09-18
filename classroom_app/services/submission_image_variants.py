"""Compressed thumbnail / preview variants for student attachment images.

Variants are built lazily from the stored attachment, encoded as JPEG and
cached in the global content-addressed file store under a hash derived from
the *source* hash, so a screenshot that is uploaded twice (draft → final
submission, or the same file in two assignments) shares one cached variant and
nothing has to be tracked in the database.
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
from pathlib import Path

from PIL import Image

from .chat_image_derivatives import (
    ChatImageDerivativeError,
    _encode_jpeg,
    _flatten_for_jpeg,
    load_normalized_chat_image,
    run_chat_image_processing,
)
from .file_service import global_file_write_path, resolve_global_file_path
from .submission_file_alignment import _file_hash_sha256

VARIANT_MIME_TYPE = "image/jpeg"
VARIANT_CACHE_VERSION = "v1"
IMAGE_VARIANTS: dict[str, dict[str, object]] = {
    "thumb": {"max_size": (360, 360), "quality": 80},
    "preview": {"max_size": (1600, 1600), "quality": 86},
}


def normalize_variant(value: str | None) -> str:
    variant = str(value or "thumb").strip().lower()
    return variant if variant in IMAGE_VARIANTS else "thumb"


def variant_cache_hash(source_hash: str, variant: str) -> str:
    key = f"lanshare-image-variant:{VARIANT_CACHE_VERSION}:{normalize_variant(variant)}:{source_hash.lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _write_bytes_atomically(target: Path, binary: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        temp_path.write_bytes(binary)
        if target.exists():
            temp_path.unlink(missing_ok=True)
        else:
            os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def _build_variant_bytes(source_path: Path, variant: str) -> bytes:
    spec = IMAGE_VARIANTS[normalize_variant(variant)]
    source, _width, _height = load_normalized_chat_image(source_path)
    try:
        derivative = source.copy()
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        derivative.thumbnail(tuple(spec["max_size"]), resampling)  # type: ignore[arg-type]
        derivative = _flatten_for_jpeg(derivative)
        return _encode_jpeg(derivative, quality=int(spec["quality"]))  # type: ignore[arg-type]
    finally:
        source.close()


def _resolve_variant_sync(source_path: Path, source_hash: str, variant: str) -> Path | None:
    normalized_variant = normalize_variant(variant)
    digest = str(source_hash or "").strip().lower()
    if len(digest) != 64:
        digest = _file_hash_sha256(source_path)
    cache_hash = variant_cache_hash(digest, normalized_variant)
    cached = resolve_global_file_path(cache_hash)
    if cached is not None:
        return cached
    try:
        binary = _build_variant_bytes(source_path, normalized_variant)
    except ChatImageDerivativeError:
        return None
    except (OSError, ValueError):
        return None
    target = global_file_write_path(cache_hash)
    _write_bytes_atomically(target, binary)
    return target


async def resolve_submission_image_variant(source_path: Path, source_hash: str, variant: str) -> Path | None:
    """Return the cached variant path, building it on first use.

    ``None`` means the source could not be decoded (corrupt, unsupported or
    oversized image) and the caller should fall back to the original file.
    """
    if not source_path or not Path(source_path).is_file():
        return None
    return await run_chat_image_processing(_resolve_variant_sync, Path(source_path), source_hash, variant)


def build_variant_bytes_for_tests(binary: bytes, variant: str = "thumb") -> bytes:
    """Small helper used by tests to exercise the encoder without touching disk."""
    spec = IMAGE_VARIANTS[normalize_variant(variant)]
    with Image.open(io.BytesIO(binary)) as image:
        derivative = image.copy()
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    derivative.thumbnail(tuple(spec["max_size"]), resampling)  # type: ignore[arg-type]
    return _encode_jpeg(_flatten_for_jpeg(derivative), quality=int(spec["quality"]))  # type: ignore[arg-type]
