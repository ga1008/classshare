import sqlite3

from .connection import get_db_connection
from .repair import (
    _is_user_sessions_storage_issue,
    _normalize_session_row,
    _run_user_session_operation,
    _utcnow_iso,
    repair_user_sessions_storage,
)


def save_user_session(
    *,
    session_user_key: str,
    session_id: str,
    user_id: str,
    role: str | None = None,
    name: str | None = None,
    ip: str | None = None,
    last_login: str | None = None,
    expires_at: str,
) -> dict:
    normalized_user_key = str(session_user_key or "").strip()
    if not normalized_user_key:
        raise ValueError("session_user_key is required")

    def _save() -> dict:
        timestamp = _utcnow_iso()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_sessions (
                    session_user_key,
                    session_id,
                    user_id,
                    role,
                    name,
                    ip,
                    last_login,
                    expires_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_user_key) DO UPDATE SET
                    session_id = excluded.session_id,
                    user_id = excluded.user_id,
                    role = excluded.role,
                    name = excluded.name,
                    ip = excluded.ip,
                    last_login = excluded.last_login,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_user_key,
                    str(session_id or "").strip(),
                    str(user_id or "").strip(),
                    str(role or "").strip(),
                    str(name or "").strip(),
                    str(ip or "").strip(),
                    str(last_login or "").strip(),
                    str(expires_at or "").strip(),
                    timestamp,
                ),
            )
            row = conn.execute(
                """
                SELECT session_user_key, session_id, user_id, role, name, ip, last_login, expires_at, updated_at
                FROM user_sessions
                WHERE session_user_key = ?
                LIMIT 1
                """,
                (normalized_user_key,),
            ).fetchone()
            conn.commit()
        return _normalize_session_row(row) or {}

    return _run_user_session_operation(_save)

def get_user_session(session_user_key: str) -> dict | None:
    normalized_user_key = str(session_user_key or "").strip()
    if not normalized_user_key:
        return None

    def _get() -> dict | None:
        now_iso = _utcnow_iso()
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT session_user_key, session_id, user_id, role, name, ip, last_login, expires_at, updated_at
                FROM user_sessions
                WHERE session_user_key = ?
                LIMIT 1
                """,
                (normalized_user_key,),
            ).fetchone()
            if row is None:
                return None

            expires_at = str(row["expires_at"] or "")
            if expires_at and expires_at <= now_iso:
                conn.execute(
                    "DELETE FROM user_sessions WHERE session_user_key = ?",
                    (normalized_user_key,),
                )
                conn.commit()
                return None

        return _normalize_session_row(row)

    return _run_user_session_operation(_get)

def list_user_sessions() -> dict[str, dict]:
    def _list() -> dict[str, dict]:
        now_iso = _utcnow_iso()
        sessions: dict[str, dict] = {}
        with get_db_connection() as conn:
            conn.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?",
                (now_iso,),
            )
            rows = conn.execute(
                """
                SELECT session_user_key, session_id, user_id, role, name, ip, last_login, expires_at, updated_at
                FROM user_sessions
                ORDER BY updated_at DESC, session_user_key ASC
                """
            ).fetchall()
            conn.commit()

        for row in rows:
            normalized_row = _normalize_session_row(row)
            if not normalized_row:
                continue
            sessions[normalized_row["session_user_key"]] = {
                "session_id": normalized_row["session_id"],
                "ip": normalized_row["ip"],
                "last_login": normalized_row["last_login"],
                "user_id": normalized_row["user_id"],
                "role": normalized_row["role"],
                "name": normalized_row["name"],
                "expires_at": normalized_row["expires_at"],
                "updated_at": normalized_row["updated_at"],
            }
        return sessions

    return _run_user_session_operation(_list)

def list_user_session_roles(user_id: str) -> list[str]:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return []

    def _list_roles() -> list[str]:
        now_iso = _utcnow_iso()
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT role
                FROM user_sessions
                WHERE user_id = ?
                  AND expires_at > ?
                ORDER BY role ASC
                """,
                (normalized_user_id, now_iso),
            ).fetchall()
        return [
            str(row["role"] or "").strip().lower()
            for row in rows
            if str(row["role"] or "").strip()
        ]

    return _run_user_session_operation(_list_roles)

def delete_user_sessions(
    user_id: str,
    role: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    normalized_user_id = str(user_id or "").strip()
    normalized_role = str(role or "").strip().lower()
    if not normalized_user_id:
        return 0

    owns_connection = conn is None
    active_conn = conn or get_db_connection()

    def _delete(active_connection: sqlite3.Connection) -> int:
        if normalized_role:
            cursor = active_connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ? AND role = ?",
                (normalized_user_id, normalized_role),
            )
        else:
            cursor = active_connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ?",
                (normalized_user_id,),
            )
        return int(cursor.rowcount or 0)

    try:
        try:
            removed_count = _delete(active_conn)
        except sqlite3.DatabaseError as exc:
            if not _is_user_sessions_storage_issue(exc):
                raise
            print(f"[SESSION] Failed to delete user_sessions; repairing and retrying once: {exc}")
            repair_user_sessions_storage(active_conn, force=True)
            removed_count = _delete(active_conn)
        if owns_connection:
            active_conn.commit()
        return removed_count
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
