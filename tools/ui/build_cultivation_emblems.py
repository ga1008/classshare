"""Generate the ten cultivation level emblems (修为境界标志).

Single source for static/img/cultivation/emblems/<level_key>.svg. Each emblem is a
self-contained 64x64 SVG with its own dark backing so it reads on light and dark
pages alike; later tiers add rotating halos, glow and sparkle. All motion lives in
the SVG's own <style> and stops under prefers-reduced-motion.

Run:  python tools/ui/build_cultivation_emblems.py
"""
from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "static" / "img" / "cultivation" / "emblems"

BASE_STYLE = """
.spin{transform-box:view-box;transform-origin:32px 32px;animation:spin 24s linear infinite}
.spin-r{transform-box:view-box;transform-origin:32px 32px;animation:spin 18s linear infinite reverse}
.spin-f{transform-box:view-box;transform-origin:32px 32px;animation:spin 12s linear infinite}
.pulse{transform-box:fill-box;transform-origin:center;animation:pulse 2.6s ease-in-out infinite}
.glint{animation:glint 2.4s ease-in-out infinite}
.flick{transform-box:fill-box;transform-origin:50% 100%;animation:flick 1.6s ease-in-out infinite}
.twinkle{transform-box:fill-box;transform-origin:center;animation:twinkle 1.8s ease-in-out infinite}
.sweep{animation:sweep 3.6s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.07)}}
@keyframes glint{0%,100%{opacity:.35}50%{opacity:1}}
@keyframes flick{0%,100%{transform:scale(1,1)}35%{transform:scale(.96,1.06)}70%{transform:scale(1.03,.95)}}
@keyframes twinkle{0%,100%{opacity:.2;transform:scale(.7)}50%{opacity:1;transform:scale(1)}}
@keyframes sweep{0%{transform:translateX(-70px)}60%,100%{transform:translateX(70px)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
"""


def polygon(cx: float, cy: float, radius: float, sides: int, rotate: float = -90.0) -> str:
    pts = []
    for i in range(sides):
        angle = math.radians(rotate + 360.0 * i / sides)
        pts.append(f"{cx + radius * math.cos(angle):.2f},{cy + radius * math.sin(angle):.2f}")
    return " ".join(pts)


def star(cx: float, cy: float, outer: float, inner: float, points: int, rotate: float = -90.0) -> str:
    pts = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(rotate + 180.0 * i / points)
        pts.append(f"{cx + radius * math.cos(angle):.2f},{cy + radius * math.sin(angle):.2f}")
    return " ".join(pts)


def rays(count: int, r1: float, r2: float, *, alt: float | None = None, **attrs: str) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    lines = []
    for i in range(count):
        angle = math.radians(360.0 * i / count - 90)
        outer = alt if (alt is not None and i % 2) else r2
        lines.append(
            f'<line x1="{32 + r1 * math.cos(angle):.2f}" y1="{32 + r1 * math.sin(angle):.2f}" '
            f'x2="{32 + outer * math.cos(angle):.2f}" y2="{32 + outer * math.sin(angle):.2f}" {extra}/>'
        )
    return "".join(lines)


def sparkle(x: float, y: float, size: float, fill: str, delay: float = 0.0) -> str:
    s = size
    path = f"M{x:.2f} {y - s:.2f} Q{x:.2f} {y:.2f} {x + s:.2f} {y:.2f} Q{x:.2f} {y:.2f} {x:.2f} {y + s:.2f} Q{x:.2f} {y:.2f} {x - s:.2f} {y:.2f} Q{x:.2f} {y:.2f} {x:.2f} {y - s:.2f}Z"
    style = f' style="animation-delay:{delay}s"' if delay else ""
    return f'<path d="{path}" fill="{fill}" class="twinkle"{style}/>'


def radial(id_: str, stops: list[tuple[str, str]], cx: str = "50%", cy: str = "40%", r: str = "65%") -> str:
    inner = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<radialGradient id="{id_}" cx="{cx}" cy="{cy}" r="{r}">{inner}</radialGradient>'


def linear(id_: str, stops: list[tuple[str, str]], x2: str = "1", y2: str = "1") -> str:
    inner = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{id_}" x1="0" y1="0" x2="{x2}" y2="{y2}">{inner}</linearGradient>'


GLOW = (
    '<filter id="glow" x="-50%" y="-50%" width="200%" height="200%">'
    '<feGaussianBlur stdDeviation="1.6" result="b"/>'
    '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
)


