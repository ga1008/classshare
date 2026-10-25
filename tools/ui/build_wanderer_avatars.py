"""Write the 12 default 散修 silhouette avatars to static/img/wanderers/silhouettes/<key>.svg.

Design §11 (docs/wanderer-ai-free-agents-2026-10-25.md). The silhouettes are
the fallback between a persona illustration and the runtime geometric SVG:
monochrome shapes on a tinted circle, one hue per key, 256×256 viewBox.

The persona illustrations ``static/img/wanderers/<slug>.svg`` are hand-reviewed
artwork and are never written by this script. ``--geometric DIR`` additionally
exports the runtime geometric avatars of every seed into DIR for design review
(DIR must not be the illustration folder).

Run:  python tools/ui/build_wanderer_avatars.py [--geometric .codex-temp/avatars/geometric]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AVATAR_DIR = ROOT / "static" / "img" / "wanderers"
SILHOUETTE_DIR = AVATAR_DIR / "silhouettes"

# key: (tint background, silhouette colour, aria label)
PALETTE = {
    "student_m": ("#DCE6F4", "#3F5B85", "男学生剪影"),
    "student_f": ("#F6DFE6", "#8C4560", "女学生剪影"),
    "scholar": ("#D9EFEA", "#2F6F66", "戴眼镜捧书的学霸剪影"),
    "athlete": ("#FBE3CF", "#B4561F", "篮球剪影"),
    "musician": ("#E7DEF5", "#5E4391", "吉他剪影"),
    "foodie": ("#FAEBC8", "#A86A12", "一碗面条剪影"),
    "gamer": ("#DEE0F7", "#3D4296", "游戏手柄剪影"),
    "artist": ("#F7DDF0", "#93367B", "调色盘剪影"),
    "traveler": ("#DDEFD9", "#3E7A3A", "旅行背包剪影"),
    "dog": ("#F1E4D6", "#7A5232", "小狗剪影"),
    "cat": ("#E6E2EE", "#55506E", "小猫剪影"),
    "sword": ("#D8EEF0", "#2B6E7C", "仙剑与祥云剪影"),
}

_BUST = ('<path d="M40 256C40 206 80 186 128 186C176 186 216 206 216 256Z" fill="{ink}"/>'
         '<rect x="112" y="146" width="32" height="48" rx="12" fill="{ink}"/>')
_HEAD = '<circle cx="128" cy="112" r="46" fill="{ink}"/>'
_EARS = '<circle cx="83" cy="118" r="9" fill="{ink}"/><circle cx="173" cy="118" r="9" fill="{ink}"/>'
_HAIR_SHORT = ('<path d="M80 114C76 70 102 54 130 54C162 54 182 74 177 110C168 94 150 86 130 89'
               'C110 92 94 99 80 114Z" fill="{ink}"/>')
_COLLAR = '<path d="M110 188L128 212L146 188Z" fill="{tint}"/>'

BODIES = {
    "student_m": _BUST + _HEAD + _EARS + _HAIR_SHORT + _COLLAR,
    "student_f": (
        '<circle cx="128" cy="54" r="24" fill="{ink}"/>'
        + _BUST + _HEAD
        + '<path d="M76 154C66 92 90 62 128 62C166 62 190 92 180 154C170 162 158 160 152 150L150 112'
          'C136 100 120 100 106 112L104 150C98 160 86 162 76 154Z" fill="{ink}"/>'
        + '<path d="M106 188C110 208 146 208 150 188Z" fill="{tint}"/>'
        + '<circle cx="150" cy="44" r="8" fill="{tint}"/>'
    ),
    "scholar": (
        _BUST + _HEAD + _EARS + _HAIR_SHORT
        + '<g fill="none" stroke="{tint}" stroke-width="5"><circle cx="108" cy="116" r="14"/>'
          '<circle cx="148" cy="116" r="14"/><path d="M122 114H134"/></g>'
        + '<rect x="78" y="200" width="100" height="64" rx="8" fill="{tint}"/>'
        + '<rect x="86" y="208" width="40" height="56" rx="4" fill="{ink}"/>'
        + '<rect x="130" y="208" width="40" height="56" rx="4" fill="{ink}"/>'
    ),
    "athlete": (
        '<circle cx="140" cy="130" r="74" fill="{ink}"/>'
        '<g fill="none" stroke="{tint}" stroke-width="7" stroke-linecap="round">'
        '<path d="M140 56V204M66 130H214M88 78C114 104 114 156 88 182M192 78C166 104 166 156 192 182"/></g>'
        '<g stroke="{ink}" stroke-width="9" stroke-linecap="round"><path d="M28 104H52M20 130H48M28 156H52"/></g>'
    ),
    "musician": (
        '<g transform="rotate(35 128 128) translate(128 128) scale(.86) translate(-128 -128)">'
        '<rect x="119" y="26" width="18" height="104" rx="5" fill="{ink}"/>'
        '<rect x="112" y="8" width="32" height="32" rx="8" fill="{ink}"/>'
        '<circle cx="128" cy="126" r="38" fill="{ink}"/><circle cx="128" cy="184" r="50" fill="{ink}"/>'
        '<circle cx="128" cy="152" r="15" fill="{tint}"/>'
        '<rect x="110" y="194" width="36" height="9" rx="4" fill="{tint}"/>'
        '<g fill="{tint}"><circle cx="118" cy="18" r="3"/><circle cx="138" cy="18" r="3"/>'
        '<circle cx="118" cy="30" r="3"/><circle cx="138" cy="30" r="3"/></g></g>'
    ),
    "foodie": (
        '<g fill="none" stroke="{ink}" stroke-width="8" stroke-linecap="round">'
        '<path d="M98 116C88 102 108 92 98 74M128 112C118 98 138 88 128 70M158 116C148 102 168 92 158 74"/>'
        '<path d="M150 134L208 60M164 136L222 72" stroke-width="7"/></g>'
        '<path d="M50 138H206C206 186 174 214 128 214C82 214 50 186 50 138Z" fill="{ink}"/>'
        '<rect x="102" y="208" width="52" height="14" rx="5" fill="{ink}"/>'
        '<path d="M62 162C94 176 162 176 194 162" fill="none" stroke="{tint}" stroke-width="6" stroke-linecap="round"/>'
    ),
    "gamer": (
        '<path d="M72 98H184C212 98 226 126 230 158C234 190 220 206 202 206C188 206 180 194 170 180H86'
        'C76 194 68 206 54 206C36 206 22 190 26 158C30 126 44 98 72 98Z" fill="{ink}"/>'
        '<g fill="{tint}"><rect x="74" y="124" width="16" height="44" rx="4"/>'
        '<rect x="60" y="138" width="44" height="16" rx="4"/>'
        '<circle cx="176" cy="128" r="9"/><circle cx="198" cy="148" r="9"/><circle cx="154" cy="148" r="9"/>'
        '<circle cx="176" cy="168" r="9"/><rect x="114" y="122" width="28" height="9" rx="4"/></g>'
    ),
    "artist": (
        '<path d="M128 50C186 50 224 88 224 132C224 162 202 172 182 164C164 158 150 172 158 190'
        'C166 208 150 220 124 218C74 214 34 182 34 134C34 86 74 50 128 50Z" fill="{ink}"/>'
        '<g fill="{tint}"><circle cx="86" cy="110" r="14"/><circle cx="126" cy="86" r="14"/>'
        '<circle cx="170" cy="100" r="14"/><circle cx="78" cy="154" r="14"/><circle cx="114" cy="184" r="12"/></g>'
    ),
    "traveler": (
        '<path d="M104 82C104 50 152 50 152 82" fill="none" stroke="{ink}" stroke-width="12" stroke-linecap="round"/>'
        '<rect x="70" y="74" width="116" height="156" rx="42" fill="{ink}"/>'
        '<path d="M80 122H176" stroke="{tint}" stroke-width="6" stroke-linecap="round"/>'
        '<rect x="94" y="150" width="68" height="52" rx="16" fill="none" stroke="{tint}" stroke-width="6"/>'
        '<path d="M112 150V138M144 150V138" stroke="{tint}" stroke-width="6" stroke-linecap="round"/>'
        '<rect x="50" y="128" width="16" height="70" rx="8" fill="{ink}"/>'
        '<rect x="190" y="128" width="16" height="70" rx="8" fill="{ink}"/>'
    ),
    "dog": (
        '<ellipse cx="68" cy="122" rx="24" ry="48" transform="rotate(22 68 122)" fill="{ink}"/>'
        '<ellipse cx="188" cy="122" rx="24" ry="48" transform="rotate(-22 188 122)" fill="{ink}"/>'
        '<ellipse cx="128" cy="144" rx="66" ry="60" fill="{ink}"/>'
        '<g fill="{tint}"><circle cx="104" cy="132" r="8"/><circle cx="152" cy="132" r="8"/>'
        '<ellipse cx="128" cy="172" rx="32" ry="23"/></g>'
        '<ellipse cx="128" cy="161" rx="12" ry="8" fill="{ink}"/>'
        '<path d="M128 168V176M128 176C122 183 113 181 111 175M128 176C134 183 143 181 145 175" fill="none" '
        'stroke="{ink}" stroke-width="4" stroke-linecap="round"/>'
    ),
    "cat": (
        '<path d="M66 122L76 54L120 92ZM190 122L180 54L136 92Z" fill="{ink}" stroke="{ink}" stroke-width="10" '
        'stroke-linejoin="round"/>'
        '<ellipse cx="128" cy="148" rx="70" ry="58" fill="{ink}"/>'
        '<g fill="{tint}"><path d="M80 98L85 70L104 88Z"/><path d="M176 98L171 70L152 88Z"/>'
        '<ellipse cx="102" cy="140" rx="9" ry="12"/><ellipse cx="154" cy="140" rx="9" ry="12"/>'
        '<path d="M120 160H136L128 170Z"/></g>'
        '<g stroke="{tint}" stroke-width="4" stroke-linecap="round" fill="none">'
        '<path d="M70 160L98 166M72 178L98 174M186 160L158 166M184 178L158 174"/>'
        '<path d="M128 170C124 178 116 178 114 172M128 170C132 178 140 178 142 172"/></g>'
    ),
    "sword": (
        '<g transform="rotate(45 128 128) translate(128 128) scale(.9) translate(-128 -128)">'
        '<path d="M128 14L144 40V158H112V40Z" fill="{ink}"/>'
        '<path d="M128 46V150" stroke="{tint}" stroke-width="4" stroke-linecap="round"/>'
        '<rect x="88" y="156" width="80" height="16" rx="8" fill="{ink}"/>'
        '<rect x="119" y="170" width="18" height="48" rx="6" fill="{ink}"/>'
        '<circle cx="128" cy="226" r="11" fill="{ink}"/></g>'
        '<g fill="{ink}"><circle cx="168" cy="196" r="18"/><circle cx="194" cy="186" r="24"/>'
        '<circle cx="218" cy="198" r="15"/><rect x="152" y="196" width="80" height="18" rx="9"/></g>'
    ),
}


def render_silhouette(key: str) -> str:
    tint, ink, label = PALETTE[key]
    body = BODIES[key].format(tint=tint, ink=ink)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" '
        f'role="img" aria-label="{label}"><defs><clipPath id="c"><circle cx="128" cy="128" r="128"/></clipPath>'
        f'</defs><circle cx="128" cy="128" r="128" fill="{tint}"/><g clip-path="url(#c)">{body}</g></svg>\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the 散修 silhouette avatars.")
    parser.add_argument("--geometric", type=Path, help="also export geometric avatars of every seed into this folder")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from classroom_app.services.wanderer_avatar_service import (
        SILHOUETTE_KEYS,
        render_wanderer_avatar_svg,
        validate_avatar_svg,
    )

    if set(SILHOUETTE_KEYS) != set(PALETTE):
        raise SystemExit("silhouette keys out of sync with wanderer_avatar_service.SILHOUETTES")
    SILHOUETTE_DIR.mkdir(parents=True, exist_ok=True)
    for key in SILHOUETTE_KEYS:
        svg = render_silhouette(key)
        problems = validate_avatar_svg(svg)
        if problems:
            raise SystemExit(f"silhouette {key} failed validation: {problems}")
        (SILHOUETTE_DIR / f"{key}.svg").write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {len(SILHOUETTE_KEYS)} silhouettes to {SILHOUETTE_DIR}")
    if args.geometric:
        target = args.geometric.resolve()
        if target == AVATAR_DIR.resolve():
            raise SystemExit("refusing to overwrite the persona illustrations; pick another folder")
        from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS

        target.mkdir(parents=True, exist_ok=True)
        for seed in WANDERER_SEEDS:
            (target / f"{seed['slug']}.svg").write_text(
                render_wanderer_avatar_svg(seed["slug"]), encoding="utf-8", newline="\n")
        print(f"wrote {len(WANDERER_SEEDS)} geometric avatars to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
