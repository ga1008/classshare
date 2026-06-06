from __future__ import annotations

import importlib
from typing import Any, Iterable, Sequence

from .. import config
from .errors import DatabaseConfigurationError, DatabaseConnectionError, redact_database_url


POSTGRES_DRIVER_REQUIREMENT = "psycopg[binary]==3.3.4"


def load_psycopg_driver() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        raise DatabaseConfigurationError(
            "DB_ENGINE=postgres requires the psycopg driver. "
            f"Install {POSTGRES_DRIVER_REQUIREMENT} before enabling PostgreSQL."
        ) from exc


def validate_database_url(database_url: str) -> str:
    normalized = str(database_url or "").strip()
    if not normalized:
        raise DatabaseConfigurationError(
            "DB_ENGINE=postgres requires DATABASE_URL. Refusing to fall back to SQLite."
        )
    if not normalized.startswith(("postgresql://", "postgres://")):
        raise DatabaseConfigurationError(
            "DATABASE_URL must start with postgresql:// or postgres:// when DB_ENGINE=postgres."
        )
    return normalized


def qmark_to_psycopg(sql: str) -> str:
    """Convert SQLite qmark placeholders to psycopg placeholders outside literals."""
    text = str(sql)
    output: list[str] = []
    index = 0
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            output.append(char)
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                output.append("*/")
                index += 2
                in_block_comment = False
            else:
                output.append(char)
                index += 1
            continue

        if in_single_quote:
            output.append(char)
            if char == "'" and next_char == "'":
                output.append(next_char)
                index += 2
                continue
            if char == "'":
                in_single_quote = False
            index += 1
            continue

        if in_double_quote:
            output.append(char)
            if char == '"' and next_char == '"':
                output.append(next_char)
                index += 2
                continue
            if char == '"':
                in_double_quote = False
            index += 1
            continue

        if char == "-" and next_char == "-":
            output.append("--")
            index += 2
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            output.append("/*")
            index += 2
            in_block_comment = True
            continue
        if char == "'":
            output.append(char)
            index += 1
            in_single_quote = True
            continue
        if char == '"':
            output.append(char)
            index += 1
            in_double_quote = True
            continue
        if char == "?":
            output.append("%s")
            index += 1
            continue

        output.append(char)
        index += 1

    return "".join(output)


class LanSharePostgresConnection:
    """Small sqlite-like facade over psycopg while services are migrated by domain."""

    def __init__(self, raw_connection: Any):
        self._raw_connection = raw_connection

    @property
    def raw_connection(self) -> Any:
        return self._raw_connection

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        converted = qmark_to_psycopg(sql)
        if params is None:
            return self._raw_connection.execute(converted)
        return self._raw_connection.execute(converted, tuple(params))

    def executemany(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> Any:
        converted = qmark_to_psycopg(sql)
        with self._raw_connection.cursor() as cursor:
            cursor.executemany(converted, [tuple(params) for params in params_seq])
            return cursor

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()

    def close(self) -> None:
        self._raw_connection.close()

    def __enter__(self) -> "LanSharePostgresConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_connection, name)


def _session_timeout_value(milliseconds: int) -> str:
    return f"{max(0, int(milliseconds))}ms"


def apply_postgres_session_settings(raw_connection: Any) -> None:
    settings = (
        ("statement_timeout", _session_timeout_value(config.POSTGRES_STATEMENT_TIMEOUT_MS)),
        ("lock_timeout", _session_timeout_value(config.POSTGRES_LOCK_TIMEOUT_MS)),
        (
            "idle_in_transaction_session_timeout",
            _session_timeout_value(config.POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS),
        ),
        ("application_name", "lanshare-app"),
    )
    for setting_name, setting_value in settings:
        raw_connection.execute(
            "SELECT set_config(%s, %s, false)",
            (setting_name, setting_value),
        )


def connect_postgres(*, driver: Any | None = None, row_factory: Any | None = None) -> LanSharePostgresConnection:
    database_url = validate_database_url(config.DATABASE_URL)
    psycopg_driver = driver or load_psycopg_driver()
    if row_factory is None:
        row_factory = getattr(getattr(psycopg_driver, "rows", None), "dict_row", None)
    try:
        raw_connection = psycopg_driver.connect(
            database_url,
            autocommit=False,
            connect_timeout=10,
            row_factory=row_factory,
        )
        apply_postgres_session_settings(raw_connection)
        return LanSharePostgresConnection(raw_connection)
    except DatabaseConfigurationError:
        raise
    except Exception as exc:
        redacted_url = redact_database_url(database_url)
        raise DatabaseConnectionError(f"Unable to connect to PostgreSQL: {redacted_url}") from exc