def emblem_qi_awakening() -> tuple[str, str]:
    defs = radial("base", [("0", "#163f5c"), ("1", "#07131f")]) + linear("rim", [("0", "#bae6fd"), ("1", "#0284c7")])
    body = (
        '<circle cx="32" cy="32" r="30" fill="url(#base)"/>'
        '<circle cx="32" cy="32" r="28.5" fill="none" stroke="url(#rim)" stroke-width="3"/>'
        '<path d="M19 47 Q32 52 45 47" fill="none" stroke="#0ea5e9" stroke-width="2.4" stroke-linecap="round"/>'
        '<path d="M32 48 C32 42 32 37 32 31" fill="none" stroke="#7dd3fc" stroke-width="2.8" stroke-linecap="round"/>'
        '<path d="M32 40 C24 39 20 33 21 27 C28 28 32 33 32 40Z" fill="#38bdf8"/>'
        '<path d="M32 35 C39 33 44 27 43 20 C36 21 32 27 32 35Z" fill="#7dd3fc"/>'
        '<circle cx="32" cy="17" r="3" fill="#e0f2fe" class="glint"/>'
    )
    return defs, body


def emblem_qi_refining() -> tuple[str, str]:
    defs = radial("base", [("0", "#124a47"), ("1", "#05201e")]) + linear("rim", [("0", "#99f6e4"), ("1", "#0d9488")])
    body = (
        f'<polygon points="{polygon(32, 32, 30.5, 6)}" fill="url(#base)" stroke="url(#rim)" stroke-width="3" stroke-linejoin="round"/>'
        f'<polygon points="{polygon(32, 32, 24, 6)}" fill="none" stroke="#2dd4bf" stroke-opacity=".35" stroke-width="1.2"/>'
        '<g class="spin">'
        '<circle cx="32" cy="32" r="13" fill="none" stroke="#2dd4bf" stroke-width="2.2"/>'
        '<path d="M32 19 A6.5 6.5 0 0 1 32 32 A6.5 6.5 0 0 0 32 45" fill="none" stroke="#ccfbf1" stroke-width="2.6"/>'
        '<circle cx="32" cy="25.5" r="2.3" fill="#ccfbf1"/><circle cx="32" cy="38.5" r="2.3" fill="#14b8a6"/>'
        '</g>'
    )
    return defs, body


def emblem_foundation() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#0f4c5c"), ("1", "#031a20")])
        + linear("rim", [("0", "#a5f3fc"), ("1", "#0e7490")])
        + linear("peak", [("0", "#ecfeff"), ("1", "#67e8f9")], x2="0")
    )
    body = (
        '<path d="M32 2.5 L57 11 V30 C57 45.5 46 55.5 32 61.5 C18 55.5 7 45.5 7 30 V11 Z" fill="url(#base)" stroke="url(#rim)" stroke-width="3" stroke-linejoin="round"/>'
        '<path d="M32 8 L51 14.5 V30 C51 42 43 50 32 55 C21 50 13 42 13 30 V14.5 Z" fill="none" stroke="#22d3ee" stroke-opacity=".3" stroke-width="1.2"/>'
        '<rect x="18" y="41" width="28" height="5.5" rx="1.2" fill="#0891b2"/>'
        '<rect x="21.5" y="34" width="21" height="5.5" rx="1.2" fill="#22d3ee"/>'
        '<rect x="25" y="27" width="14" height="5.5" rx="1.2" fill="#67e8f9"/>'
        '<path d="M32 13 L39 25 H25 Z" fill="url(#peak)"/>'
        '<circle cx="32" cy="12" r="2" fill="#ffffff" class="glint"/>'
    )
    return defs, body


def emblem_application_seed() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#0f4a33"), ("1", "#04160e")])
        + linear("rim", [("0", "#a7f3d0"), ("1", "#047857")])
        + radial("seed", [("0", "#d1fae5"), ("0.55", "#34d399"), ("1", "#047857")], cy="35%")
    )
    dots = "".join(
        f'<circle cx="{32 + 24 * math.cos(math.radians(a)):.2f}" cy="{32 + 24 * math.sin(math.radians(a)):.2f}" r="2.1" fill="#d1fae5"/>'
        for a in (0, 90, 180, 270)
    )
    body = (
        '<circle cx="32" cy="32" r="30" fill="url(#base)"/>'
        '<circle cx="32" cy="32" r="28.5" fill="none" stroke="url(#rim)" stroke-width="3"/>'
        f'<g class="spin-f"><circle cx="32" cy="32" r="24" fill="none" stroke="#34d399" stroke-width="1.6" stroke-dasharray="3 4.2"/>{dots}</g>'
        '<path d="M32 27 C27 20 22 19 18.5 20.5 C20.5 26 26 28.5 32 27Z" fill="#6ee7b7"/>'
        '<path d="M32 27 C37 20 42 19 45.5 20.5 C43.5 26 38 28.5 32 27Z" fill="#a7f3d0"/>'
        '<ellipse cx="32" cy="36.5" rx="7.5" ry="9.5" fill="url(#seed)" class="pulse"/>'
        '<path d="M29.5 32 Q31 30.5 33 31" fill="none" stroke="#ecfdf5" stroke-width="1.4" stroke-linecap="round"/>'
    )
    return defs, body


