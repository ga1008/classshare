from __future__ import annotations

from dataclasses import dataclass


class LanShareDatabaseError(RuntimeError):
    """Base class for database boundary errors raised by LanShare."""


class DatabaseConfigurationError(LanShareDatabaseError):
    """Database settings are missing, unsupported, or unsafe."""


class DatabaseConnectionError(LanShareDatabaseError):
    """The configured database could not be reached."""


class DatabaseBusyError(LanShareDatabaseError):
    """The database is temporarily busy or locked."""


class DatabaseIntegrityError(LanShareDatabaseError):
    """A database integrity constraint failed."""


def is_unique_violation(exc: BaseException) -> bool:
    """True for a UNIQUE/primary-key conflict on SQLite or PostgreSQL (sqlstate 23505)."""
    if str(getattr(exc, "sqlstate", "") or getattr(exc, "pgcode", "") or "") == "23505":
        return True
    if any(cls.__name__ == "UniqueViolation" for cls in type(exc).__mro__):
        return True
    import sqlite3

    return isinstance(exc, sqlite3.IntegrityError) and "UNIQUE" in str(exc).upper()


def is_retryable_conflict(exc: BaseException) -> bool:
    """Lock/serialization conflicts a client may simply retry (SQLite busy, PG 40001/40P01/55P03)."""
    if str(getattr(exc, "sqlstate", "") or getattr(exc, "pgcode", "") or "") in {"40001", "40P01", "55P03"}:
        return True
    import sqlite3

    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


class DatabaseRetryableError(LanShareDatabaseError):
    """A database operation can be retried safely by the caller."""


class DatabaseProgrammingError(LanShareDatabaseError):
    """A SQL or schema assumption is invalid for the configured database."""


@dataclass(frozen=True)
class DatabaseBackendState:
    engine: str
    configured: bool
    details: str = ""


def redact_database_url(raw_url: str | None) -> str:
    """Return a log-safe database URL without credentials."""
    if not raw_url:
        return ""
    value = raw_url.strip()
    if not value:
        return ""
    if "@" not in value or "://" not in value:
        return value
    scheme, rest = value.split("://", 1)
    _, host_part = rest.rsplit("@", 1)
    return f"{scheme}://***:***@{host_part}"
