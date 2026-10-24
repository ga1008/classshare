"""把遗留 CSS 里写死的浅色底改成随外观自适应的 light-dark()（2026-10-24）。

背景：遗留样式的文字几乎都走令牌（--text-* → --ls-ink，随亮/暗翻转），但底色是
写死的浅色字面量（rgba(255,255,255,.9)、#f8fafc、hsl(166 76% 97%) ……）。暗色外观下
文字变浅、底色不变，于是浅底浅字。本工具逐条改写 background / background-color 声明：

    background: <原值> /* lq-light-fallback */;
    background: <原值中每个浅色字面量 → light-dark(原色, 同色相深色玻璃)>;

- 亮色外观：light-dark() 取第一个参数，渲染与改写前逐像素一致。
- 暗色外观（:root[data-appearance="dark"] 设置 color-scheme: dark）：取深色值，
  色相保留、饱和度收敛、亮度压到 12–20%，透明度收敛到玻璃区间。
- 不支持 light-dark() 的旧浏览器丢弃第二条声明，沿用第一条（即改写前的行为）。
- 内容槽（试卷/预览/签名图/二维码/色板/打印/画布等）必须保持白底，按选择器排除。
- 幂等：已含 light-dark( 或回退标记的声明不再处理。

    python tools/ui/adaptive_light_fills.py --check          # 只统计，不写文件
    python tools/ui/adaptive_light_fills.py --write          # 改写默认文件清单
    python tools/ui/adaptive_light_fills.py --write --files static/css/x.css
"""
from __future__ import annotations

import argparse
import colorsys
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARK = "lq-light-fallback"
DEFAULT_FILES = [
    "static/css/ui-system.src.css",
    "static/css/academic_final_materials.css",
    "static/css/assessment-student-material.css",
    "static/css/attendance_reports.css",
    "static/css/career_path.css",
    "static/css/classroom_closeout.css",
    "static/css/classroom_group_qr.css",
    "static/css/classroom_members.css",
    "static/css/exam-take.css",
    "static/css/feedback_conversation.css",
    "static/css/material_workflows.css",
    "static/css/materials_library.css",
    "static/css/ordinary_grade_kind_controls.css",
    "static/css/resume_console.css",
    "static/css/signature_point_workflow.css",
    "static/css/signature_scope_fields.css",
]
# 必须保持白底的内容槽：文档/试卷/预览/签名图/二维码/色板/画布/打印。
EXCLUDE_SELECTOR = re.compile(
    r"paper|preview|print|canvas|\bqr|qrcode|swatch|color-picker|ui-color|whiteboard|"
    r"signature-(?:img|image|pad|canvas|cell|ink)|psig-img|sig-image|\ba4\b|pdf|certificate|docx|"
    r"evaluation-sheet|lesson-plan-sheet|thumb|lightbox__stage|katex|mermaid|code-?block|hljs|"
    r"data-appearance=.?dark|\.dark\b|is-dark|scene-dark|login-|life-tip|tip-reveal|"
    r"::?before|::?after|\bimg\b|frame",
    re.I,
)
# Fixed palette constants (--ls-c-*) never flip with the appearance; they are
# colour literals in disguise, so their real channels are read from tokens.css.
PALETTE = {
    m[1]: (float(m[2]), float(m[3]), float(m[4]))
    for m in re.finditer(r"^\s*(--ls-c-[a-z0-9-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%;",
                         (ROOT / "static/css/lq/tokens.css").read_text(encoding="utf-8"), re.M)
}
# Any colour literal; whether it is "light" is decided by its HSL lightness.
LIGHT = re.compile(
    r"hsla?\(\s*var\(\s*--ls-c-[a-z0-9-]+\s*\)\s*(?:/\s*[\d.]+%?\s*)?\)"
    r"|#[0-9a-f]{8}\b|#[0-9a-f]{6}\b|#[0-9a-f]{3}\b"
    r"|rgba?\(\s*[\d.]+[\s,]+[\d.]+[\s,]+[\d.]+\s*(?:[,/]\s*[\d.]+%?\s*)?\)"
    r"|hsla?\(\s*[\d.]+(?:deg)?[\s,]+[\d.]+%[\s,]+[\d.]+%\s*(?:[,/]\s*[\d.]+%?\s*)?\)"
    r"|(?<![-\w])white\b",
    re.I,
)
LIGHTNESS = 0.86           # HSL lightness at or above which a fill counts as "light"
# Semantic text colours written for light mode only. Tokens that do not flip
# with the appearance map to the flipping semantic foreground of the same tone.
TOKEN_INK = {
    "--ls-teal": "success", "--ls-teal-bright": "success", "--ls-success": "success",
    "--ls-info": "info", "--ls-warning": "warning", "--ls-destructive": "danger",
}
TEXT_DECL = re.compile(r"(?P<indent>[ \t]*)(?<![-\w])color\s*:\s*(?P<value>[^;{}]+?)\s*(?:;|$)", re.I)
# Literal dark text in the same rule as a converted fill flips with it.
DARK_TEXT = re.compile(
    r"(?P<indent>[ \t]*)(?<![-\w])color\s*:\s*(?P<value>#(?:0f172a|111827|1e293b|1f2937|334155|374151|475569|4b5563|172033|0b1220|020617)\b)\s*(?:;|$)",
    re.I,
)
MIN_ALPHA = 0.3   # lower alphas are sheens/highlights, not fills
DECL = re.compile(r"(?P<indent>[ \t]*)(?P<prop>background(?:-color)?)\s*:\s*(?P<value>[^;{}]+?)\s*(?:;|$)", re.I)
BLOCK = re.compile(r"(?P<selector>[^{}]+)\{(?P<body>[^{}]*)\}")


