"""Deterministic post-generation style guard for 散修 public text.

The audit (docs/wanderer-voice-audit-2026-11-01.md §10.1) wants a hard stop for
the most obvious assistant patterns even when the prompt is ignored. This
module only changes expression: it removes summary/teaching framing, numbered
list markers, extra hedges, and an optional advice ending, plus the
natural-voice fixes for livestream-style fixed openings and excessive
exclamation/emoji density. It never invents facts, changes a stance, or makes a
model call. A pure emoji reply is a legal action and is never flagged; when a
repair empties the text, the caller's existing skip path handles it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .wanderer_text_style import has_platform_meta, strip_platform_meta

SUMMARY_CUES = ("我来总结一下", "总结一下", "我来总结", "总的来说", "总而言之", "综上")
ADVICE_CUES = ("建议你", "建议您", "你可以试试", "你可以尝试", "不妨",
               "希望对你有帮助", "希望能帮到你", "希望这能帮到你")
# Advice-shaped sentence openings. Bare words such as 记得/最好/祝/加油 are
# ordinary speech ("我记得他当时也在"), so they only count in a fixed phrase.
_ADVICE_PATTERNS = (
    re.compile(r"^(?:所以|那|还是|我)?建议(?:你|您)"),
    re.compile(r"^(?:你|您)?可以(?:试试|试一下|考虑一下|先)"),
    re.compile(r"^不妨"),
    re.compile(r"^希望(?:这|它)?(?:对你|对你有帮助|能帮到你)"),
    re.compile(r"^祝(?:你|您|大家|一切|考试|周末|早日|顺利|好运|新年|生日)"),
    re.compile(r"^加油(?:哦|呀|吧|！|!|。|~|～)*$"),
    re.compile(r"^记得(?:早点睡|早睡|休息|吃饭|喝水|吃药|保暖)"),
    re.compile(r"^别忘了(?:休息|吃饭|喝水|早点睡|带伞|带)"),
)
HEDGE_WORDS = ("不过", "其实", "确实")
_TEACHING_PREFIX = re.compile(r"^(?:补充一点|补充一下)[：:]\s*")
_SEQUENCE = re.compile(r"(?m)^\s*(?:首先|其次|最后|一是|二是|三是)[，,、：:]?\s*")
_LIST_LINE = re.compile(r"(?m)^\s*\d{1,2}[.、)]\s*")

# --- Natural-voice guards: fixed livestream-style openings, exclamation and
# emoji density. These are deterministic, high-confidence expression fixes;
# they never delete substantive words or invent facts.
# A fixed opening only counts at the very start of a message, followed by
# punctuation/whitespace, so the same word used mid-sentence is left alone.
_OPENING_WORDS = ("家人们", "集美们", "老铁们", "宝子们", "铁子们", "兄弟们", "老铁", "宝子")
_OPENING_SEPARATORS = r"\s！!，,。．.…？?：:；;~～"
_OPENING_RE = re.compile(
    r"^\s*(?:" + "|".join(_OPENING_WORDS) + r")(?=$|[" + _OPENING_SEPARATORS
    + r"])[" + _OPENING_SEPARATORS + r"]*"
)
# Single emoji glyph plus an optional variation selector / joining char. The
# arrow block is intentionally excluded so ordinary arrows are not treated as
# emoji (mirrors the range used in wanderer_text_style._LEADING_EMOJI).
_EMOJI_CHAR = re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿][️‍]?")
_EXCLAMATION_RUN = re.compile(r"[！!]{2,}")
EXCLAMATION_MAX = 3
EMOJI_MAX = 3
NATURAL_RULES = frozenset({"opening", "exclamation", "emoji_density"})
_CODE_MARKER = re.compile(r"`|^[ \t]*~~~|^(?: {4}|\t)[ \t]*\S", re.MULTILINE)


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str
    span: tuple[int, int]
    suggestion: str


@dataclass(frozen=True)
class GuardResult:
    text: str
    violations: tuple[Violation, ...]
    repaired: bool
    rewritten: bool = False


def _span(text: str, needle: str) -> tuple[int, int]:
    start = text.find(needle)
    return (start, start + len(needle)) if start >= 0 else (0, 0)


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[。！？!?…])", text) if part.strip()]


def _is_advice_sentence(sentence: object) -> bool:
    body = str(sentence or "").strip().lstrip("“”\"'（(【")
    if not body:
        return False
    if any(cue in body[:12] for cue in ADVICE_CUES):
        return True
    return any(pattern.match(body) for pattern in _ADVICE_PATTERNS)


def _has_fixed_opening(raw: str) -> bool:
    return bool(_OPENING_RE.match(raw))


def _strip_fixed_opening(value: str) -> str:
    return _OPENING_RE.sub("", value, count=1)


def _emoji_matches(raw: str) -> list[re.Match]:
    return list(_EMOJI_CHAR.finditer(raw))


def _has_text_without_emoji(raw: str) -> bool:
    """True when the message carries real words besides emoji/whitespace."""
    return bool(_EMOJI_CHAR.sub("", raw).strip())


def _normalize_exclamation(value: str) -> str:
    """Collapse repeated exclamation marks, then cap the total per message."""
    value = _EXCLAMATION_RUN.sub("！", value)
    marks = [m.start() for m in re.finditer(r"[！!]", value)]
    if len(marks) <= EXCLAMATION_MAX:
        return value
    keep = set(marks[:EXCLAMATION_MAX])
    return "".join(ch for index, ch in enumerate(value)
                   if index not in marks or index in keep)


def _normalize_emoji(value: str) -> str:
    """Keep only the first emoji when a text message carries too many."""
    matches = _emoji_matches(value)
    if len(matches) <= EMOJI_MAX:
        return value
    drop_spans = set()
    for match in matches[1:]:
        drop_spans.update(range(match.start(), match.end()))
    return "".join(ch for index, ch in enumerate(value) if index not in drop_spans)


def lint(text: object, *, channel: str = "", persona: dict | None = None) -> list[Violation]:
    """High-confidence assistant-pattern violations; never edits the text."""
    raw = str(text or "")
    if not raw.strip() or _CODE_MARKER.search(raw):
        # All callers, including the legacy social guard, must preserve code.
        # Punctuation, indentation and quoted strings are meaningful there.
        return []
    found: list[Violation] = []
    seen: set[str] = set()

    def add(rule: str, severity: str, needle: str, suggestion: str) -> None:
        if rule in seen:
            return
        seen.add(rule)
        found.append(Violation(rule, severity, _span(raw, needle), suggestion))

    for cue in SUMMARY_CUES:
        if cue in raw:
            add("summary", "high", cue, "delete the summary sentence")
            break
    if "补充一点" in raw or "补充一下" in raw or _SEQUENCE.search(raw):
        add("teaching", "high", "补充一点" if "补充一点" in raw else "首先", "remove the lecture framing")
    sentences = _sentences(raw)
    if len(sentences) >= 2 and _is_advice_sentence(sentences[-1]):
        add("advice", "high", sentences[-1][:20], "delete the closing advice")
    if channel != "post" and len(_LIST_LINE.findall(raw)) >= 2:
        add("list", "medium", "1.", "remove numbered list markers")
    if sum(raw.count(word) for word in HEDGE_WORDS) >= 2:
        add("hedge", "medium", "不过", "keep at most one hedge")
    if has_platform_meta(raw):
        add("meta", "high", "", "replace platform vocabulary")
    if _has_fixed_opening(raw):
        add("opening", "high", _OPENING_RE.match(raw).group(0), "remove the fixed opening")
    if _EXCLAMATION_RUN.search(raw) or len(re.findall(r"[！!]", raw)) > EXCLAMATION_MAX:
        add("exclamation", "medium", "！", "reduce exclamation marks")
    # A pure emoji reply is a legal action; only text messages with excessive emoji count.
    if _has_text_without_emoji(raw) and len(_emoji_matches(raw)) > EMOJI_MAX:
        add("emoji_density", "medium", "", "reduce emoji count")
    return found


def repair(text: object, violations: Iterable[Violation], *, channel: str = "",
           repair_advice: bool = True) -> str:
    """Preserve content; an opening with no remaining words may become silence."""
    raw = str(text or "")
    rules = {violation.rule for violation in violations}
    if not raw.strip() or not rules or _CODE_MARKER.search(raw):
        return raw
    value = raw
    if "summary" in rules:
        for cue in SUMMARY_CUES:
            value = re.sub(re.escape(cue) + r"[：:，,]?\s*", "", value)
        value = re.sub(r"^[。！？!?…\s]+", "", value)
    if "teaching" in rules:
        value = _TEACHING_PREFIX.sub("", value)
        value = value.replace("补充一点：", "").replace("补充一下：", "")
        value = _SEQUENCE.sub("", value)
    if "list" in rules and channel != "post":
        value = _LIST_LINE.sub("", value)
    if "advice" in rules and repair_advice:
        sentences = _sentences(value)
        if len(sentences) >= 2 and _is_advice_sentence(sentences[-1]):
            value = "".join(sentences[:-1])
    if "hedge" in rules:
        value = value.replace("其实", "").replace("确实", "")
        if value.count("不过") >= 2:
            value = value.replace("不过", "但", 1)
    if "meta" in rules:
        value = strip_platform_meta(value)
    if "opening" in rules:
        value = _strip_fixed_opening(value)
    if "exclamation" in rules:
        value = _normalize_exclamation(value)
    if "emoji_density" in rules:
        value = _normalize_emoji(value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value if value or "opening" in rules else raw


def guard_text(text: object, *, channel: str = "", persona: dict | None = None,
               repair_advice: bool = True, natural_only: bool = False) -> GuardResult:
    raw = str(text or "")
    violations = lint(raw, channel=channel, persona=persona)
    if natural_only:
        violations = [violation for violation in violations if violation.rule in NATURAL_RULES]
    if not violations:
        return GuardResult(raw, (), False)
    if natural_only:
        rules = {violation.rule for violation in violations}
        repaired = _strip_fixed_opening(raw) if "opening" in rules else raw
        if "exclamation" in rules:
            repaired = _normalize_exclamation(repaired)
        if "emoji_density" in rules:
            repaired = _normalize_emoji(repaired)
    else:
        repaired = repair(raw, violations, channel=channel, repair_advice=repair_advice)
    return GuardResult(repaired, tuple(violations), repaired != raw)


def guard_parts(parts: Iterable[object], *, channel: str = "", persona: dict | None = None,
                repair_advice: bool = True, natural_only: bool = False) -> tuple[list[str], list[str], bool]:
    """(nonempty parts, rule names, any repair); empty repairs become silence."""
    kept: list[str] = []
    rules: set[str] = set()
    repaired = False
    for part in parts:
        result = guard_text(part, channel=channel, persona=persona, repair_advice=repair_advice,
                            natural_only=natural_only)
        if result.text.strip():
            kept.append(result.text)
        rules.update(violation.rule for violation in result.violations)
        repaired = repaired or result.repaired
    return kept, sorted(rules), repaired


async def guard_turn(text: object, *, channel: str = "", persona: dict | None = None,
                     rewrite=None, repair_advice: bool = True) -> GuardResult:
    """Optional one-shot rewrite hook; deterministic repair remains the fallback."""
    raw = str(text or "")
    violations = lint(raw, channel=channel, persona=persona)
    if not violations:
        return GuardResult(raw, (), False)
    if rewrite is not None and any(violation.severity == "high" for violation in violations):
        rewritten = str(await rewrite(raw, violations) or "").strip()
        if rewritten and not lint(rewritten, channel=channel, persona=persona):
            return GuardResult(rewritten, tuple(violations), False, True)
    repaired = repair(raw, violations, channel=channel, repair_advice=repair_advice)
    return GuardResult(repaired, tuple(violations), repaired != raw)
