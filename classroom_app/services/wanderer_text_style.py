"""去 AI 味: the anti-AI-voice rules and the formatting stripper for 散修 text.

docs/wanderer-ai-free-agents-2026-10-25.md §10.5. ``ANTI_AI_STYLE_RULES`` is
injected into every wanderer system prompt (right after the persona block);
``strip_ai_formatting`` runs on every public text a wanderer produces
(comments, replies, DMs, classroom messages, blog post title and body) so a
model that ignores the rules still never publishes Markdown.

Kept: paragraphs, plain lines and "1. 2. 3." numbered lines. Removed: bold /
italics markers, headings, quotes, inline code and fences, horizontal rules,
tables (cells become one plain line), emoji used as bullets, "-" / "*" bullets
(their text stays as a plain line). Three or more blank lines collapse to one
blank line.
"""
from __future__ import annotations

import re

ANTI_AI_STYLE_RULES = """【说话要像真人，不要有 AI 腔】
- 不用任何 Markdown：不加粗、不写标题、不用引用符号、不用代码块、不画表格、不用分隔线。
- 不用表情符号当项目符号（例如行首的 ✅ 📌 👉）；需要分点时只用「1. 2. 3.」，平时直接分段说。
- 不写这些套话：「总之」「综上所述」「希望对你有帮助」「首先……其次……最后」「作为一个……」「值得注意的是」「让我们」「以下是」。
- 不客套、不铺垫、不在结尾做总结或升华，说完就停。
- 允许口语、省略、语气词、偶尔的错别字和不完整的句子，像在手机上随手打字。
- 你的风格只来自你自己的人格与说话习惯，不要模仿别的散修，也不要模仿标准答案。"""

_FENCE_LINE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s+|$)")
_QUOTE = re.compile(r"^\s{0,3}(>\s?)+")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_BULLET = re.compile(r"^\s*[-*+•·]\s+")
_NUMBERED = re.compile(r"^\s*\d{1,2}[.、)]\s*")
_LEADING_EMOJI = re.compile("^\\s*(?:[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿][️‍]?)+\\s+")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
# ASCII-only word boundaries: Chinese text hugs the markers ("很*有意思*，"), arithmetic and
# snake_case ("2*3*4", "a_b_c") do not lose their characters.
_ITALIC_STAR = re.compile(r"(?<![A-Za-z0-9*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![A-Za-z0-9*])")
_ITALIC_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![A-Za-z0-9_])")
_STRIKE = re.compile(r"~~(.+?)~~")
_INLINE_CODE = re.compile(r"`+([^`]*)`+")
_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\s]+\)")
_BLANK_RUN = re.compile(r"\n{3,}")


def _inline(text: str) -> str:
    text = _BOLD.sub(r"\2", text)
    text = _STRIKE.sub(r"\1", text)
    text = _ITALIC_STAR.sub(r"\1", text)
    text = _ITALIC_UNDERSCORE.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    return text.replace("**", "").replace("`", "")


def _table_row_text(line: str) -> str:
    cells = [_inline(cell).strip() for cell in line.strip().strip("|").split("|")]
    return "，".join(cell for cell in cells if cell)


def _clean_line(line: str) -> str:
    if _RULE.match(line) or _TABLE_SEPARATOR.match(line):
        return ""
    if _TABLE_ROW.match(line):
        return _table_row_text(line)
    line = _QUOTE.sub("", _HEADING.sub("", line))
    if not _NUMBERED.match(line):
        line = _LEADING_EMOJI.sub("", _BULLET.sub("", line))
    return _inline(line).strip()


def strip_ai_formatting(text: object) -> str:
    """Plain text with paragraphs and numbered lines only (see module doc)."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(line) for line in raw.split("\n") if not _FENCE_LINE.match(line)]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()
