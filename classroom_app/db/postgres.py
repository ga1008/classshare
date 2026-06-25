from __future__ import annotations

import importlib
import re
import threading
from typing import Any, Iterable, Sequence

from .. import config
from .errors import (
    DatabaseBusyError,
    DatabaseConfigurationError,
    DatabaseConnectionError,
    redact_database_url,
)


POSTGRES_DRIVER_REQUIREMENT = "psycopg[binary]==3.3.4"

# Per-process connection pool (psycopg_pool). Created lazily on first pooled
# checkout so importing this module never opens sockets, and so each worker
# container builds its own pool. See connect_postgres / _get_connection_pool.
_connection_pool: Any | None = None
_connection_pool_lock = threading.Lock()
_LIKE_NOCASE_RE = re.compile(r"\bLIKE\s+(%s)\s+COLLATE\s+NOCASE\b", re.IGNORECASE)
_COLLATE_NOCASE_RE = re.compile(r"\s+COLLATE\s+NOCASE\b", re.IGNORECASE)


def _replace_group_concat(sql: str) -> str:
    result: list[str] = []
    index = 0
    pattern = re.compile(r"group_concat\s*\(", re.IGNORECASE)

    while True:
        match = pattern.search(sql, index)
        if not match:
            result.append(sql[index:])
            break

        open_paren = match.end() - 1
        depth = 0
        position = open_paren
        in_single_quote = False
        close_paren = -1

        while position < len(sql):
            char = sql[position]
            next_char = sql[position + 1] if position + 1 < len(sql) else ""
            if in_single_quote:
                if char == "'" and next_char == "'":
                    position += 2
                    continue
                if char == "'":
                    in_single_quote = False
                position += 1
                continue
            if char == "'":
                in_single_quote = True
                position += 1
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_paren = position
                    break
            position += 1

        if close_paren < 0:
            result.append(sql[index:])
            break

        result.append(sql[index:match.start()])
        inner = sql[open_paren + 1:close_paren].strip()
        distinct = ""
        if inner.upper().startswith("DISTINCT "):
            distinct = "DISTINCT "
            inner = inner[len("DISTINCT "):].strip()
        result.append(f"STRING_AGG({distinct}({inner})::text, ',')")
        index = close_paren + 1

    return "".join(result)


