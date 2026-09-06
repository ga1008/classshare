"""Account-owned UI preferences. Executed at startup, never during page reads."""

from typing import Any


def ensure_user_ui_preferences_schema(conn: Any) -> None:
    """The same additive schema is valid on SQLite and PostgreSQL."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_ui_preferences (
            user_role TEXT NOT NULL CHECK (user_role IN ('student', 'teacher')),
            user_pk BIGINT NOT NULL CHECK (user_pk > 0),
            palette_key TEXT NOT NULL DEFAULT 'indigo',
            version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_role, user_pk)
        )
        """
    )
