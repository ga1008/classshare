from __future__ import annotations

from typing import Any


MIN_NEWS_COVER_WIDTH = 480
MIN_NEWS_COVER_HEIGHT = 260
MIN_NEWS_COVER_ASPECT_RATIO = 1.2
MAX_NEWS_COVER_ASPECT_RATIO = 3.4


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def is_suitable_news_cover_dimensions(width: Any, height: Any) -> bool:
    """Return whether an image can work as a wide editorial news cover."""
    normalized_width = _positive_int(width)
    normalized_height = _positive_int(height)
    if normalized_width < MIN_NEWS_COVER_WIDTH or normalized_height < MIN_NEWS_COVER_HEIGHT:
        return False
    ratio = normalized_width / max(normalized_height, 1)
    return MIN_NEWS_COVER_ASPECT_RATIO <= ratio <= MAX_NEWS_COVER_ASPECT_RATIO
