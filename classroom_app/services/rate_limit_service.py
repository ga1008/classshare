from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


class RateLimitExceededError(PermissionError):
    def __init__(self, message: str, *, retry_after_seconds: int = 1):
        super().__init__(message)
        self.retry_after_seconds = max(int(retry_after_seconds or 1), 1)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_rate_limit_window_start(*, window_seconds: int, now: Optional[datetime] = None) -> tuple[datetime, str]:
    normalized_window = max(int(window_seconds or 0), 1)
    current = now or datetime.now()
    return current, (current - timedelta(seconds=normalized_window)).isoformat()


def calculate_retry_after_seconds(
    *,
    oldest_event_at: Optional[str],
    window_seconds: int,
    now: Optional[datetime] = None,
) -> int:
    normalized_window = max(int(window_seconds or 0), 1)
    parsed_oldest = parse_iso_datetime(oldest_event_at)
    if parsed_oldest is None:
        return normalized_window

    current = now or (datetime.now(parsed_oldest.tzinfo) if parsed_oldest.tzinfo is not None else datetime.now())
    remaining = (parsed_oldest + timedelta(seconds=normalized_window)) - current
    if remaining.total_seconds() <= 0:
        return 1
    return max(int(remaining.total_seconds()) + (1 if remaining.microseconds > 0 else 0), 1)
