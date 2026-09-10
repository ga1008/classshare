"""Additive Agent authority/operation schema, explicitly run at migration/startup.

No request-path DDL, process-global ready cache, implicit commit, or cascading
task deletion. UUID identifiers are TEXT; integer identities/timestamps use
BIGINT on both PostgreSQL and SQLite. Historical receipts survive task deletion.
"""
from __future__ import annotations

import sqlite3


AGENT_AUTHORITY_SCHEMA_VERSION = 2

_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS agent_task_attempts (
        id TEXT PRIMARY KEY,
        task_id BIGINT NOT NULL,
        actor_role TEXT NOT NULL CHECK (actor_role IN ('teacher', 'student')),
        actor_id BIGINT NOT NULL CHECK (actor_id > 0),
        fencing_token BIGINT NOT NULL CHECK (fencing_token > 0),
        worker_id TEXT NOT NULL,
        startup_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'canceled', 'superseded')),
        lease_expires_at BIGINT NOT NULL,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        finished_at BIGINT,
        UNIQUE (task_id, fencing_token),
        UNIQUE (task_id, startup_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_attempts_lease ON agent_task_attempts (status, lease_expires_at)",
    """CREATE TABLE IF NOT EXISTS agent_persistent_authorizations (
        id TEXT PRIMARY KEY,
        actor_role TEXT NOT NULL CHECK (actor_role IN ('teacher', 'student')),
        actor_id BIGINT NOT NULL CHECK (actor_id > 0),
        authority_fingerprint TEXT NOT NULL,
        scopes_json TEXT NOT NULL,
        intent_reference TEXT NOT NULL,
        source_session_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        revoked_at BIGINT,
        revoke_reason TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_persistent_actor ON agent_persistent_authorizations (actor_role, actor_id, status)",
    """CREATE TABLE IF NOT EXISTS agent_task_delegations (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        task_id BIGINT NOT NULL,
        attempt_id TEXT NOT NULL,
        fencing_token BIGINT NOT NULL,
        actor_role TEXT NOT NULL CHECK (actor_role IN ('teacher', 'student')),
        actor_id BIGINT NOT NULL CHECK (actor_id > 0),
        authority_fingerprint TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK (purpose IN ('tools', 'model')),
        scopes_json TEXT NOT NULL,
        source_session_hash TEXT,
        persistent_authorization_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        revoked_at BIGINT,
        revoke_reason TEXT NOT NULL DEFAULT '',
        CHECK ((source_session_hash IS NOT NULL AND persistent_authorization_id IS NULL)
            OR (source_session_hash IS NULL AND persistent_authorization_id IS NOT NULL))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_delegations_task ON agent_task_delegations (task_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_agent_delegations_authorization ON agent_task_delegations (persistent_authorization_id, status)",
    """CREATE TABLE IF NOT EXISTS agent_action_executions (
        id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL,
        actor_role TEXT NOT NULL CHECK (actor_role IN ('teacher', 'student')),
        actor_id BIGINT NOT NULL CHECK (actor_id > 0),
        task_id BIGINT NOT NULL,
        attempt_id TEXT,
        fencing_token BIGINT,
        delegation_id TEXT,
        source_kind TEXT NOT NULL DEFAULT 'delegation' CHECK (source_kind IN ('delegation', 'user_confirmation')),
        source_session_hash TEXT,
        authority_fingerprint TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        params_hash TEXT NOT NULL,
        resource_revision TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN ('executing', 'completed', 'failed')),
        result_json TEXT NOT NULL DEFAULT '{}',
        error_code TEXT NOT NULL DEFAULT '',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        completed_at BIGINT,
        UNIQUE (actor_role, actor_id, operation_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_operations_task ON agent_action_executions (task_id, created_at)",
)


def ensure_agent_authority_schema(conn) -> None:
    """Call once per migration/startup transaction; caller commits or rolls back."""
    for statement in _STATEMENTS:
        conn.execute(statement)
    _upgrade_operation_sources(conn)


def _upgrade_operation_sources(conn) -> None:
    """Preserve v1 receipts while allowing a distinct fresh-user source.

    This runs inside the startup/migration transaction. SQLite needs a bounded
    table rebuild to drop NOT NULL on the three runner-only columns. The table
    has no foreign-key dependents; its actor/operation uniqueness is restored by
    the new table definition before any request is served.
    """
    additions = {
        "source_kind": "TEXT NOT NULL DEFAULT 'delegation'",
        "source_session_hash": "TEXT",
        "authority_fingerprint": "TEXT NOT NULL DEFAULT ''",
    }
    runner_columns = ("attempt_id", "fencing_token", "delegation_id")
    if isinstance(conn, sqlite3.Connection):
        columns = {row[1]: row for row in conn.execute('PRAGMA table_info("agent_action_executions")').fetchall()}
        if any(columns[name][3] for name in runner_columns):
            create = next(statement for statement in _STATEMENTS if statement.startswith("CREATE TABLE IF NOT EXISTS agent_action_executions"))
            target = "agent_action_executions_source_migration"
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (target,)).fetchone():
                raise RuntimeError("An unfinished Agent operation schema migration needs review")
            conn.execute(create.replace("agent_action_executions", target, 1))
            target_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{target}")').fetchall()}
            if not set(columns).issubset(target_columns):
                raise RuntimeError("Unknown Agent receipt columns must be preserved by an explicit migration")
            names = ", ".join(f'"{name}"' for name in columns)
            conn.execute(f"INSERT INTO {target} ({names}) SELECT {names} FROM agent_action_executions")
            conn.execute("DROP TABLE agent_action_executions")
            conn.execute(f"ALTER TABLE {target} RENAME TO agent_action_executions")
            conn.execute("CREATE INDEX idx_agent_operations_task ON agent_action_executions (task_id, created_at)")
        else:
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE agent_action_executions ADD COLUMN {name} {definition}")
    else:
        for name, definition in additions.items():
            conn.execute(f"ALTER TABLE agent_action_executions ADD COLUMN IF NOT EXISTS {name} {definition}")
        for name in runner_columns:
            conn.execute(f"ALTER TABLE agent_action_executions ALTER COLUMN {name} DROP NOT NULL")
