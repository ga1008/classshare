"""Account-owned UI preferences. Executed at startup, never during page reads."""

from typing import Any

from .connection import get_configured_db_engine


def ensure_user_ui_preferences_schema(conn: Any, *, engine: str | None = None) -> None:
    """Add nullable preferences without rewriting existing account choices."""
    engine = engine or get_configured_db_engine()
    if engine not in {"sqlite", "postgres"}:
        raise ValueError("Unsupported UI preferences database engine")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_ui_preferences (
            user_role TEXT NOT NULL CHECK (user_role IN ('student', 'teacher')),
            user_pk BIGINT NOT NULL CHECK (user_pk > 0),
            palette_key TEXT NOT NULL DEFAULT 'indigo',
            appearance TEXT NULL,
            glass TEXT NULL,
            backdrop TEXT NULL,
            backdrop_color TEXT NULL,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_role, user_pk)
        )
        """
    )
    optional = ("appearance", "glass", "backdrop", "backdrop_color")
    if engine == "postgres":
        for column in optional:
            conn.execute(f'ALTER TABLE "user_ui_preferences" ADD COLUMN IF NOT EXISTS "{column}" TEXT NULL')
    else:
        columns = {str(row[1]) for row in conn.execute('PRAGMA table_info("user_ui_preferences")').fetchall()}
        for column in optional:
            if column not in columns:
                conn.execute(f'ALTER TABLE "user_ui_preferences" ADD COLUMN "{column}" TEXT NULL')
