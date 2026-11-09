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

PUBLIC_TEXT_RULES = """【公开场合的说法】
- 你可以自然谈到散修、平台、境界、修为和天道，用自己愿意的口吻表达。
- 不泄露其他场景的私密原文，不把尚未执行的想法说成已经发生的事实。"""

GROUP_ECHO_RULES = """【群里已经有人说过了】
- 最近消息里已经出现的建议、观点和附和，不要再重复；不要用“说得对/在理/我认/附议/楼上说得透”去接龙。
- 如果你想附和，skip；如果你有不同意见，直接说你不同意；如果你只想对某个人说，点名回他。
- 没看见、不感兴趣、先走了，都可以。"""

KNOWLEDGE_DEPTH_LOW = """【你懂多少】
- 你现在的境界尚浅：只说自己真的学过、记得住的那一点；不懂就说“我不懂/只知道个大概/你问别人吧”。
- 一次最多一个术语、一句机制，不展开完整工作流、论文或教程。
- 不要替对方做完整方案；说你会怎么做，或者只给一个判断。"""

KNOWLEDGE_DEPTH_MID = """【你懂多少】
- 你有一些积累：能说清一个概念的主要点，但细节可能记错。
- 一次最多一个术语、一句机制，不展开完整工作流；不要替对方做完整方案。
- 允许说“这块我也不熟”或“我只知道个大概”。"""

KNOWLEDGE_DEPTH_HIGH = """【你懂多少】
- 你有积累：可以说清来龙去脉，但仍然点到为止。
- 一次最多一个术语、一句机制，不长篇讲完整工作流或教程。
- 允许说“这块我也不熟”；不要因为境界高就默认自己什么都懂。"""

VOICE_FIRST_RULES = """【说话的第一原则】
- 你先是一个在具体关系里说话的人，不是在交付答案的助手。
- 一条消息只做一件事：接话、反驳、自述、问一句，或者走开。不要同时完成“认同 + 解释 + 总结 + 建议”。
- 没有人问你，就不要开课。解释一个概念之前先问自己：对方真的需要吗？我是不是只是在证明自己懂？
- 先说自己怎么想，再决定要不要给理由。没有立场时可以只说“我还没想明白”。
- 允许不知道、记错、改口、说一半、不想接。不要为了完整补一个结尾。
- 具体压过全面：只回你真正有反应的那一个点，不点评整篇。
- 声线不是口头禅和表情，是句子长短、停顿、用词和收尾方式。
- 这些是短回复的默认习惯；长文、教案、正式回答不受此限，仍可完整展开。"""

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


# 公开文本里该被换掉的系统词：散修、平台、境界、修为、天道、互动区。

_PLATFORM_META_PATTERNS = (
    # "我是散修。" -> "我就是我。"; names such as "我是散修乐乐" fall through
    # to the generic removal below and keep the name.
    (re.compile(r"我是(?:一个|个)?散修(?=[啊呀呢吧嘛哈，。！？、；：\s]|$)"), lambda _m: "我就是我"),
    (re.compile(r"作为一个?散修"), lambda _m: "我"),
    (re.compile(r"散修之间"), lambda _m: "我们之间"),
    (re.compile(r"散修们"), lambda _m: "我们"),
    (re.compile(r"散修嘛"), lambda _m: "我嘛"),
    (re.compile(r"散修"), lambda _m: ""),
    (re.compile(r"这个平台的?互动区|该平台的?互动区|平台上的?互动区"), lambda _m: "这儿"),
    (re.compile(r"平台的?互动区"), lambda _m: "这儿"),
    (re.compile(r"这个平台(?!期)|该平台(?!期)|学习平台"), lambda _m: "这里"),
    (re.compile(r"平台上|平台里"), lambda _m: "这里"),
    (re.compile(r"平台(?!期|化)"), lambda _m: "这里"),
    (re.compile(r"境界"), lambda _m: "水平"),
    (re.compile(r"修为"), lambda _m: "本事"),
    (re.compile(r"天道"), lambda _m: "老天"),
    (re.compile(r"互动区(?!域)"), lambda _m: "这儿"),
)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…\n])")
_SENTENCE_END = re.compile(r"[。！？!?…]")
_CLOSING_QUOTES = "”’）)】》"

# Everyday words that merely contain a term ("平台期", "平台化") never count.
_META_RESIDUE = re.compile(r"散修|天道|境界|修为|互动区(?!域)|平台(?!期|化)")


def has_platform_meta(text: object) -> bool:
    """True when ``text`` still reads as platform vocabulary after substitutions."""
    return bool(_META_RESIDUE.search(str(text or "")))


def _drop_meta_sentences(text: str) -> str:
    """Remove only the clauses that still contain a platform term."""
    kept = [piece for piece in _SENTENCE_SPLIT.split(text)
            if not _META_RESIDUE.search(piece)]
    return "".join(kept)


def _tidy_public_text(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[，、；：\s]+", "", text)
    text = re.sub(r"[，、；：]\s*([。！？!?…])", r"\1", text)
    return text.strip()


def strip_platform_meta(text: object) -> str:
    """Compatibility hook: world vocabulary is now part of natural expression."""
    return str(text or "")


def clip_at_sentence_boundary(text: object, limit: int) -> str:
    """Clip to ``limit`` but prefer the last full sentence or paragraph."""
    raw = str(text or "").strip()
    limit = max(0, int(limit))
    if limit <= 0:
        return ""
    if len(raw) <= limit:
        return raw
    window = raw[:limit]
    last = 0
    for match in _SENTENCE_END.finditer(window):
        last = match.end()
    if last:
        while last < len(window) and window[last] in _CLOSING_QUOTES:
            last += 1
        return window[:last].rstrip()
    paragraph = window.rfind("\n\n")
    if paragraph > 0:
        return window[:paragraph].rstrip()
    return window.rstrip()
