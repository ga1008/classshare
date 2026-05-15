from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
from pathlib import Path
from typing import Callable, TypeVar

from PIL import Image, ImageOps, UnidentifiedImageError

from .file_service import global_file_write_path

CHAT_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
CHAT_IMAGE_THUMBNAIL_SIZE = 64
CHAT_IMAGE_PREVIEW_MAX_SIZE = (1024, 720)
CHAT_IMAGE_MAX_PIXELS = 36_000_000
CHAT_IMAGE_DERIVATIVE_MIME_TYPE = "image/jpeg"
CHAT_IMAGE_DERIVATIVE_PROCESS_LIMIT = max(2, min(4, (os.cpu_count() or 2)))

_image_processing_semaphore = asyncio.Semaphore(CHAT_IMAGE_DERIVATIVE_PROCESS_LIMIT)
_T = TypeVar("_T")


class ChatImageDerivativeError(ValueError):
    """Raised when a chat image cannot be decoded or transformed."""


class ChatImageTooLargeError(ChatImageDerivativeError):
    """Raised when an image is valid but too large for safe server processing."""


async def run_chat_image_processing(func: Callable[..., _T], *args, **kwargs) -> _T:
    async with _image_processing_semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)


def _validate_image_dimensions(width: int | None, height: int | None) -> None:
    if not width or not height:
        raise ChatImageDerivativeError("Invalid chat image")
    if int(width) * int(height) > CHAT_IMAGE_MAX_PIXELS:
        raise ChatImageTooLargeError("Chat image dimensions are too large")


def store_chat_image_derivative_bytes(binary: bytes) -> dict:
    file_hash = hashlib.sha256(binary).hexdigest()
    file_path = global_file_write_path(file_hash)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if not file_path.exists():
        temp_path = file_path.with_name(
            f"{file_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        try:
            temp_path.write_bytes(binary)
            if file_path.exists():
                temp_path.unlink(missing_ok=True)
            else:
                os.replace(temp_path, file_path)
        finally:
            temp_path.unlink(missing_ok=True)

    return {
        "hash": file_hash,
        "path": str(file_path),
        "size": file_path.stat().st_size,
    }


def _flatten_for_jpeg(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _encode_jpeg(image: Image.Image, *, quality: int) -> bytes:
    buffer = io.BytesIO()
    try:
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    except OSError:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _build_thumbnail_image(source: Image.Image) -> Image.Image:
    width, height = source.size
    square_size = min(width, height)
    left = max((width - square_size) // 2, 0)
    top = max((height - square_size) // 2, 0)
    cropped = source.crop((left, top, left + square_size, top + square_size))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    return cropped.resize((CHAT_IMAGE_THUMBNAIL_SIZE, CHAT_IMAGE_THUMBNAIL_SIZE), resampling)


def _build_preview_image(source: Image.Image) -> Image.Image:
    preview = source.copy()
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    preview.thumbnail(CHAT_IMAGE_PREVIEW_MAX_SIZE, resampling)
    return preview


def build_chat_image_derivative_metadata(image: Image.Image, variant: str) -> dict:
    normalized_variant = str(variant or "").lower()
    if normalized_variant == "thumbnail":
        derivative = _build_thumbnail_image(image)
        quality = 82
    elif normalized_variant == "preview":
        derivative = _build_preview_image(image)
        quality = 84
    else:
        raise ValueError(f"Unsupported chat image variant: {variant}")

    derivative = _flatten_for_jpeg(derivative)
    binary = _encode_jpeg(derivative, quality=quality)
    storage = store_chat_image_derivative_bytes(binary)
    return {
        "file_hash": storage["hash"],
        "mime_type": CHAT_IMAGE_DERIVATIVE_MIME_TYPE,
        "file_size": int(storage["size"] or len(binary)),
        "width": int(derivative.size[0] or 0),
        "height": int(derivative.size[1] or 0),
    }


def load_normalized_chat_image(file_path: Path) -> tuple[Image.Image, int, int]:
    try:
        with Image.open(file_path) as image:
            try:
                image.seek(0)
            except EOFError:
                pass
            normalized = ImageOps.exif_transpose(image)
            normalized.load()
            source = normalized.copy()
    except (UnidentifiedImageError, OSError) as exc:
        raise ChatImageDerivativeError("Invalid chat image") from exc

    width, height = source.size
    _validate_image_dimensions(int(width or 0), int(height or 0))
    return source, int(width or 0), int(height or 0)


def prepare_chat_image_derivatives_sync(file_path: Path) -> dict:
    source, width, height = load_normalized_chat_image(file_path)
    try:
        thumbnail = build_chat_image_derivative_metadata(source, "thumbnail")
        preview = build_chat_image_derivative_metadata(source, "preview")
    finally:
        source.close()

    return {
        "width": width,
        "height": height,
        "thumbnail": thumbnail,
        "preview": preview,
    }


async def prepare_chat_image_derivatives(file_path: Path) -> dict:
    return await run_chat_image_processing(prepare_chat_image_derivatives_sync, file_path)


def build_chat_image_derivative_sync(file_path: Path, variant: str) -> dict:
    source, _width, _height = load_normalized_chat_image(file_path)
    try:
        return build_chat_image_derivative_metadata(source, variant)
    finally:
        source.close()


async def build_chat_image_derivative(file_path: Path, variant: str) -> dict:
    return await run_chat_image_processing(build_chat_image_derivative_sync, file_path, variant)
