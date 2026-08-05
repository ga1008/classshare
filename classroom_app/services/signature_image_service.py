"""Signature image hygiene: upload normalization and watermarked previews.

Two jobs, both pure image plumbing:

1. ``normalize_upload_image`` — self-service uploads are validated (minimum
   size, not blank), auto-trimmed, converted white-background→transparent and
   re-encoded as PNG so the library stays clean and exports composite well.
   Document-import harvesting deliberately bypasses this (its dedupe relies on
   the original bytes).

2. ``ensure_preview`` — a content-addressed, watermarked, downscaled preview
   under ``SIGNATURES_DIR/_previews``. Browsing surfaces (cards, detail panel,
   claim review) serve this instead of the original autograph; the original is
   only reachable for users who may directly use the signature or through the
   registered export pipeline.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ..config import SIGNATURES_DIR


MIN_WIDTH = 60
MIN_HEIGHT = 30
MAX_DIMENSION = 4000
# Fraction of pixels that must carry ink for the image to count as a signature.
MIN_INK_RATIO = 0.001
WHITE_THRESHOLD = 235
CROP_MARGIN_RATIO = 0.04
PREVIEW_MAX_WIDTH = 420
PREVIEW_MAX_HEIGHT = 220

_CJK_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


class SignatureImageError(Exception):
    """Raised for user-facing validation problems; message is display-ready."""


def preview_dir() -> Path:
    return SIGNATURES_DIR / "_previews"


def preview_path(file_hash: str) -> Path:
    normalized = str(file_hash or "").strip().lower()
    return preview_dir() / f"{normalized}.png"


def _load_image(data: bytes):
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise SignatureImageError("图片无法解析，请上传有效的 PNG/JPG 签名图。") from exc
    return image


def normalize_upload_image(data: bytes) -> tuple[bytes, str, str]:
    """Validate + auto-clean a self-service signature upload.

    Returns ``(png_bytes, ".png", "image/png")``. Raises SignatureImageError
    with an actionable message when the image cannot pass as a signature.
    """
    from PIL import ImageChops

    image = _load_image(data)
    if image.width < MIN_WIDTH or image.height < MIN_HEIGHT:
        raise SignatureImageError(
            f"签名图片太小（{image.width}×{image.height}），至少需要 {MIN_WIDTH}×{MIN_HEIGHT} 像素。"
        )
    if max(image.width, image.height) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    gray = rgba.convert("L")
    # Near-white pixels become fully transparent; everything else keeps its
    # original alpha. Colored pens (blue/black) sit far below the threshold.
    ink_mask = gray.point(lambda value: 0 if value >= WHITE_THRESHOLD else 255)
    new_alpha = ImageChops.multiply(alpha, ink_mask)
    rgba.putalpha(new_alpha)

    histogram = new_alpha.histogram()
    ink_pixels = sum(histogram[33:])
    total_pixels = rgba.width * rgba.height
    if not total_pixels or ink_pixels / total_pixels < MIN_INK_RATIO:
        raise SignatureImageError("图片内容近乎空白，未检测到签名笔迹，请重新拍摄或扫描。")

    bbox = new_alpha.getbbox()
    if bbox is None:
        raise SignatureImageError("图片内容近乎空白，未检测到签名笔迹，请重新拍摄或扫描。")
    left, top, right, bottom = bbox
    margin_x = max(4, int((right - left) * CROP_MARGIN_RATIO))
    margin_y = max(4, int((bottom - top) * CROP_MARGIN_RATIO))
    crop_box = (
        max(0, left - margin_x),
        max(0, top - margin_y),
        min(rgba.width, right + margin_x),
        min(rgba.height, bottom + margin_y),
    )
    cropped = rgba.crop(crop_box)
    if cropped.width < MIN_WIDTH or cropped.height < MIN_HEIGHT:
        raise SignatureImageError("裁剪掉空白后签名区域太小，请上传更清晰、更大的签名图。")

    output = io.BytesIO()
    cropped.save(output, format="PNG", optimize=True)
    return output.getvalue(), ".png", "image/png"


def _watermark_font(size: int):
    from PIL import ImageFont

    for candidate in _CJK_FONT_CANDIDATES:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size), True
            except OSError:
                continue
    return ImageFont.load_default(), False


def _apply_watermark(image: Any) -> Any:
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_size = max(14, image.height // 6)
    font, has_cjk = _watermark_font(font_size)
    text = "仅供预览" if has_cjk else "PREVIEW"
    step_x = max(90, image.width // 3)
    step_y = max(40, image.height // 3)
    for y in range(0, image.height + step_y, step_y):
        for x in range(-step_x, image.width + step_x, step_x):
            draw.text((x + (y // step_y % 2) * step_x // 2, y), text, font=font, fill=(100, 116, 139, 88))
    rotated = overlay.rotate(18, expand=False)
    return Image.alpha_composite(image.convert("RGBA"), rotated)


def build_preview_bytes(source_path: Path) -> bytes:
    """Downscaled + watermarked rendition of a signature image."""
    with open(source_path, "rb") as handle:
        image = _load_image(handle.read())
    preview = image.convert("RGBA")
    preview.thumbnail((PREVIEW_MAX_WIDTH, PREVIEW_MAX_HEIGHT))
    watermarked = _apply_watermark(preview)
    output = io.BytesIO()
    watermarked.save(output, format="PNG", optimize=True)
    return output.getvalue()


def ensure_preview(file_hash: str, source_path: Path) -> Path:
    """Content-addressed preview cache; regenerating is cheap and lazy."""
    target = preview_path(file_hash)
    if target.is_file():
        return target
    data = build_preview_bytes(source_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.tmp")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
        temp_path.replace(target)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
    return target
