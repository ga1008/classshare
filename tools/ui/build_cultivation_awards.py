"""Build collectible award art from the existing ten cultivation emblem builders.

The 640 px art is deliberately static. The reveal controller owns a short,
preference-aware celebration; downloads and resting awards never animate.
Run: python tools/ui/build_cultivation_awards.py
"""
from __future__ import annotations

import math
from pathlib import Path
from build_cultivation_emblems import EMBLEMS

OUT = Path(__file__).resolve().parents[2] / "static/img/cultivation/awards"
PALETTES = [
    ("#38bdf8", "#d7f3ff", "#082d47"), ("#2dd4bf", "#d6fff7", "#083b38"),
    ("#22d3ee", "#e0fcff", "#07353e"), ("#34d399", "#ddffed", "#103c2a"),
    ("#fbbf24", "#fff0ba", "#4b2b09"), ("#fb923c", "#ffe3b9", "#4e1e0c"),
    ("#818cf8", "#e2e7ff", "#25224c"), ("#c084fc", "#f4e0ff", "#35204d"),
    ("#fb7185", "#ffe0ce", "#4b172c"), ("#f9cf70", "#fff2ce", "#403014"),
]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def point(radius: float, angle: float) -> tuple[float, float]:
    radians = math.radians(angle - 90)
    return 320 + radius * math.cos(radians), 320 + radius * math.sin(radians)


def render(key: str, tier: int) -> str:
    title, builder = EMBLEMS[key]
    defs, body = builder()
    primary, light, dark = PALETTES[tier - 1]
    ticks = []
    for index in range(60):
        start, end = point(220 if index % 5 == 0 else 225, index * 6), point(232, index * 6)
        ticks.append(f'<path d="M{start[0]:.2f} {start[1]:.2f}L{end[0]:.2f} {end[1]:.2f}" stroke="{light}" stroke-width="{2 if index % 5 == 0 else .75}" opacity="{.8 if index % 5 == 0 else .28}"/>')
    ornaments = []
    for index in range(4 + tier * 2):
        angle = index * 360 / (4 + tier * 2)
        x, y = point(251 + min(tier, 6), angle)
        ornaments.append(f'<g transform="rotate({angle} {x:.2f} {y:.2f})"><path d="M{x:.2f} {y-5-tier*.7:.2f}q5 {5+tier*.7:.2f} 0 {10+tier*1.4:.2f}q-5 {-5-tier*.7:.2f} 0 {-10-tier*1.4:.2f}" fill="url(#award-metal)"/></g>')
    spokes = []
    if tier >= 5:
        for index in range(8 if tier < 8 else 12):
            angle = index * 360 / (8 if tier < 8 else 12)
            a, b, c = point(229, angle - 5), point(269 + tier * 1.8, angle), point(229, angle + 5)
            spokes.append(f'<path d="M{a[0]:.2f} {a[1]:.2f}L{b[0]:.2f} {b[1]:.2f}L{c[0]:.2f} {c[1]:.2f}Z" fill="url(#award-metal)" stroke="{light}" stroke-opacity=".45"/>')
    constellation = []
    for index in range(tier):
        angle = 225 + 90 * (index + 1) / (tier + 1)
        x, y = point(203, angle)
        constellation.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{light}"/>')
    aurora = ''
    if tier == 10:
        aurora = '<circle cx="320" cy="320" r="246" fill="none" stroke="url(#award-aurora)" stroke-width="7"/><circle cx="320" cy="320" r="194" fill="none" stroke="url(#award-aurora)" stroke-opacity=".6" stroke-width="2"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640" viewBox="0 0 640 640" role="img">
<title>{title} · 第 {tier} 境道印</title>
<defs>{defs}
<linearGradient id="award-metal" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{light}"/><stop offset=".2" stop-color="{primary}"/><stop offset=".42" stop-color="{light}"/><stop offset=".6" stop-color="{primary}"/><stop offset=".8" stop-color="{dark}"/><stop offset="1" stop-color="{light}"/></linearGradient>
<radialGradient id="award-enamel" cx="36%" cy="24%" r="80%"><stop stop-color="{dark}"/><stop offset=".65" stop-color="#0b1220"/><stop offset="1" stop-color="#02050b"/></radialGradient>
<radialGradient id="award-light"><stop stop-color="{primary}" stop-opacity=".28"/><stop offset=".68" stop-color="{primary}" stop-opacity=".07"/><stop offset="1" stop-color="{primary}" stop-opacity="0"/></radialGradient>
<linearGradient id="award-sheen" x1="0" y1="0" x2=".7" y2="1"><stop stop-color="{light}" stop-opacity=".22"/><stop offset=".5" stop-color="{light}" stop-opacity="0"/></linearGradient>
<linearGradient id="award-aurora"><stop stop-color="#fce8a2"/><stop offset=".25" stop-color="#f9a8d4"/><stop offset=".5" stop-color="#a5b4fc"/><stop offset=".75" stop-color="#67e8f9"/><stop offset="1" stop-color="#fce8a2"/></linearGradient>
<filter id="award-shadow" x="-25%" y="-25%" width="150%" height="150%"><feDropShadow dx="0" dy="7" stdDeviation="8" flood-color="#000" flood-opacity=".5"/></filter>
</defs>
<circle cx="320" cy="320" r="312" fill="url(#award-light)"/>
<g filter="url(#award-shadow)">{''.join(spokes)}
<circle cx="320" cy="320" r="246" fill="url(#award-enamel)" stroke="url(#award-metal)" stroke-width="10"/>
<circle cx="320" cy="320" r="238" fill="none" stroke="{light}" stroke-opacity=".55" stroke-width="1"/>
{''.join(ticks)}
<circle cx="320" cy="320" r="212" fill="none" stroke="url(#award-metal)" stroke-width="2"/>
<circle cx="320" cy="320" r="207" fill="none" stroke="{primary}" stroke-opacity=".22"/>
<circle cx="320" cy="320" r="198" fill="url(#award-sheen)"/>
{''.join(ornaments)}{aurora}
<path d="M174 174A207 207 0 0 1 466 174" fill="none" stroke="{light}" stroke-opacity=".6" stroke-width="2"/>
<g transform="translate(320 292) scale(4.25) translate(-32 -32)">{body}</g>
<text x="320" y="472" text-anchor="middle" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="29" font-weight="600" letter-spacing="6" fill="{light}">{title}</text>
<path d="M265 496h27m56 0h27" stroke="{primary}" stroke-opacity=".7"/>
<text x="320" y="503" text-anchor="middle" font-family="Georgia,serif" font-size="21" letter-spacing="3" fill="{light}">{ROMAN[tier-1]}</text>
{''.join(constellation)}
</g></svg>
'''


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for tier, key in enumerate(EMBLEMS, 1):
        (OUT / f'{key}.svg').write_text(render(key, tier), encoding='utf-8', newline='\n')
    print(f'Wrote {len(EMBLEMS)} cultivation awards to {OUT}')


if __name__ == '__main__':
    main()
