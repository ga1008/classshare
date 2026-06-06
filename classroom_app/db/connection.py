import sqlite3
import sys
import threading

from .. import config
from .errors import DatabaseBackendState, DatabaseConfigurationError, redact_database_url
from .postgres import connect_postgres


_sqlite_journal_mode_lock = threading.Lock()
_sqlite_journal_mode_paths: set[str] = set()
SUPPORTED_DB_ENGINES = {"sqlite", "postgres"}


def get_configured_db_engine() -> str:
    engine = str(getattr(config, "DB_ENGINE", "sqlite") or "sqlite").strip().lower()
    if engine not in SUPPORTED_DB_ENGINES:
        raise DatabaseConfigurationError(
            f"Unsupported DB_ENGINE '{engine}'. Supported values: sqlite, postgres."
        )
    return engine


def database_backend_state() -> DatabaseBackendState:
    engine = get_configured_db_engine()
    if engine == "sqlite":
        return DatabaseBackendState(engine=engine, configured=True, details=str(config.DB_PATH))
    return DatabaseBackendState(
        engine=engine,
        configured=bool(getattr(config, "DATABASE_URL", "")),
        details=redact_database_url(getattr(config, "DATABASE_URL", "")),
    )


def begin_immediate_transaction(conn, *, engine: str | None = None) -> None:
    """Start SQLite's reserved write transaction; PostgreSQL uses the active implicit transaction."""
    db_engine = (engine or get_configured_db_engine()).strip().lower()
    if db_engine == "sqlite":
        conn.execute("BEGIN IMMEDIATE")
        return
    if db_engine == "postgres":
        return
    raise DatabaseConfigurationError(
        f"Unsupported DB engine '{db_engine}' for immediate transaction."
    )


def execute_insert_returning_id(
    conn,
    sql: str,
    params: tuple | list,
    *,
    id_column: str = "id",
    engine: str | None = None,
) -> int:
    db_engine = (engine or get_configured_db_engine()).strip().lower()
    statement = str(sql).rstrip()
    if db_engine == "postgres":
        if " RETURNING " not in statement.upper():
            statement = f"{statement} RETURNING {id_column}"
        cursor = conn.execute(statement, tuple(params))
        row = cursor.fetchone()
        return int(row[id_column])
    if db_engine == "sqlite":
        cursor = conn.execute(statement, tuple(params))
        return int(cursor.lastrowid)
    raise DatabaseConfigurationError(
        f"Unsupported DB engine '{db_engine}' for insert returning id."
    )


def _apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    db_key = str(config.DB_PATH.resolve())
    if db_key not in _sqlite_journal_mode_paths:
        with _sqlite_journal_mode_lock:
            if db_key not in _sqlite_journal_mode_paths:
                conn.execute("PRAGMA journal_mode=WAL;")
                _sqlite_journal_mode_paths.add(db_key)
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute(f"PRAGMA busy_timeout = {int(max(0, config.SQLITE_BUSY_TIMEOUT_MS))};")
    conn.execute(f"PRAGMA wal_autocheckpoint = {int(max(1, config.SQLITE_WAL_AUTOCHECKPOINT_PAGES))};")
    if config.SQLITE_CACHE_SIZE_KB:
        conn.execute(f"PRAGMA cache_size = {-int(abs(config.SQLITE_CACHE_SIZE_KB))};")
    conn.execute("PRAGMA foreign_keys = ON;")


class LanShareSQLiteConnection(sqlite3.Connection):
    """SQLite connection that also closes when used as a context manager."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_db_connection():
    """Return a database connection for the configured LanShare backend."""
    engine = get_configured_db_engine()
    if engine == "postgres":
        if not getattr(config, "DATABASE_URL", ""):
            raise DatabaseConfigurationError(
                "DB_ENGINE=postgres requires DATABASE_URL. Refusing to fall back to SQLite."
            )
        if not getattr(config, "POSTGRES_BACKEND_READY", False):
            raise DatabaseConfigurationError(
                "DB_ENGINE=postgres is recognized but gated until the PostgreSQL dialect, "
                "schema migration, data migration, and deployment targets are complete. "
                "Set POSTGRES_BACKEND_READY=true only in an explicit PostgreSQL test or cutover flow."
            )
        return connect_postgres()

    try:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        timeout_seconds = max(float(config.SQLITE_BUSY_TIMEOUT_MS) / 1000.0, 1.0)
        conn = sqlite3.connect(
            config.DB_PATH,
            timeout=timeout_seconds,
            factory=LanShareSQLiteConnection,
        )
        _apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        print(f"[DB ERROR] Unable to connect to SQLite database: {exc}")
        sys.exit(1)
