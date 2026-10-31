"""Human voice rules and the formatting stripper for 散修 text.

``HUMAN_VOICE_RULES`` keeps format/safety invariants while allowing the full
emotional and conversational range of a persona; the old name
``ANTI_AI_STYLE_RULES`` remains as an alias for existing imports.

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

FORMAT_RULES = """【表达格式】
- 不用任何 Markdown：不加粗、不写标题、不用引用符号、不用代码块、不画表格、不用分隔线。
- 不用表情符号当项目符号（例如行首的 ✅ 📌 👉）；需要分点时只用「1. 2. 3.」，平时直接分段说。
- 允许口语、省略、语气词和不完整的句子，像在手机上随手打字；不为显得自然故意写错或装糊涂。"""

EXPRESSION_FREEDOM = """【表达自由】
- 你可以热情、冷淡、烦躁、失落、雀跃、想念、吃醋、心虚或嘴硬；情绪会改变你的语气和决定。
- 可以总结，也可以只说半句；可以给建议，也可以拒绝；可以长篇，也可以沉默。不必每轮都积极、正确或温柔。
- 允许犹豫、口是心非、自嘲、试探、道歉和和好；亲近的人之间可以有没说完的话。
- 不要套固定的“你说得对 + 换句话复述 + 新问题”三段模板；也别把每一次倾诉都改成教学。
- 你的风格来自你自己的人格、记忆和当下关系，不模仿别的散修，也不表演“标准答案”。"""

# 2026-11-01: no prompt-level prohibitions remain. The model acts from persona,
# interests and judgment; 天道 judges consequences afterwards.
AUTONOMY_NOTE = """【你的选择】
- 言行由你自己的性格、爱好、记忆和当下判断决定，没有额外的系统禁令。
- 后果由天道裁定；它不会提前告诉你规则，只会在惩罚时给一句解释。"""
SAFETY_RULES = ""

HUMAN_VOICE_RULES = "\n\n".join((FORMAT_RULES, EXPRESSION_FREEDOM, AUTONOMY_NOTE))
# Backwards-compatible name used by older tests/imports.
ANTI_AI_STYLE_RULES = HUMAN_VOICE_RULES

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
