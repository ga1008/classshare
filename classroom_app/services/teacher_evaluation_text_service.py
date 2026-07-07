"""Text helpers for teacher evaluation analysis prose.

The editor stores the analysis as plain text, while the Word template needs to
render natural paragraphs and numbered points cleanly. Keep that parsing here so
API responses and export code share the same rules.
"""

from __future__ import annotations

import re
from typing import Any

_CJK_NUMERALS = r"\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341"
_POINT_PREFIX = (
    r"[1-9]\d{0,1}[.\uff0e\u3001]\s*[\u4e00-\u9fffA-Za-z]"
    r"|[\uff08(][1-9]\d{0,1}[\uff09)]\s*[\u4e00-\u9fffA-Za-z]"
    rf"|[{_CJK_NUMERALS}]+[.\uff0e\u3001]\s*[\u4e00-\u9fffA-Za-z]"
)
_NUMBERED_POINT_RE = re.compile(rf"(?<!^)(?<!\n)(?=({_POINT_PREFIX}))")


def split_analysis_blocks(text: Any, *, max_blocks: int = 24) -> list[str]:
    """Split evaluation analysis text into Word-renderable paragraphs/points."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    raw = raw.replace("```", "")
    raw = _NUMBERED_POINT_RE.sub("\n", raw)

    blocks: list[str] = []
    for line in raw.split("\n"):
        cleaned = line.strip()
        cleaned = re.sub(r"^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+)", "", cleaned).strip()
        if cleaned:
            blocks.append(cleaned)
        if len(blocks) >= max_blocks:
            break
    return blocks
