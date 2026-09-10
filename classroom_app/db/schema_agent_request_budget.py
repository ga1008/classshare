"""Persist Agent request budgets during startup/migration, never per request."""
from __future__ import annotations


def ensure_agent_request_budget_schema(conn) -> None:
    # No foreign keys to tasks: deleting user-facing history cannot reset a
    # running reservation or remove evidence used to protect shared capacity.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_request_buckets (
            scope_key TEXT NOT NULL,
            channel TEXT NOT NULL CHECK (channel IN ('tools', 'web', 'model')),
            tokens DOUBLE PRECISION NOT NULL,
            updated_at_ms BIGINT NOT NULL,
            used_count BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (scope_key, channel)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_request_budget_leases (
            id TEXT PRIMARY KEY,
            channel TEXT NOT NULL CHECK (channel IN ('tools', 'web', 'model')),
            task_id BIGINT NOT NULL,
            actor_role TEXT NOT NULL CHECK (actor_role IN ('teacher', 'student')),
            actor_id BIGINT NOT NULL,
            attempt_id TEXT NOT NULL,
            fencing_token BIGINT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'failed', 'canceled')),
            created_at_ms BIGINT NOT NULL,
            expires_at_ms BIGINT NOT NULL,
            finished_at_ms BIGINT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_budget_active ON agent_request_budget_leases (channel, status, expires_at_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_budget_actor ON agent_request_budget_leases (actor_role, actor_id, channel, created_at_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_budget_task ON agent_request_budget_leases (task_id, channel, created_at_ms)")
