from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


APP_TIMEZONE_NAME = (
    os.getenv("APP_TIMEZONE")
    or os.getenv("TZ")
    or "Asia/Shanghai"
).strip() or "Asia/Shanghai"


def _load_timezone() -> timezone:
    try:
        return ZoneInfo(APP_TIMEZONE_NAME)
    except Exception:
        if APP_TIMEZONE_NAME == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


APP_TIMEZONE = _load_timezone()


def app_timezone_name() -> str:
    return APP_TIMEZONE_NAME


def aware_local_now() -> datetime:
    return datetime.now(APP_TIMEZONE)


def local_now() -> datetime:
    return aware_local_now().replace(tzinfo=None)


def local_iso(*, timespec: str = "seconds") -> str:
    return local_now().isoformat(timespec=timespec)


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    normalized = str(value).strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None


def to_local_datetime(value: str | datetime | None) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(APP_TIMEZONE).replace(tzinfo=None)


def format_local_datetime(
    value: str | datetime | None,
    fmt: str = "%Y-%m-%d %H:%M",
    *,
    fallback: str = "",
) -> str:
    parsed = to_local_datetime(value)
    if parsed is None:
        return fallback
    return parsed.strftime(fmt)


def format_local_time(value: str | datetime | None, *, fallback: str = "") -> str:
    return format_local_datetime(value, "%H:%M", fallback=fallback)