def emblem_golden_core() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#5a2d07"), ("1", "#1f0e02")])
        + linear("rim", [("0", "#fde68a"), ("0.5", "#f59e0b"), ("1", "#b45309")])
        + radial("core", [("0", "#fffbeb"), ("0.35", "#fde68a"), ("0.75", "#f59e0b"), ("1", "#b45309")], cx="42%", cy="38%")
        + GLOW
    )
    body = (
        f'<polygon points="{star(32, 32, 31, 24.5, 8, -90)}" fill="url(#base)" stroke="url(#rim)" stroke-width="2.4" stroke-linejoin="round"/>'
        '<circle cx="32" cy="32" r="21" fill="none" stroke="#fbbf24" stroke-opacity=".35" stroke-width="1.2"/>'
        f'<g class="spin-r" opacity=".9">{rays(8, 15.5, 20, stroke="#fcd34d", stroke_width="2", stroke_linecap="round")}</g>'
        '<circle cx="32" cy="32" r="16" fill="none" stroke="#fde68a" stroke-width="1.4" class="glint"/>'
        '<circle cx="32" cy="32" r="12" fill="url(#core)" filter="url(#glow)" class="pulse"/>'
        '<ellipse cx="28" cy="27.5" rx="4" ry="2.4" fill="#ffffff" opacity=".75"/>'
    )
    return defs, body


def emblem_practical_mastery() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#5c1a06"), ("1", "#1a0702")])
        + linear("rim", [("0", "#fed7aa"), ("0.5", "#f97316"), ("1", "#9a3412")])
        + linear("flame", [("0", "#fef08a"), ("0.45", "#fb923c"), ("1", "#dc2626")], x2="0")
        + GLOW
    )
    body = (
        f'<polygon points="{polygon(32, 32, 31, 8, -67.5)}" fill="url(#base)" stroke="url(#rim)" stroke-width="3" stroke-linejoin="round"/>'
        f'<polygon points="{polygon(32, 32, 25.5, 8, -67.5)}" fill="none" stroke="#fb923c" stroke-opacity=".4" stroke-width="1.2"/>'
        '<path class="flick" filter="url(#glow)" d="M32 10 C38 18 44 23 42.5 33 C41.5 40.5 37 45 32 45 C27 45 22.5 40.5 21.5 33 C20.5 26 26 22.5 27.5 16 C29.5 21 30.5 22 32 23.5 C33.5 19.5 33.5 15 32 10Z" fill="url(#flame)"/>'
        '<path d="M32 26 C35 30 36.5 33 35.5 37 C34.5 40 33 41 32 41 C31 41 29.5 40 28.5 37 C27.5 33 29 30 32 26Z" fill="#fff7ed" opacity=".9"/>'
        '<line x1="15" y1="52" x2="49" y2="38" stroke="#fff7ed" stroke-width="2.6" stroke-linecap="round"/>'
        '<line x1="49" y1="52" x2="15" y2="38" stroke="#fdba74" stroke-width="2.6" stroke-linecap="round"/>'
        '<line x1="18.5" y1="47" x2="20.5" y2="51.5" stroke="#fff7ed" stroke-width="2.2" stroke-linecap="round"/>'
        '<ellipse cx="14" cy="37.6" rx="2.6" ry="1.7" fill="#fdba74" transform="rotate(22 14 37.6)"/>'
    )
    return defs, body


