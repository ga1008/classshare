"""Generate the 散修 geometric avatars (static/img/wanderers/<slug>.svg).

Deterministic from the slug: a SHA-256 of the slug picks two hues, a central
polygon, an orbit of dots and a rotation, so re-running yields identical files
and every wanderer gets a visibly different face. No faces or letters — the
avatar must not pass for a photo of a real student.

Run:  python tools/ui/build_wanderer_avatars.py
"""
from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "static" / "img" / "wanderers"
SIZE = 96
CENTER = SIZE / 2


def _digest(slug: str) -> bytes:
    return hashlib.sha256(f"wanderer-avatar|{slug}".encode("utf-8")).digest()


def _polygon(radius: float, sides: int, rotate: float) -> str:
    points = []
    for index in range(sides):
        angle = math.radians(rotate + 360.0 * index / sides)
        points.append(f"{CENTER + radius * math.cos(angle):.2f},{CENTER + radius * math.sin(angle):.2f}")
    return " ".join(points)


def _orbit(count: int, radius: float, dot: float, rotate: float, color: str) -> str:
    dots = []
    for index in range(count):
        angle = math.radians(rotate + 360.0 * index / count)
        x = CENTER + radius * math.cos(angle)
        y = CENTER + radius * math.sin(angle)
        dots.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{dot:.2f}" fill="{color}"/>')
    return "".join(dots)


def render_avatar(slug: str) -> str:
    data = _digest(slug)
    hue_a = data[0] * 360 // 256
    hue_b = (hue_a + 60 + data[1] % 120) % 360
    sides = 3 + data[2] % 6
    rotate = data[3] * 360 / 256
    dots = 3 + data[4] % 7
    inner_sides = 3 + data[5] % 4
    ring = 26 + data[6] % 8
    bg_a = f"hsl({hue_a}, 62%, 46%)"
    bg_b = f"hsl({hue_b}, 58%, 30%)"
    shape = f"hsl({hue_b}, 80%, 82%)"
    accent = f"hsl({hue_a}, 90%, 88%)"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}" '
        f'role="img" aria-label="散修头像">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg_a}"/><stop offset="1" stop-color="{bg_b}"/></linearGradient></defs>'
        f'<rect width="{SIZE}" height="{SIZE}" fill="url(#g)"/>'
        f'<circle cx="{CENTER}" cy="{CENTER}" r="{ring}" fill="none" stroke="{accent}" stroke-opacity=".35" stroke-width="1.5"/>'
        f'<polygon points="{_polygon(22, sides, rotate)}" fill="{shape}" fill-opacity=".9"/>'
        f'<polygon points="{_polygon(10, inner_sides, rotate + 180)}" fill="{bg_b}" fill-opacity=".85"/>'
        f'{_orbit(dots, ring, 2.6, rotate / 2, accent)}'
        "</svg>\n"
    )


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS

    OUT.mkdir(parents=True, exist_ok=True)
    for seed in WANDERER_SEEDS:
        (OUT / f"{seed['slug']}.svg").write_text(render_avatar(seed["slug"]), encoding="utf-8", newline="\n")
    print(f"wrote {len(WANDERER_SEEDS)} wanderer avatars to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
