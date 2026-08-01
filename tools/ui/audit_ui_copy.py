from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_GLOBS = (
    (ROOT / "templates", ("*.html",)),
    (ROOT / "static" / "js", ("*.js",)),
    (ROOT / "frontend" / "src", ("*.ts", "*.tsx", "*.js", "*.jsx")),
    (ROOT / "classroom_app" / "routers", ("*.py",)),
    (ROOT / "classroom_app" / "services", ("manage_nav_service.py",)),
)

VISIBLE_CLASS_WORDS = {
    "caption",
    "copy",
    "description",
    "empty",
    "eyebrow",
    "helper",
    "hint",
    "intro",
    "lead",
    "note",
    "subtitle",
    "summary",
    "title",
}
FRAMING_PHRASES = (
    "在这里",
    "本页面",
    "本功能",
    "您可以",
    "你可以",
    "帮助您",
    "帮助你",
    "系统将",
    "系统会",
    "通过本",
    "一站式",
    "全方位",
    "快速了解",
    "轻松完成",
)

TAG_RE = re.compile(
    r"<(?P<tag>h[1-6]|p|small|div|span)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
CLASS_RE = re.compile(r"\bclass\s*=\s*([\"'])(?P<value>.*?)\1", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r"\b(?P<name>title|placeholder|aria-description)\s*=\s*([\"'])(?P<value>.*?)\2",
    re.IGNORECASE | re.DOTALL,
)
KEYED_STRING_RE = re.compile(
    r"\b(?P<key>page_title|title|subtitle|description|caption|eyebrow|helper_text|helperText|hint|empty_text|emptyText)"
    r"\s*(?:=|:)\s*(?P<quote>[\"'`])(?P<value>(?:\\.|(?!\2).){4,1000}?)(?P=quote)",
    re.DOTALL,
)
TAG_STRIP_RE = re.compile(r"<[^>]+>")
JINJA_RE = re.compile(r"{[{%#].*?[}%#]}", re.DOTALL)
SPACE_RE = re.compile(r"\s+")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
PUNCTUATION_RE = re.compile(r"[，。；：！？、,.!?;:]")
JS_TEMPLATE_EXPR_RE = re.compile(r"\$\{.*?}", re.DOTALL)
CODE_MARKERS = ("${", "escapeHtml(", "RZ.esc(", "sanitizeHtml(", " || ", " + ")


@dataclass(frozen=True)
class Candidate:
    priority: str
    score: int
    path: str
    line: int
    kind: str
    text: str
    reasons: tuple[str, ...]


def _normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = JINJA_RE.sub("", value)
    value = TAG_STRIP_RE.sub(" ", value)
    value = value.replace("\\n", " ").replace("\\t", " ")
    return SPACE_RE.sub(" ", value).strip(" \t\r\n-·|/")


def _visible_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def _score(text: str, kind: str) -> tuple[int, tuple[str, ...]]:
    length = _visible_length(text)
    score = 0
    reasons: list[str] = []
    if length >= 42:
        score += 4
        reasons.append("long")
    elif length >= 26:
        score += 3
        reasons.append("long")
    elif length >= 16:
        score += 1
        reasons.append("dense")
    punctuation_count = len(PUNCTUATION_RE.findall(text))
    if punctuation_count >= 3:
        score += 2
        reasons.append("multi-clause")
    elif punctuation_count >= 2:
        score += 1
        reasons.append("multi-clause")
    if any(phrase in text for phrase in FRAMING_PHRASES):
        score += 2
        reasons.append("framing")
    if re.fullmatch(r"h[1-6]", kind) and length >= 12:
        score += 2
        reasons.append("long-heading")
    if any(word in kind for word in ("description", "subtitle", "caption", "eyebrow", "helper", "hint", "intro")):
        score += 1
        reasons.append("secondary-copy")
    return score, tuple(dict.fromkeys(reasons))


def _candidate(path: Path, line: int, kind: str, raw_text: str) -> Candidate | None:
    text = _normalize_text(raw_text)
    if not text or _visible_length(text) < 7 or not CHINESE_RE.search(text):
        return None
    if any(marker in text for marker in CODE_MARKERS):
        return None
    score, reasons = _score(text, kind.lower())
    if score < 2:
        return None
    priority = "P1" if score >= 7 else "P2" if score >= 4 else "P3"
    return Candidate(
        priority=priority,
        score=score,
        path=path.relative_to(ROOT).as_posix(),
        line=max(1, line),
        kind=kind,
        text=text[:240],
        reasons=reasons,
    )


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _scan_markup(path: Path, source: str) -> list[Candidate]:
    results: list[Candidate] = []
    if path.suffix.lower() != ".html":
        source = JS_TEMPLATE_EXPR_RE.sub(
            lambda match: "…" + ("\n" * match.group(0).count("\n")),
            source,
        )
    for match in TAG_RE.finditer(source):
        tag = match.group("tag").lower()
        attrs = match.group("attrs")
        class_match = CLASS_RE.search(attrs)
        classes = (class_match.group("value") if class_match else "").lower()
        class_words = set(re.split(r"[^a-z]+", classes))
        if tag.startswith("h") or class_words.intersection(VISIBLE_CLASS_WORDS):
            kind = tag if tag.startswith("h") else next(
                (word for word in VISIBLE_CLASS_WORDS if word in class_words),
                tag,
            )
            item = _candidate(path, _line_number(source, match.start()), kind, match.group("body"))
            if item:
                results.append(item)
    for match in ATTR_RE.finditer(source):
        item = _candidate(
            path,
            _line_number(source, match.start()),
            match.group("name"),
            match.group("value"),
        )
        if item:
            results.append(item)
    return results


def _scan_keyed_strings(path: Path, source: str) -> list[Candidate]:
    results: list[Candidate] = []
    for match in KEYED_STRING_RE.finditer(source):
        item = _candidate(
            path,
            _line_number(source, match.start()),
            match.group("key"),
            match.group("value"),
        )
        if item:
            results.append(item)
    return results


def _iter_sources() -> list[Path]:
    files: set[Path] = set()
    for directory, patterns in SOURCE_GLOBS:
        if not directory.exists():
            continue
        for pattern in patterns:
            files.update(directory.rglob(pattern))
    return sorted(
        path for path in files
        if path.is_file()
        and ".min." not in path.name
        and "static/dist" not in path.as_posix()
        and "node_modules" not in path.parts
    )


def scan() -> tuple[list[Candidate], dict[str, object]]:
    sources = _iter_sources()
    candidates: list[Candidate] = []
    for path in sources:
        source = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in {".html", ".js", ".jsx", ".tsx"}:
            candidates.extend(_scan_markup(path, source))
        candidates.extend(_scan_keyed_strings(path, source))

    unique: dict[tuple[str, int, str], Candidate] = {}
    for item in candidates:
        key = (item.path, item.line, item.text)
        current = unique.get(key)
        if current is None or item.score > current.score:
            unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: (-item.score, item.path, item.line))
    summary = {
        "files_scanned": len(sources),
        "candidate_count": len(ordered),
        "by_priority": dict(Counter(item.priority for item in ordered)),
        "by_root": dict(Counter(item.path.split("/", 1)[0] for item in ordered)),
    }
    return ordered, summary


