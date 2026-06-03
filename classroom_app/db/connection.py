import sqlite3
import sys
import threading

from .. import config


_sqlite_journal_mode_lock = threading.Lock()
_sqlite_journal_mode_paths: set[str] = set()


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


def get_db_connection():
    """Return a SQLite connection with LanShare's concurrency pragmas applied."""
    try:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        timeout_seconds = max(float(config.SQLITE_BUSY_TIMEOUT_MS) / 1000.0, 1.0)
        conn = sqlite3.connect(config.DB_PATH, timeout=timeout_seconds)
        _apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        print(f"[DB ERROR] Unable to connect to SQLite database: {exc}")
        sys.exit(1)
