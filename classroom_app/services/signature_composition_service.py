"""Deterministic, balanced composition for ordered multi-person signatures."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from ..config import SIGNATURES_DIR
from . import signature_service


def resolve_signature_paths(conn: Any, signature_ids: Iterable[Any]) -> list[str]:
    paths: list[str] = []
    seen: set[int] = set()
    for value in signature_ids:
        try:
            signature_id = int(value)
        except (TypeError, ValueError):
            continue
        if signature_id <= 0 or signature_id in seen:
            continue
        seen.add(signature_id)
        row = conn.execute(
            """
            SELECT * FROM electronic_signatures
            WHERE id = ? AND status = 'active' AND deleted_at IS NULL LIMIT 1
            """,
            (signature_id,),
        ).fetchone()
        if not row:
            continue
        path = signature_service.resolve_signature_file_path(row)
        if path:
            paths.append(str(path))
    return paths


def compose_signature_strip(
    image_paths: Iterable[str],
    *,
    slot_width: int = 360,
    height: int = 150,
    gap: int = 18,
) -> str:
    """Return one transparent PNG containing signatures in the given order.

    Every signer receives an equal-width slot.  Content is trimmed, centered
    and fitted without distortion, so a wide autograph cannot visually crowd
    out a compact one.  Derived images are content-addressed and safely reused.
    """
    paths = [Path(value) for value in image_paths if str(value or "").strip()]
    paths = [path for path in paths if path.is_file()]
    if not paths:
        return ""
    if len(paths) == 1:
        return str(paths[0])
    from PIL import Image

    normalized_slot_width = max(120, min(int(slot_width), 600))
    normalized_height = max(60, min(int(height), 320))
    normalized_gap = max(4, min(int(gap), 48))
    digest = hashlib.sha256()
    digest.update(f"signature-strip-v1:{normalized_slot_width}:{normalized_height}:{normalized_gap}".encode())
    for path in paths:
        digest.update(path.read_bytes())
    output_dir = Path(SIGNATURES_DIR) / "_composites"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{digest.hexdigest()}.png"
    if output_path.is_file() and output_path.stat().st_size > 0:
        return str(output_path)

    canvas_width = normalized_slot_width * len(paths) + normalized_gap * (len(paths) - 1)
    canvas = Image.new("RGBA", (canvas_width, normalized_height), (255, 255, 255, 0))
    padding_x = max(8, normalized_slot_width // 18)
    padding_y = max(5, normalized_height // 14)
    target_width = normalized_slot_width - padding_x * 2
    target_height = normalized_height - padding_y * 2
    for index, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert("RGBA")
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            if bbox:
                image = image.crop(bbox)
            image.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
            slot_left = index * (normalized_slot_width + normalized_gap)
            x = slot_left + (normalized_slot_width - image.width) // 2
            y = (normalized_height - image.height) // 2
            canvas.alpha_composite(image, (x, y))
    temp_path = output_path.with_suffix(".tmp.png")
    canvas.save(temp_path, format="PNG", optimize=True)
    temp_path.replace(output_path)
    return str(output_path)
