"""Deterministic group-echo suppression for 散修 public speech.

The audit (docs/wanderer-voice-audit-2026-11-01.md §D1) found whole classrooms
repeating the same suggestion and wanderers confirming each other. This module
is the cheap, offline half of the fix: compare a generated message with the
last few messages in the same room, and drop a duplicate or a pure
"说得对/我认/附议" reply before it is posted. The prompt block in
``wanderer_prompt_service`` is the other half.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

ECHO_THRESHOLD = 0.62
ECHO_MIN_CHARS = 12
CONFIRMATION_MAX_CHARS = 20

# Whole-message agreement only: a message that goes on to say something
# ("我支持你", "我认为这个方案不错", "支持本地部署") is content, not an echo.
_CONFIRMATION_RE = re.compile(
    r"^(?:"
    r"(?:我|我也|我们|咱)?(?:完全|非常|挺|很|比较)?(?:同意|赞成|支持|认同|认可|附议)"
    r"|(?:[^，。！？!?；;\s]{0,6})?(?:说得|说的)(?:挺|很|真)?(?:对|在理|到位|没错|有道理|有见解)"
    r"|(?:[^，。！？!?；;\s]{0,6})?说(?:得)?(?:透|到点子上)"
    r"|(?:我认|我认了|认了)"
    r"|\+1"
    r")[。！？!?…~～]*$"
)
_CONTRAST = ("但", "不过", "可是", "然而", "不同意", "不认同", "不赞成", "不支持", "反对")


def normalize_for_echo(text: object) -> str:
    """Keep CJK, ASCII letters and digits; punctuation and emoji are noise."""
    raw = str(text or "").lower()
    return "".join(ch for ch in raw if ch.isalnum() or "一" <= ch <= "鿿")


def echo_similarity(left: object, right: object) -> float:
    """0..1; max of edit ratio and shorter-text coverage."""
    a, b = normalize_for_echo(left), normalize_for_echo(right)
    if len(a) < ECHO_MIN_CHARS or len(b) < ECHO_MIN_CHARS:
        return 0.0
    matcher = SequenceMatcher(None, a, b)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    coverage = matched / min(len(a), len(b))
    return max(matcher.ratio(), coverage)


def is_confirmation_echo(text: object) -> bool:
    """Pure agreement with no added content; a disagreement is never dropped."""
    raw = str(text or "").strip()
    if not raw or len(raw) > CONFIRMATION_MAX_CHARS:
        return False
    body = raw.rstrip("。！？!?…")
    if any(sep in body for sep in ("，", ",", "；", ";")):
        return False
    if any(word in body for word in _CONTRAST):
        return False
    return bool(_CONFIRMATION_RE.match(body))


def filter_echo_parts(parts: Iterable[object], recent_texts: Iterable[object], *,
                      threshold: float = ECHO_THRESHOLD) -> tuple[list[str], int]:
    """(kept parts, dropped count); kept parts join the comparison set."""
    recent = [str(item or "").strip() for item in recent_texts if str(item or "").strip()]
    kept: list[str] = []
    dropped = 0
    for item in parts:
        text = str(item or "").strip()
        if not text:
            continue
        if (recent and is_confirmation_echo(text)) or any(
            echo_similarity(text, previous) >= threshold for previous in recent
        ):
            dropped += 1
            continue
        kept.append(text)
        recent.append(text)
    return kept, dropped