def _escape_literal_percent_markers(sql: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if char == "%":
            if next_char in {"s", "b", "t"}:
                output.append(char)
            elif next_char == "%":
                output.append("%%")
                index += 1
            else:
                output.append("%%")
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def sqlite_sql_to_psycopg(sql: str) -> str:
    converted = qmark_to_psycopg(sql)
    converted = _replace_group_concat(converted)
    converted = _LIKE_NOCASE_RE.sub(r"ILIKE \1", converted)
    converted = _COLLATE_NOCASE_RE.sub("", converted)
    return _escape_literal_percent_markers(converted)


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

    def __init__(self, raw_connection: Any, pool: Any | None = None):
        self._raw_connection = raw_connection
        self._pool = pool

    @property
    def raw_connection(self) -> Any:
        return self._raw_connection

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        converted = sqlite_sql_to_psycopg(sql)
        if params is None:
            return self._raw_connection.execute(converted)
        return self._raw_connection.execute(converted, tuple(params))

    def executemany(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> Any:
        converted = sqlite_sql_to_psycopg(sql)
        with self._raw_connection.cursor() as cursor:
            cursor.executemany(converted, [tuple(params) for params in params_seq])
            return cursor

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()

    def close(self) -> None:
        raw = self._raw_connection
        if raw is None:
            return
        # Prevent reuse-after-return and double-return.
        self._raw_connection = None
        if self._pool is not None:
            # Return the physical connection to the pool instead of closing it.
            self._pool.putconn(raw)
        else:
            raw.close()

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


class SqliteCompatibleRow(dict):
    """Dict row that also supports sqlite3.Row-style integer indexing."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = tuple(columns)
        self._values = tuple(values)
        super().__init__(zip(self._columns, self._values))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _cursor_column_name(column: Any) -> str:
    name = getattr(column, "name", None)
    if name is not None:
        return str(name)
    try:
        return str(column[0])
    except (IndexError, TypeError):
        return str(column)


def sqlite_compatible_dict_row(cursor: Any):
    columns = tuple(_cursor_column_name(column) for column in (cursor.description or ()))

    def make_row(values: Sequence[Any]) -> SqliteCompatibleRow:
        return SqliteCompatibleRow(columns, values)

    return make_row


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
    # Apply every session setting in a single round-trip. With no pooling each
    # request opens a fresh connection, so collapsing four set_config calls into
    # one statement removes three network round-trips per request.
    projection = ", ".join("set_config(%s, %s, false)" for _ in settings)
    params: list[Any] = []
    for setting_name, setting_value in settings:
        params.append(setting_name)
        params.append(setting_value)
    raw_connection.execute(f"SELECT {projection}", tuple(params))


def _configure_pooled_connection(raw_connection: Any) -> None:
    """Run once per *physical* pooled connection (statement/lock timeouts, app name).

    The session settings use ``set_config(..., is_local=false)`` (session scope), so
    they survive the commit. The commit is required: psycopg_pool discards any
    connection the configure callback leaves in an open transaction (INTRANS).
    """
    apply_postgres_session_settings(raw_connection)
    raw_connection.commit()


def _build_connection_pool(row_factory: Any) -> Any:
    try:
        from psycopg_pool import ConnectionPool
    except ModuleNotFoundError as exc:
        raise DatabaseConfigurationError(
            "POSTGRES_POOL_ENABLED=true requires the psycopg-pool package. "
            "Install psycopg-pool, or set POSTGRES_POOL_ENABLED=false to use per-request connections."
        ) from exc

    database_url = validate_database_url(config.DATABASE_URL)
    pool = ConnectionPool(
        conninfo=database_url,
        min_size=int(config.POSTGRES_POOL_MIN),
        max_size=int(config.POSTGRES_POOL_MAX),
        timeout=float(config.POSTGRES_POOL_CHECKOUT_TIMEOUT),
        max_lifetime=float(config.POSTGRES_POOL_MAX_LIFETIME),
        max_idle=float(config.POSTGRES_POOL_MAX_IDLE),
        kwargs={"autocommit": False, "row_factory": row_factory},
        configure=_configure_pooled_connection,
        check=ConnectionPool.check_connection,
        name="lanshare-app",
        open=True,
    )
    print(
        f"[DB] PostgreSQL connection pool ready: min={config.POSTGRES_POOL_MIN}, "
        f"max={config.POSTGRES_POOL_MAX}, checkout_timeout={config.POSTGRES_POOL_CHECKOUT_TIMEOUT}s, "
        f"max_lifetime={config.POSTGRES_POOL_MAX_LIFETIME}s"
    )
    return pool


def _is_pool_timeout(exc: BaseException) -> bool:
    """True when the pool could not hand out a connection within the checkout timeout."""
    try:
        from psycopg_pool import PoolTimeout
    except Exception:  # pragma: no cover - psycopg_pool always present when pooling is on
        return False
    return isinstance(exc, PoolTimeout)


def _get_connection_pool(row_factory: Any) -> Any:
    global _connection_pool
    if _connection_pool is None:
        with _connection_pool_lock:
            if _connection_pool is None:
                _connection_pool = _build_connection_pool(row_factory)
    return _connection_pool


def get_pool_stats() -> dict[str, Any] | None:
    """Snapshot of the live pool for the health endpoint; None when pooling is off/unused."""
    pool = _connection_pool
    if pool is None:
        return None
    try:
        stats = pool.get_stats()
    except Exception:
        return None
    return {
        "min": int(config.POSTGRES_POOL_MIN),
        "max": int(config.POSTGRES_POOL_MAX),
        "size": stats.get("pool_size"),
        "available": stats.get("pool_available"),
        "waiting": stats.get("requests_waiting"),
    }


def close_connection_pool() -> None:
    """Close the pool on shutdown (best-effort)."""
    global _connection_pool
    pool = _connection_pool
    if pool is None:
        return
    _connection_pool = None
    try:
        pool.close()
    except Exception as exc:  # pragma: no cover - shutdown best-effort
        print(f"[DB] connection pool close failed: {exc}")


def connect_postgres(*, driver: Any | None = None, row_factory: Any | None = None) -> LanSharePostgresConnection:
    database_url = validate_database_url(config.DATABASE_URL)
    caller_row_factory = row_factory
    if row_factory is None:
        row_factory = sqlite_compatible_dict_row

    # Pooled path (default). Bypassed when a driver is injected (tests) or a custom
    # row_factory is requested, since the pool is built once with the default factory.
    use_pool = (
        getattr(config, "POSTGRES_POOL_ENABLED", True)
        and driver is None
        and caller_row_factory is None
    )
    if use_pool:
        try:
            pool = _get_connection_pool(row_factory)
            raw_connection = pool.getconn()
            return LanSharePostgresConnection(raw_connection, pool=pool)
        except DatabaseConfigurationError:
            raise
        except Exception as exc:
            # Pool saturated (all connections busy past the checkout timeout): signal
            # "temporarily busy" so the web layer can return 503 + Retry-After instead
            # of a generic 500. This is graceful degradation under load, not an outage.
            if _is_pool_timeout(exc):
                raise DatabaseBusyError("数据库连接繁忙，请稍候重试。") from exc
            redacted_url = redact_database_url(database_url)
            raise DatabaseConnectionError(
                f"Unable to acquire pooled PostgreSQL connection: {redacted_url}"
            ) from exc

    # Fallback: per-request connection (kill-switch off, driver injection, or custom row_factory).
    psycopg_driver = driver or load_psycopg_driver()
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

