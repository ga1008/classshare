"""Durable behavior receipts. Initialize at startup, never in a request."""
from __future__ import annotations


BEHAVIOR_INBOX_TABLES = {
    "behavior_write_inbox": """CREATE TABLE IF NOT EXISTS behavior_write_inbox (
        batch_key TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        class_offering_id INTEGER NOT NULL,
        user_pk INTEGER NOT NULL,
        user_role TEXT NOT NULL,
        display_name TEXT NOT NULL,
        page_key TEXT,
        events_json TEXT NOT NULL,
        session_started_at TEXT,
        received_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'applied', 'quarantined', 'discarded')),
        applied_at TEXT,
        resolved_at TEXT,
        retry_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        result_json TEXT
    )""",
}

BEHAVIOR_INBOX_REQUIRED_COLUMNS = {
    "behavior_write_inbox": (
        "batch_key", "batch_id", "payload_hash", "class_offering_id", "user_pk", "user_role",
        "display_name", "page_key", "events_json", "session_started_at", "received_at", "state",
        "applied_at", "resolved_at", "retry_at", "attempts", "last_error", "result_json",
    ),
}


def ensure_behavior_inbox_schema(conn, *, engine=None) -> None:
    if engine is None:
        from .connection import get_configured_db_engine
        engine = get_configured_db_engine()
    for sql in BEHAVIOR_INBOX_TABLES.values():
        conn.execute(sql)
    if engine == "postgres":
        conn.execute("ALTER TABLE behavior_write_inbox ADD COLUMN IF NOT EXISTS resolved_at TEXT")
        constraint = conn.execute("""SELECT pg_get_constraintdef(c.oid) AS definition
            FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=current_schema() AND t.relname='behavior_write_inbox'
              AND c.conname='behavior_write_inbox_state_check'""").fetchone()
        if constraint is not None and "quarantined" not in constraint["definition"]:
            conn.execute("ALTER TABLE behavior_write_inbox DROP CONSTRAINT behavior_write_inbox_state_check")
            conn.execute("ALTER TABLE behavior_write_inbox ADD CONSTRAINT behavior_write_inbox_state_check "
                         "CHECK(state IN ('pending', 'applied', 'quarantined', 'discarded'))")
    else:
        conn.execute("SAVEPOINT behavior_inbox_schema_upgrade")
        try:
            if "resolved_at" not in {row[1] for row in conn.execute("PRAGMA table_info(behavior_write_inbox)").fetchall()}:
                conn.execute("ALTER TABLE behavior_write_inbox ADD COLUMN resolved_at TEXT")
            definition = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='behavior_write_inbox'").fetchone()
            if definition is not None and "quarantined" not in definition[0]:
                # SQLite cannot alter a CHECK. The savepoint keeps even the
                # DDL crash-safe without committing the caller's transaction.
                conn.execute(BEHAVIOR_INBOX_TABLES["behavior_write_inbox"].replace(
                    "IF NOT EXISTS behavior_write_inbox", "behavior_write_inbox_upgrade", 1))
                columns = ", ".join(BEHAVIOR_INBOX_REQUIRED_COLUMNS["behavior_write_inbox"])
                conn.execute(f"INSERT INTO behavior_write_inbox_upgrade ({columns}) SELECT {columns} FROM behavior_write_inbox")
                conn.execute("DROP TABLE behavior_write_inbox")
                conn.execute("ALTER TABLE behavior_write_inbox_upgrade RENAME TO behavior_write_inbox")
            conn.execute("RELEASE SAVEPOINT behavior_inbox_schema_upgrade")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT behavior_inbox_schema_upgrade")
            conn.execute("RELEASE SAVEPOINT behavior_inbox_schema_upgrade")
            raise
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_inbox_pending "
                 "ON behavior_write_inbox(state, received_at, batch_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_inbox_actor "
                 "ON behavior_write_inbox(user_role, user_pk, state, received_at, batch_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_inbox_class "
                 "ON behavior_write_inbox(class_offering_id, state, received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_inbox_open_capacity "
                 "ON behavior_write_inbox(state) WHERE state IN ('pending','quarantined')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_inbox_pending_actor_order "
                 "ON behavior_write_inbox(user_role, user_pk, received_at, batch_key) WHERE state='pending'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_inbox_retired_cleanup "
                 "ON behavior_write_inbox(COALESCE(resolved_at, applied_at, received_at), batch_key) "
                 "WHERE state IN ('applied','discarded') AND SUBSTR(batch_id,1,3)='b1_'")
