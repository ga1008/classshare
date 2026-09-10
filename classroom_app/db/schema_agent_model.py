"""Model gateway receipts. Schema installation only; no request-time DDL."""

import sqlite3


def ensure_agent_key_configuration_schema(conn) -> None:
    """Serialize key changes and enforce one selected key per provider.

    A model-only test fixture may not have installed the key table yet. In that
    case the lock is still created, and the key-specific migration waits until
    the normal startup call after the base schema has installed that table.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS agent_model_configuration_lock (id INTEGER PRIMARY KEY CHECK (id = 1), revision BIGINT NOT NULL)")
    conn.execute("INSERT INTO agent_model_configuration_lock (id, revision) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
    conn.execute("UPDATE agent_model_configuration_lock SET revision = revision WHERE id = 1")
    if isinstance(conn, sqlite3.Connection):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_runtime_api_keys'").fetchone()
        columns = {row[1] for row in conn.execute('PRAGMA table_info("agent_runtime_api_keys")').fetchall()} if exists else set()
    else:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'agent_runtime_api_keys'").fetchall()
        columns = {row["column_name"] if hasattr(row, "keys") else row[0] for row in rows}
    if not columns:
        return
    required = {"id", "provider", "is_active", "enabled", "updated_at"}
    if not required.issubset(columns):
        raise ValueError("Agent key configuration requires the full base key schema")
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE agent_runtime_api_keys ADD COLUMN deleted_at TEXT")
    # Preserve every historical key/check row; only repair the selected flag.
    conn.execute("""
        UPDATE agent_runtime_api_keys SET is_active = 0
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY provider ORDER BY enabled DESC, updated_at DESC, id DESC
                ) AS selected_rank
                FROM agent_runtime_api_keys WHERE is_active = 1
            ) ranked WHERE selected_rank > 1
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_keys_one_selected_provider ON agent_runtime_api_keys (provider) WHERE is_active = 1")


def ensure_agent_model_schema(conn) -> None:
    ensure_agent_key_configuration_schema(conn)
    # Text UUID keys work on both supported engines. No cascades: audit survives
    # user-facing task/history deletion. Prompts and credentials are never stored.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_model_requests (
            id TEXT PRIMARY KEY,
            task_id BIGINT NOT NULL,
            attempt_id TEXT NOT NULL,
            fencing_token BIGINT NOT NULL,
            actor_role TEXT NOT NULL,
            actor_id BIGINT NOT NULL,
            key_id BIGINT NOT NULL,
            config_generation TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            output_token_limit INTEGER NOT NULL,
            input_tokens BIGINT,
            output_tokens BIGINT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            usage_source TEXT,
            upstream_status INTEGER,
            expires_at BIGINT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_model_requests_task ON agent_model_requests (task_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_model_requests_time ON agent_model_requests (created_at, model)")