def emblem_systems_thinking() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#26205e"), ("1", "#08071c")])
        + linear("rim", [("0", "#c7d2fe"), ("0.5", "#6366f1"), ("1", "#312e81")])
        + GLOW
    )
    stars = "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="#e0e7ff" class="twinkle" style="animation-delay:{d}s"/>'
        for x, y, r, d in ((14, 22, 0.9, 0), (49, 16, 0.8, 0.6), (51, 45, 1, 1.1), (17, 47, 0.8, 0.3), (40, 54, 0.7, 0.9))
    )
    orbits = "".join(
        f'<g transform="rotate({angle} 32 32)"><ellipse cx="32" cy="32" rx="22" ry="8" fill="none" stroke="#818cf8" stroke-width="1.5"/>'
        f'<circle cx="54" cy="32" r="2.4" fill="#c7d2fe" filter="url(#glow)"/></g>'
        for angle in (0, 60, 120)
    )
    body = (
        '<circle cx="32" cy="32" r="30" fill="url(#base)"/>'
        '<circle cx="32" cy="32" r="28.5" fill="none" stroke="url(#rim)" stroke-width="3"/>'
        f'{stars}<g class="spin">{orbits}</g>'
        '<path d="M32 21 Q33.4 30.6 43 32 Q33.4 33.4 32 43 Q30.6 33.4 21 32 Q30.6 30.6 32 21Z" fill="#eef2ff" filter="url(#glow)" class="pulse"/>'
    )
    return defs, body


def emblem_nascent_soul() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#3b1278"), ("1", "#10042a")])
        + linear("rim", [("0", "#f5d0fe"), ("0.5", "#a855f7"), ("1", "#5b21b6")])
        + linear("petal", [("0", "#fae8ff"), ("1", "#a855f7")], x2="0")
        + radial("soul", [("0", "#ffffff"), ("0.6", "#f5d0fe"), ("1", "#c084fc")], cy="65%")
        + GLOW
    )
    petals = "".join(
        f'<path d="M32 46 C27 38 28 30 32 24 C36 30 37 38 32 46Z" fill="url(#petal)" transform="rotate({a} 32 46)" opacity="{o}"/>'
        for a, o in ((-58, ".7"), (58, ".7"), (-29, ".88"), (29, ".88"), (0, "1"))
    )
    body = (
        '<circle cx="32" cy="32" r="30" fill="url(#base)"/>'
        '<circle cx="32" cy="32" r="28.5" fill="none" stroke="url(#rim)" stroke-width="3"/>'
        '<g class="spin"><circle cx="32" cy="32" r="25" fill="none" stroke="#e9d5ff" stroke-width="2" stroke-dasharray="1 3.2"/></g>'
        '<g class="spin-r"><circle cx="32" cy="32" r="21.5" fill="none" stroke="#c084fc" stroke-opacity=".6" stroke-width="1" stroke-dasharray="10 6"/></g>'
        f'{petals}'
        '<path class="flick" filter="url(#glow)" d="M32 11 C35.5 16.5 38 20 38 23.5 A6 6 0 0 1 26 23.5 C26 20 28.5 16.5 32 11Z" fill="url(#soul)"/>'
        '<circle cx="32" cy="23" r="1.8" fill="#7e22ce"/>'
    )
    return defs, body


def emblem_independent_path() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#5c0a22"), ("1", "#19020a")])
        + linear("rim", [("0", "#fef3c7"), ("0.45", "#fb7185"), ("1", "#9f1239")])
        + linear("wing", [("0", "#fde68a"), ("0.5", "#fb7185"), ("1", "#be123c")])
        + linear("body", [("0", "#fff1f2"), ("1", "#f43f5e")], x2="0")
        + '<linearGradient id="shine" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ffffff" stop-opacity="0"/>'
        + '<stop offset=".5" stop-color="#ffffff" stop-opacity=".65"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>'
        + '<clipPath id="disc"><circle cx="32" cy="32" r="28"/></clipPath>'
        + GLOW
    )
    wing = "M32 35 C26 27 16 22.5 5.5 24.5 C11.5 28 14 31.5 19.5 33 C13.5 34.2 10.5 37.5 8.5 41.5 C16.5 40.5 24 38.6 32 39Z"
    body = (
        '<circle cx="32" cy="32" r="30" fill="url(#base)"/>'
        f'<g class="spin" opacity=".45">{rays(16, 9, 28, alt=22, stroke="#fb7185", stroke_width="1.2", stroke_linecap="round")}</g>'
        '<circle cx="32" cy="32" r="28.5" fill="none" stroke="url(#rim)" stroke-width="3"/>'
        f'<path d="{wing}" fill="url(#wing)"/>'
        f'<path d="{wing}" fill="url(#wing)" transform="translate(64 0) scale(-1 1)"/>'
        '<path d="M32 21 C35.5 25.5 35.5 34 32 44 C28.5 34 28.5 25.5 32 21Z" fill="url(#body)" filter="url(#glow)"/>'
        '<path d="M32 44 C29 48 26 50 23 51 M32 44 C32 48.5 32 51 32 54 M32 44 C35 48 38 50 41 51" fill="none" stroke="#fda4af" stroke-width="1.8" stroke-linecap="round"/>'
        f'<polygon points="{star(32, 12.5, 4.6, 1.9, 5)}" fill="#fef3c7" filter="url(#glow)" class="pulse"/>'
        '<g clip-path="url(#disc)"><g transform="rotate(25 32 32)"><rect x="12" y="-8" width="16" height="80" fill="url(#shine)" class="sweep"/></g></g>'
    )
    return defs, body


