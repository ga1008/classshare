"""Minimal Markdown → block parser shared by the 教案 docx export and HTML
preview renderers, so both reproduce the same layout from the AI-generated
``process`` markdown (which uses headings, bullet/numbered lists, **bold**, and
GFM tables — exactly like the hand-written 第16章 samples).

Supported blocks: heading / paragraph / ul / ol / table / code. Inline:
``**bold**`` and `` `code` ``. Anything fancier degrades to plain text.
"""

from __future__ import annotations

import html
import re
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*```\s*([A-Za-z0-9_-]*)\s*$")


def _is_table_sep(line: str) -> bool:
    stripped = line.strip().strip("|").strip()
    if not stripped or "-" not in stripped:
        return False
    cells = [c.strip() for c in stripped.split("|")]
    return all(set(c) <= set("-: ") and "-" in c for c in cells if c != "")


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_blocks(markdown: Any) -> list[dict[str, Any]]:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    blocks: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    para: list[str] = []

    def flush_para() -> None:
        if para:
            joined = " ".join(p.strip() for p in para if p.strip())
            if joined:
                blocks.append({"type": "para", "text": joined})
            para.clear()

    while i < n:
        line = lines[i]
        # fenced code / mermaid
        fence = _FENCE_RE.match(line)
        if fence:
            flush_para()
            lang = fence.group(1)
            i += 1
            buf: list[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # consume closing fence
            blocks.append({"type": "code", "lang": lang, "text": "\n".join(buf)})
            continue
        # blank line
        if not line.strip():
            flush_para()
            i += 1
            continue
        # heading
        heading = _HEADING_RE.match(line)
        if heading:
            flush_para()
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2).strip()})
            i += 1
            continue
        # table: a row line followed by a separator line
        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            flush_para()
            header = _split_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue
        # unordered list
        if _UL_RE.match(line):
            flush_para()
            items: list[str] = []
            while i < n and _UL_RE.match(lines[i]):
                items.append(_UL_RE.match(lines[i]).group(1).strip())
                i += 1
            blocks.append({"type": "ul", "items": items})
            continue
        # ordered list
        if _OL_RE.match(line):
            flush_para()
            items = []
            while i < n and _OL_RE.match(lines[i]):
                items.append(_OL_RE.match(lines[i]).group(1).strip())
                i += 1
            blocks.append({"type": "ol", "items": items})
            continue
        # plain paragraph line
        para.append(line)
        i += 1

    flush_para()
    return blocks


# ---------------------------------------------------------------------------
# Inline parsing
# ---------------------------------------------------------------------------
def inline_runs(text: Any) -> list[dict[str, Any]]:
    """Split inline text into runs of {text, bold, code}."""
    raw = str(text or "")
    runs: list[dict[str, Any]] = []
    pos = 0
    pattern = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
    for match in pattern.finditer(raw):
        if match.start() > pos:
            runs.append({"text": raw[pos : match.start()], "bold": False, "code": False})
        if match.group(1) is not None:
            runs.append({"text": match.group(1), "bold": True, "code": False})
        else:
            runs.append({"text": match.group(2), "bold": False, "code": True})
        pos = match.end()
    if pos < len(raw):
        runs.append({"text": raw[pos:], "bold": False, "code": False})
    return runs or [{"text": raw, "bold": False, "code": False}]


def _inline_html(text: Any) -> str:
    parts: list[str] = []
    for run in inline_runs(text):
        escaped = html.escape(run["text"])
        if run["code"]:
            parts.append(f"<code>{escaped}</code>")
        elif run["bold"]:
            parts.append(f"<strong>{escaped}</strong>")
        else:
            parts.append(escaped)
    return "".join(parts)


def blocks_to_html(blocks: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "heading":
            level = min(6, max(1, int(block.get("level", 3)) + 2))  # nests under section headers
            out.append(f"<h{level}>{_inline_html(block.get('text'))}</h{level}>")
        elif btype == "para":
            out.append(f"<p>{_inline_html(block.get('text'))}</p>")
        elif btype == "ul":
            items = "".join(f"<li>{_inline_html(it)}</li>" for it in block.get("items", []))
            out.append(f"<ul>{items}</ul>")
        elif btype == "ol":
            items = "".join(f"<li>{_inline_html(it)}</li>" for it in block.get("items", []))
            out.append(f"<ol>{items}</ol>")
        elif btype == "table":
            head = "".join(f"<th>{_inline_html(c)}</th>" for c in block.get("header", []))
            body_rows = []
            for row in block.get("rows", []):
                cells = "".join(f"<td>{_inline_html(c)}</td>" for c in row)
                body_rows.append(f"<tr>{cells}</tr>")
            out.append(
                f"<table class='lp-md-table'><thead><tr>{head}</tr></thead>"
                f"<tbody>{''.join(body_rows)}</tbody></table>"
            )
        elif btype == "code":
            out.append(f"<pre class='lp-md-code'>{html.escape(block.get('text', ''))}</pre>")
    return "\n".join(out)


def markdown_to_html(markdown: Any) -> str:
    return blocks_to_html(parse_blocks(markdown))