def render_markdown(candidates: list[Candidate], summary: dict[str, object]) -> str:
    lines = [
        "# UI 文案精简候选清单",
        "",
        "> 此文件由 `python tools/ui/audit_ui_copy.py --output docs/ui-copy-audit-candidates.md` 生成。候选项必须经过人工判断；错误、风险、权限与不可逆操作提示不得仅因文字较长而删除。",
        "",
        f"扫描文件：{summary['files_scanned']}；候选项：{summary['candidate_count']}。",
        "",
        "| 优先级 | 来源 | 类型 | 当前文案 | 命中信号 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in candidates:
        text = item.text.replace("|", "\\|")
        source = f"`{item.path}:{item.line}`"
        lines.append(
            f"| {item.priority} | {source} | `{item.kind}` | {text} | {', '.join(item.reasons)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Inventory UI copy that may be too dense for its surface.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--priority", choices=("all", "P1", "P2", "P3"), default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    candidates, summary = scan()
    if args.priority != "all":
        candidates = [item for item in candidates if item.priority == args.priority]
    if args.format == "json":
        output = json.dumps({"summary": summary, "items": [asdict(item) for item in candidates]}, ensure_ascii=False, indent=2) + "\n"
    else:
        output = render_markdown(candidates, summary)
    if args.output:
        output_path = args.output if args.output.is_absolute() else ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