def emblem_mentor_heart() -> tuple[str, str]:
    defs = (
        radial("base", [("0", "#3a2a06"), ("0.7", "#140d02"), ("1", "#000000")])
        + linear("aurora", [("0", "#fde68a"), ("0.25", "#f9a8d4"), ("0.5", "#a5b4fc"), ("0.75", "#67e8f9"), ("1", "#fde68a")])
        + radial("sun", [("0", "#fffdf5"), ("0.45", "#fde68a"), ("0.8", "#f59e0b"), ("1", "#92400e")], cx="45%", cy="40%")
        + linear("crown", [("0", "#ffffff"), ("1", "#fcd34d")], x2="0")
        + GLOW
    )
    orbit = "".join(
        sparkle(32 + 25.5 * math.cos(math.radians(a)), 32 + 25.5 * math.sin(math.radians(a)), 2.6, "#fffbeb", d)
        for a, d in ((-45, 0), (45, 0.45), (135, 0.9), (225, 1.35))
    )
    gems = "".join(f'<circle cx="{x}" cy="{y}" r="1.4" fill="{c}"/>' for x, y, c in ((24.5, 21.5, "#f472b6"), (32, 17.5, "#60a5fa"), (39.5, 21.5, "#34d399")))
    body = (
        '<circle cx="32" cy="32" r="31" fill="url(#base)"/>'
        f'<g class="spin">{rays(24, 18.5, 29, alt=24, stroke="url(#aurora)", stroke_width="1.8", stroke_linecap="round")}</g>'
        '<g class="spin-r"><circle cx="32" cy="32" r="29.5" fill="none" stroke="url(#aurora)" stroke-width="2.6" stroke-dasharray="6 2.5"/></g>'
        '<circle cx="32" cy="32" r="17.5" fill="url(#sun)" filter="url(#glow)" class="pulse"/>'
        '<path d="M22.5 30.5 L24.5 20.5 L28.5 26.5 L32 16 L35.5 26.5 L39.5 20.5 L41.5 30.5 Z" fill="url(#crown)" stroke="#78350f" stroke-width=".8" stroke-linejoin="round"/>'
        f'{gems}'
        '<path d="M22.5 35.5 Q27.2 32.6 32 35.5 Q36.8 32.6 41.5 35.5 V43 Q36.8 40.2 32 43 Q27.2 40.2 22.5 43Z" fill="#fffbeb" stroke="#92400e" stroke-width=".9" stroke-linejoin="round"/>'
        '<line x1="32" y1="35.5" x2="32" y2="43" stroke="#92400e" stroke-width=".9"/>'
        f'<g class="spin-f">{orbit}</g>'
    )
    return defs, body


EMBLEMS = {
    "qi_awakening": ("启蒙入门", emblem_qi_awakening),
    "qi_refining": ("基础成形", emblem_qi_refining),
    "foundation": ("核心筑基", emblem_foundation),
    "application_seed": ("迁移初通", emblem_application_seed),
    "golden_core": ("融会小成", emblem_golden_core),
    "practical_mastery": ("基本掌握", emblem_practical_mastery),
    "systems_thinking": ("系统贯通", emblem_systems_thinking),
    "nascent_soul": ("完全掌握", emblem_nascent_soul),
    "independent_path": ("自主精进", emblem_independent_path),
    "mentor_heart": ("可为人师", emblem_mentor_heart),
}


def render(key: str) -> str:
    title, builder = EMBLEMS[key]
    defs, body = builder()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img">'
        f"<title>{title}</title><style>{BASE_STYLE.strip()}</style><defs>{defs}</defs>{body}</svg>\n"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key in EMBLEMS:
        (OUT / f"{key}.svg").write_text(render(key), encoding="utf-8", newline="\n")
    print(f"wrote {len(EMBLEMS)} emblems to {OUT}")


if __name__ == "__main__":
    main()