def _alpha(raw: str | None) -> float:
    if not raw:
        return 1.0
    return float(raw[:-1]) / 100 if raw.endswith("%") else float(raw)


def _rgb(token: str) -> tuple[float, float, float, float]:
    t = token.lower()
    if t == "white":
        return 255, 255, 255, 1.0
    m = re.match(r"hsla?\(\s*var\(\s*(--ls-c-[a-z0-9-]+)\s*\)\s*(?:/\s*([\d.]+%?))?", t)
    if m:
        if m[1] not in PALETTE:
            return 0, 0, 0, 0.0          # unknown constant: never treated as light
        h, s, lightness = PALETTE[m[1]]
        r, g, b = colorsys.hls_to_rgb(h / 360, lightness / 100, s / 100)
        return r * 255, g * 255, b * 255, _alpha(m[2])
    if t.startswith("#"):
        h = t[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        alpha = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha
    m = re.match(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*([\d.]+%?))?", t)
    if m:
        return float(m[1]), float(m[2]), float(m[3]), _alpha(m[4])
    m = re.match(r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%\s*(?:[,/]\s*([\d.]+%?))?", t)
    if m:
        r, g, b = colorsys.hls_to_rgb(float(m[1]) / 360, float(m[3]) / 100, float(m[2]) / 100)
        return r * 255, g * 255, b * 255, _alpha(m[4])
    raise ValueError(token)


def _hls(token: str) -> tuple[float, float, float, float]:
    r, g, b, a = _rgb(token)
    h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return h, lightness, s, a


def is_light(token: str) -> bool:
    _, lightness, _, alpha = _hls(token)
    return lightness >= LIGHTNESS and alpha >= MIN_ALPHA


def adaptive_ink(value: str) -> str | None:
    """light-dark() for a light-mode-only semantic text colour, else None."""
    v = value.strip()
    m = re.fullmatch(r"hsl\(\s*var\(\s*(--ls-[a-z-]+)\s*\)\s*\)", v)
    if m and m[1] in TOKEN_INK:
        return f"light-dark({v}, hsl(var(--ls-tone-{TOKEN_INK[m[1]]}-fg)))"
    if LIGHT.fullmatch(v):
        h, lightness, s, a = _hls(v)
        # Near-black inks (#0f172a …) are neutral body text: they only flip
        # together with a converted fill (DARK_TEXT), never on their own.
        if s >= 0.35 and 0.2 <= lightness <= 0.45 and a >= 0.5:
            return f"light-dark({v}, hsl({round(h * 360)} {min(round(s * 100), 75)}% 74%))"
    return None


def dark_equivalent(token: str) -> str:
    """Same hue, restrained saturation, glass lightness and alpha."""
    r, g, b, a = _rgb(token)
    h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if s < 0.12 or lightness > 0.985:
        hue, sat = 224, 26          # neutral whites become the platform's dark glass hue
    else:
        hue, sat = round(h * 360), min(round(s * 100), 38)
    light = 13 if lightness >= 0.97 else 17
    alpha = min(a, 0.78) if a >= 0.5 else round(a * 0.9, 2)
    alpha = max(alpha, 0.04)
    return f"hsl({hue} {sat}% {light}% / {alpha:.2f})"


def convert_value(value: str) -> str:
    def one(m: re.Match) -> str:
        if not is_light(m.group(0)):
            return m.group(0)
        return f"light-dark({m.group(0)}, {dark_equivalent(m.group(0))})"
    return LIGHT.sub(one, value)


def process(text: str) -> tuple[str, int, list[str]]:
    changed, samples = 0, []

    def block(match: re.Match) -> str:
        nonlocal changed
        selector, body = match["selector"], match["body"]
        if EXCLUDE_SELECTOR.search(selector) or selector.strip().startswith("@"):
            return match.group(0)

        def decl(d: re.Match) -> str:
            nonlocal changed
            value = d["value"]
            if "light-dark(" in value or MARK in value or not LIGHT.search(value) or "url(" in value:
                return d.group(0)
            if convert_value(value) == value:
                return d.group(0)
            changed += 1
            if len(samples) < 12:
                samples.append(f"{selector.strip()[:70]} :: {value.strip()[:60]}")
            indent, prop = d["indent"], d["prop"]
            return f"{indent}{prop}: {value} /* {MARK} */;\n{indent}{prop}: {convert_value(value)};"

        before = changed
        body = DECL.sub(decl, body)
        if changed > before:
            body = DARK_TEXT.sub(
                lambda t: f"{t['indent']}color: {t['value']} /* {MARK} */;\n"
                          f"{t['indent']}color: light-dark({t['value']}, hsl(var(--ls-ink)));",
                body,
            )

        # A fixed mid/light fill that stays as-is in dark mode (a yellow badge,
        # a brand chip) keeps its dark text: lightening the text would erase it.
        fixed_fill = any(
            "light-dark(" not in d["value"] and MARK not in d["value"]
            and any(_hls(c)[1] >= 0.55 and _hls(c)[3] >= 0.5 for c in LIGHT.findall(d["value"]))
            for d in DECL.finditer(body)
        )

        def ink(t: re.Match) -> str:
            nonlocal changed
            if fixed_fill:
                return t.group(0)
            value = t["value"]
            if "light-dark(" in value or MARK in value:
                return t.group(0)
            adapted = adaptive_ink(value)
            if not adapted:
                return t.group(0)
            changed += 1
            return f"{t['indent']}color: {value} /* {MARK} */;\n{t['indent']}color: {adapted};"

        body = TEXT_DECL.sub(ink, body)
        return selector + "{" + body + "}"

    # Skip @media print blocks entirely: paper stays paper.
    parts = re.split(r"(@media\s+print[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\})", text)
    out = []
    for part in parts:
        out.append(part if part.lstrip().startswith("@media print") else BLOCK.sub(block, part))
    return "".join(out), changed, samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--files", nargs="*")
    parser.add_argument("--templates", action="store_true", help="also rewrite <style> blocks in templates/**/*.html")
    args = parser.parse_args()
    report = {}
    targets = list(args.files or DEFAULT_FILES)
    if args.templates:
        # Login/register pages own their scene-tone pairing (lq-scene-ink); never rewrite them.
        targets += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "templates").rglob("*.html")
                          if not re.search(r"login|register|auth_flow|lq_theme", p.name))
    for rel in targets:
        path = ROOT / rel
        raw = path.read_bytes().decode("utf-8")
        newline = "\r\n" if "\r\n" in raw else "\n"
        text = raw.replace("\r\n", "\n")
        if rel.endswith(".html"):
            total = 0

            def style(m: re.Match) -> str:
                nonlocal total
                css, n, _ = process(m["css"])
                total += n
                return m["open"] + css + m["close"]

            result = re.sub(r"(?P<open><style[^>]*>)(?P<css>.*?)(?P<close></style>)", style, text, flags=re.S)
            changed, samples = total, []
            if not changed:
                continue
        else:
            result, changed, samples = process(text)
        report[rel] = {"converted": changed, "samples": samples[:4]}
        if args.write and changed:
            path.write_bytes(result.replace("\n", newline).encode("utf-8"))
    print(json.dumps({"total": sum(v["converted"] for v in report.values()), "files": report}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
