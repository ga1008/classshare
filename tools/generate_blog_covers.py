# -*- coding: utf-8 -*-
"""Generate stock cover art for the blog center (paper-desk skin).

Produces static/img/blog-covers/{section}-{1..4}.svg — four abstract
compositions per section, tinted with the section accent color. The
frontend picks one deterministically (post id % 4) when a post has no
cover image, so empty media slots never appear.

Re-run whenever sections/accents change:
    python tools/generate_blog_covers.py
"""
from __future__ import annotations

import os
import random

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'static', 'img', 'blog-covers')

# Must mirror sectionByKey defaults in static/js/blog.js.
SECTIONS = {
    'general': '#2563eb',
    'technology': '#0f766e',
    'humanities': '#b45309',
    'computer': '#4f46e5',
    'ai': '#7c3aed',
    'career': '#e11d48',
    'default': '#64748b',
}

W, H = 800, 600


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgba(color: str, alpha: float) -> str:
    r, g, b = hex_to_rgb(color)
    return f'rgba({r},{g},{b},{alpha:g})'


def mix_with_white(color: str, ratio: float) -> str:
    """ratio = share of the accent color; the rest is white."""
    r, g, b = hex_to_rgb(color)
    mixed = tuple(round(255 - (255 - channel) * ratio) for channel in (r, g, b))
    return '#%02x%02x%02x' % mixed


def svg_open(accent: str) -> str:
    bg_top = mix_with_white(accent, 0.05)
    bg_bottom = mix_with_white(accent, 0.14)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'preserveAspectRatio="xMidYMid slice" role="img" aria-label="文章配图">'
        '<defs>'
        f'<linearGradient id="bg" x1="0" y1="0" x2="0.4" y2="1">'
        f'<stop offset="0" stop-color="{bg_top}"/><stop offset="1" stop-color="{bg_bottom}"/>'
        '</linearGradient>'
        '<filter id="soft" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="46"/></filter>'
        '</defs>'
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>'
    )


def variant_waves(accent: str, rng: random.Random) -> str:
    parts = []
    base_y = rng.randint(330, 400)
    for i, alpha in enumerate((0.32, 0.2, 0.12)):
        y = base_y + i * rng.randint(55, 75)
        c1x, c2x = rng.randint(140, 260), rng.randint(480, 640)
        lift = rng.randint(70, 130)
        parts.append(
            f'<path d="M0 {y} C {c1x} {y - lift}, {c2x} {y + lift}, {W} {y - rng.randint(10, 60)} '
            f'L {W} {H} L 0 {H} Z" fill="{rgba(accent, alpha)}"/>'
        )
    parts.append(
        f'<circle cx="{rng.randint(560, 700)}" cy="{rng.randint(90, 170)}" '
        f'r="{rng.randint(60, 90)}" fill="{rgba(accent, 0.3)}" filter="url(#soft)"/>'
    )
    parts.append(
        f'<circle cx="{rng.randint(590, 690)}" cy="{rng.randint(100, 160)}" '
        f'r="{rng.randint(22, 30)}" fill="none" stroke="{rgba(accent, 0.4)}" stroke-width="3"/>'
    )
    return ''.join(parts)


def variant_blobs(accent: str, rng: random.Random) -> str:
    parts = []
    spots = [
        (rng.randint(90, 220), rng.randint(120, 240), rng.randint(130, 180), 0.34),
        (rng.randint(520, 680), rng.randint(300, 460), rng.randint(150, 210), 0.26),
        (rng.randint(340, 470), rng.randint(60, 150), rng.randint(80, 120), 0.18),
    ]
    for cx, cy, r, alpha in spots:
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{rgba(accent, alpha)}" filter="url(#soft)"/>')
    ring_x, ring_y = rng.randint(540, 660), rng.randint(120, 200)
    parts.append(
        f'<circle cx="{ring_x}" cy="{ring_y}" r="{rng.randint(52, 70)}" fill="none" '
        f'stroke="{rgba(accent, 0.45)}" stroke-width="2.5" stroke-dasharray="1 9" stroke-linecap="round"/>'
    )
    parts.append(
        f'<line x1="{rng.randint(60, 120)}" y1="{rng.randint(430, 500)}" x2="{rng.randint(240, 340)}" '
        f'y2="{rng.randint(430, 500)}" stroke="{rgba(accent, 0.4)}" stroke-width="4" stroke-linecap="round"/>'
    )
    return ''.join(parts)


def variant_dots(accent: str, rng: random.Random) -> str:
    parts = [
        f'<circle cx="{rng.randint(560, 700)}" cy="{rng.randint(360, 470)}" '
        f'r="{rng.randint(150, 200)}" fill="{rgba(accent, 0.24)}" filter="url(#soft)"/>'
    ]
    start_x, start_y = rng.randint(70, 120), rng.randint(80, 130)
    for row in range(5):
        for col in range(7):
            if rng.random() < 0.22:
                continue
            alpha = 0.14 + 0.3 * ((row + col) % 3) / 2
            parts.append(
                f'<circle cx="{start_x + col * 52}" cy="{start_y + row * 52}" '
                f'r="{rng.choice((3, 4, 5))}" fill="{rgba(accent, alpha)}"/>'
            )
    x = rng.randint(430, 520)
    parts.append(
        f'<path d="M {x} {H} L {x + 190} {H - 260} L {x + 260} {H}" fill="none" '
        f'stroke="{rgba(accent, 0.35)}" stroke-width="3"/>'
    )
    return ''.join(parts)


def variant_arcs(accent: str, rng: random.Random) -> str:
    parts = []
    cx, cy = rng.choice(((0, H), (W, 0)))
    for i, radius in enumerate(range(rng.randint(120, 150), 640, 92)):
        alpha = max(0.08, 0.4 - i * 0.07)
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
            f'stroke="{rgba(accent, alpha)}" stroke-width="{rng.choice((2, 3, 10))}"/>'
        )
    parts.append(
        f'<circle cx="{W - cx if cx else W - 140}" cy="{rng.randint(120, 200) if cx else H - 160}" '
        f'r="{rng.randint(70, 100)}" fill="{rgba(accent, 0.28)}" filter="url(#soft)"/>'
    )
    return ''.join(parts)


VARIANTS = (variant_waves, variant_blobs, variant_dots, variant_arcs)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for section, accent in SECTIONS.items():
        for index, build in enumerate(VARIANTS, start=1):
            rng = random.Random(f'{section}:{index}')
            svg = svg_open(accent) + build(accent, rng) + '</svg>'
            path = os.path.join(OUT_DIR, f'{section}-{index}.svg')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(svg)
            print(f'wrote {path} ({len(svg)} bytes)')


if __name__ == '__main__':
    main()
