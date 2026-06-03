import sqlite3
from datetime import datetime, timezone

from .connection import get_db_connection


def _normalize_session_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "session_user_key": str(row["session_user_key"] or ""),
        "session_id": str(row["session_id"] or ""),
        "ip": str(row["ip"] or ""),
        "last_login": str(row["last_login"] or ""),
        "user_id": str(row["user_id"] or ""),
        "role": str(row["role"] or ""),
        "name": str(row["name"] or ""),
        "expires_at": str(row["expires_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_USER_SESSIONS_TABLE_SQL = '''
                CREATE TABLE IF NOT EXISTS user_sessions
                (
                    session_user_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    name TEXT DEFAULT '',
                    ip TEXT DEFAULT '',
                    last_login TEXT DEFAULT '',
                    expires_at TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                '''

_USER_SESSIONS_INDEX_SQLS = (
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_role "
    "ON user_sessions (user_id, role, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at "
    "ON user_sessions (expires_at)",
)


def _ensure_user_sessions_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_USER_SESSIONS_TABLE_SQL)
    for statement in _USER_SESSIONS_INDEX_SQLS:
        conn.execute(statement)


def _user_sessions_quick_check(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("PRAGMA quick_check('user_sessions')").fetchone()
    except sqlite3.DatabaseError:
        row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0] if row else "").strip()


def _is_user_sessions_storage_issue(message: object) -> bool:
    text = str(message or "").lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "user_sessions",
            "idx_user_sessions",
            "sqlite_autoindex_user_sessions",
            "database disk image is malformed",
            "malformed",
        )
    )


def repair_user_sessions_storage(
    conn: sqlite3.Connection | None = None,
    *,
    force: bool = False,
) -> bool:
    """Repair the high-churn user_sessions indexes without touching business data."""
    owns_connection = conn is None
    active_conn = conn or get_db_connection()
    try:
        _ensure_user_sessions_schema(active_conn)
        check_result = "forced" if force else _user_sessions_quick_check(active_conn)
        if not force and check_result == "ok":
            return False
        if not force and not _is_user_sessions_storage_issue(check_result):
            return False

        print(f"[SESSION] Repairing user_sessions storage: {check_result}")
        active_conn.execute("REINDEX user_sessions")
        active_conn.execute(
            "DELETE FROM user_sessions WHERE expires_at <= ?",
            (_utcnow_iso(),),
        )
        verify_result = _user_sessions_quick_check(active_conn)
        if verify_result != "ok":
            raise sqlite3.DatabaseError(
                f"user_sessions quick_check failed after repair: {verify_result}"
            )
        if owns_connection:
            active_conn.commit()
        return True
    except Exception:
        if owns_connection:
            try:
                active_conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if owns_connection:
            active_conn.close()


def _run_user_session_operation(operation):
    try:
        return operation()
    except sqlite3.DatabaseError as exc:
        if not _is_user_sessions_storage_issue(exc):
            raise
        print(f"[SESSION] user_sessions operation failed; repairing and retrying once: {exc}")
        repair_user_sessions_storage(force=True)
        return operation()
