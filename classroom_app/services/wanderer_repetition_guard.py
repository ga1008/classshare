"""Deterministic self-repetition suppression (audit D4).

The audit found catchphrases and openings recycled across comments, classroom
messages and chats. This module compares one generated message with the
wanderer's own recent public texts: any shared 8-character run, or the third
message with the same 8-character opening in the last 20, is dropped before
posting. The prompt-side memory/voice notes explain the intent; this is the
hard stop.
"""
from __future__ import annotations

from typing import Any, Iterable

REPEAT_MIN_CHARS = 8
OPENING_CHARS = 8
OPENING_WINDOW = 20
OPENING_MAX = 2
PUBLIC_TEXT_ACTIONS = ("comment", "reply", "classroom_message", "social_message", "social_friend")


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def normalize_for_repetition(text: object) -> str:
    raw = str(text or "").lower()
    return "".join(ch for ch in raw if ch.isalnum() or "一" <= ch <= "鿿")


def _grams(text: object, size: int = REPEAT_MIN_CHARS) -> set[str]:
    value = normalize_for_repetition(text)
    if len(value) < size:
        return set()
    return {value[index:index + size] for index in range(len(value) - size + 1)}


def recent_self_texts(conn: Any, wanderer_id: Any, limit: int = 50) -> list[str]:
    """Own recent public texts, oldest first."""
    placeholders = ",".join("?" for _ in PUBLIC_TEXT_ACTIONS)
    rows = conn.execute(
        f"SELECT content_preview FROM wanderer_action_log WHERE wanderer_id = ? "
        f"AND action_kind IN ({placeholders}) AND content_preview <> '' ORDER BY id DESC LIMIT ?",
        (int(wanderer_id), *PUBLIC_TEXT_ACTIONS, int(limit)),
    ).fetchall()
    return [str(_row_value(row, "content_preview") or "") for row in reversed(rows)]


def repeated_span(text: object, recent_texts: Iterable[object], *,
                  min_chars: int = REPEAT_MIN_CHARS) -> bool:
    """True when any ``min_chars`` run already appears in the recent texts."""
    recent_grams: set[str] = set()
    for item in recent_texts:
        recent_grams.update(_grams(item, min_chars))
    return bool(recent_grams and (_grams(text, min_chars) & recent_grams))


def opening_repeated(text: object, recent_texts: Iterable[object], *,
                     chars: int = OPENING_CHARS, window: int = OPENING_WINDOW,
                     max_count: int = OPENING_MAX) -> bool:
    """True when the same opening already appears ``max_count`` times."""
    value = normalize_for_repetition(text)
    if len(value) < chars:
        return False
    opening = value[:chars]
    count = 0
    candidates = [str(item or "") for item in recent_texts]
    for item in candidates[-window:]:
        other = normalize_for_repetition(item)
        if len(other) >= chars and other[:chars] == opening:
            count += 1
    return count >= max_count


def filter_repetition_parts(parts: Iterable[object], recent_texts: Iterable[object], *,
                            min_chars: int = REPEAT_MIN_CHARS,
                            opening_chars: int = OPENING_CHARS,
                            opening_window: int = OPENING_WINDOW,
                            opening_max: int = OPENING_MAX) -> tuple[list[str], int]:
    """(kept parts, dropped count); kept parts join the comparison set."""
    recent = [str(item or "") for item in recent_texts if str(item or "").strip()]
    kept: list[str] = []
    dropped = 0
    for item in parts:
        text = str(item or "").strip()
        if not text:
            continue
        if repeated_span(text, recent, min_chars=min_chars) or opening_repeated(
            text, recent, chars=opening_chars, window=opening_window, max_count=opening_max
        ):
            dropped += 1
            continue
        kept.append(text)
        recent.append(text)
    return kept, dropped
