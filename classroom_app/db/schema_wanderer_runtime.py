"""Additive durable decision/receipt and autobiographical provenance schema.

PostgreSQL DDL belongs to startup. Business calls only inspect the catalog;
SQLite fixtures retain transactional lazy initialization. No caller is committed.
"""
from __future__ import annotations

from typing import Any

from .connection import get_configured_db_engine


TABLES = {
    "wanderer_wake_events": """event_id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        source TEXT NOT NULL, event_key TEXT NOT NULL, context_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending', turn_id TEXT, chain_id TEXT NOT NULL,
        parent_turn_id TEXT, depth INTEGER NOT NULL DEFAULT 0, run_at TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(wanderer_id,source,event_key)""",
    "wanderer_turns": """turn_id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        event_id TEXT NOT NULL, chain_id TEXT NOT NULL, parent_turn_id TEXT,
        depth INTEGER NOT NULL DEFAULT 0, life_epoch INTEGER NOT NULL,
        authority_version INTEGER NOT NULL, fencing_token INTEGER NOT NULL,
        state TEXT NOT NULL, lease_until TEXT NOT NULL, dispatched_at TEXT,
        prompt_version TEXT NOT NULL, prompt_json TEXT NOT NULL DEFAULT '{}',
        output_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
        scope_json TEXT NOT NULL DEFAULT '{}', budget_operation_id TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT""",
    "wanderer_runtime_leases": """wanderer_id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL DEFAULT 1, lease_until TEXT NOT NULL""",
    "wanderer_commands": """operation_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL,
        command_id TEXT NOT NULL, capability TEXT NOT NULL, args_hash TEXT NOT NULL,
        command_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        receipt_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, UNIQUE(turn_id,command_id)""",
    "wanderer_memory_sources": """memory_id INTEGER PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        command_key TEXT NOT NULL UNIQUE, author_kind TEXT NOT NULL DEFAULT 'self',
        origin_author INTEGER NOT NULL, turn_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        visibility TEXT NOT NULL DEFAULT 'private', scope_json TEXT NOT NULL DEFAULT '{}',
        evidence_json TEXT NOT NULL DEFAULT '[]', supersedes_id INTEGER,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "wanderer_world_chapters": """chapter_key TEXT NOT NULL, version TEXT NOT NULL,
        title TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
        effective_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        PRIMARY KEY(chapter_key,version)""",
    "wanderer_knowledge_sources": """entry_id INTEGER PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        turn_id TEXT NOT NULL, scope_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "wanderer_admin_access": """id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL, section TEXT NOT NULL, selector TEXT NOT NULL,
        created_at TEXT NOT NULL""",
    "wanderer_todo_evidence": """todo_id INTEGER NOT NULL, wanderer_id INTEGER NOT NULL,
        operation_id TEXT NOT NULL, turn_id TEXT NOT NULL, evidence_json TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(todo_id,operation_id)""",
    "wanderer_self_state_sources": """wanderer_id INTEGER PRIMARY KEY, scope_json TEXT NOT NULL,
        turn_id TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "wanderer_knowledge_revisions": """entry_id INTEGER NOT NULL, revision INTEGER NOT NULL,
        wanderer_id INTEGER NOT NULL, turn_id TEXT NOT NULL, record_json TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(entry_id,revision)""",
    "wanderer_evidence_snapshots": """id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        evidence_json TEXT NOT NULL, created_at TEXT NOT NULL""",
    "wanderer_memory_corrections": """id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        target_kind TEXT NOT NULL CHECK(target_kind IN ('episode','knowledge')),
        target_id INTEGER NOT NULL, revision INTEGER NOT NULL, actor_id INTEGER NOT NULL,
        actor_kind TEXT NOT NULL, reason TEXT NOT NULL, request_hash TEXT NOT NULL,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(wanderer_id,target_kind,target_id,revision)""",
}
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_wanderer_wake_ready ON wanderer_wake_events(state,run_at,wanderer_id)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_turn_history ON wanderer_turns(wanderer_id,created_at,turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_command_turn ON wanderer_commands(turn_id,state)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_memory_visibility ON wanderer_memory_sources(wanderer_id,visibility,memory_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wanderer_memory_revision ON wanderer_memory_sources(supersedes_id) WHERE supersedes_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_knowledge_source_owner ON wanderer_knowledge_sources(wanderer_id,entry_id)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_admin_access_owner ON wanderer_admin_access(wanderer_id,created_at)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_episode_chronology ON wanderer_episodic_memories(wanderer_id,occurred_at,id)",
)


def ensure_wanderer_runtime_schema(conn: Any, *, engine: str | None = None,
                                   allow_schema_changes: bool = False) -> None:
    engine = engine or get_configured_db_engine()
    names = tuple(TABLES)
    marks = ",".join("?" for _ in names)
    if engine == "postgres":
        rows = conn.execute("SELECT tablename AS name FROM pg_tables WHERE schemaname=current_schema() "
                            f"AND tablename IN ({marks})", names).fetchall()
    else:
        rows = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({marks})", names).fetchall()
    present = {str(row["name"]) for row in rows}
    if present == set(names) and not allow_schema_changes:
        return
    if engine == "postgres":
        if not allow_schema_changes:
            raise RuntimeError("散修运行账本需要启动迁移")
        conn.execute("SELECT pg_advisory_xact_lock(?)", (742819036124,))
    elif not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    for name, body in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({body})")
    for statement in INDEXES:
        conn.execute(statement)
