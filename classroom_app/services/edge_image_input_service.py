"""Prepare already-authorized current-message assets for inexpensive vision calls.

Authorization belongs to the calling domain service. This module never resolves a
remote URL or searches historical attachments, and never silently drops an image.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from PIL import Image

from .chat_image_derivatives import (
    CHAT_IMAGE_MAX_PIXELS, CHAT_IMAGE_TYPES,
    build_chat_image_derivative_sync, run_chat_image_processing,
)
from .file_service import resolve_global_file_path


def _prepare_images(assets: list[dict], source_feature: str, max_images: int, max_bytes: int) -> list[dict]:
    if len(assets) > max_images:
        raise ValueError(f"本次图片识别最多支持 {max_images} 张图片，请减少图片后再发送")
    result = []
    for index, asset in enumerate(assets, 1):
        file_hash = str(asset.get("file_hash") or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", file_hash):
            raise ValueError("图片附件无效，请重新上传")
        if str(asset.get("mime_type") or "").lower() not in CHAT_IMAGE_TYPES:
            raise ValueError("AI 图片识别仅支持 PNG、JPG、GIF 或 WebP 图片")
        path = resolve_global_file_path(file_hash)
        if path is None or not path.is_file():
            raise ValueError("图片附件已不可用，请重新上传")
        if path.stat().st_size > max_bytes:
            raise ValueError("图片文件过大，请压缩后再发送")
        try:
            # Check the header before the shared decoder allocates the full bitmap.
            with Image.open(path) as probe:
                width, height = probe.size
                if width <= 0 or height <= 0 or width * height > CHAT_IMAGE_MAX_PIXELS:
                    raise ValueError("图片像素过大，请缩小后再发送")
            preview = build_chat_image_derivative_sync(Path(path), "preview")
            preview_path = resolve_global_file_path(preview["file_hash"])
            if preview_path is None:
                raise ValueError("图片预览不可用，请重新上传")
            encoded = base64.b64encode(preview_path.read_bytes()).decode("ascii")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("图片无法解码，请重新上传 PNG、JPG、GIF 或 WebP 图片") from exc
        result.append({
            "url": f"data:image/jpeg;base64,{encoded}",
            "name": str(asset.get("original_filename") or f"图片 {index}"),
            "mime_type": "image/jpeg",
            "source_kind": "current_message_attachment",
            "source_label": source_feature,
            "image_index": index,
        })
    return result


async def prepare_edge_image_inputs(
    assets: list[dict], *, source_feature: str, max_images: int, max_bytes: int,
) -> list[dict]:
    if not assets:
        return []
    return await run_chat_image_processing(_prepare_images, assets, source_feature, max_images, max_bytes)
