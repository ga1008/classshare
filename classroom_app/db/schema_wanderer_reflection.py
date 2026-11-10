"""Additive daily reflection entitlements and deterministic affinity history."""
from __future__ import annotations

from .connection import get_configured_db_engine

TABLES = {
    "wanderer_reflection_sessions": """wanderer_id INTEGER NOT NULL, reflection_date TEXT NOT NULL,
        root_event_id TEXT NOT NULL UNIQUE, chain_id TEXT NOT NULL UNIQUE,
        life_epoch INTEGER NOT NULL, authority_version INTEGER NOT NULL,
        ai_calls INTEGER NOT NULL DEFAULT 0 CHECK(ai_calls BETWEEN 0 AND 3),
        status TEXT NOT NULL DEFAULT 'pending', roster_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT,
        PRIMARY KEY(wanderer_id,reflection_date)""",
    "wanderer_reflection_dispatches": """turn_id TEXT PRIMARY KEY, wanderer_id INTEGER NOT NULL,
        reflection_date TEXT NOT NULL, event_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 3), dispatched_at TEXT NOT NULL,
        UNIQUE(wanderer_id,reflection_date,ordinal)""",
    "wanderer_affinity_ratings": """wanderer_id INTEGER NOT NULL, reflection_date TEXT NOT NULL,
        peer_identity TEXT NOT NULL, grade TEXT NOT NULL,
        baseline_affinity REAL NOT NULL, new_affinity REAL NOT NULL,
        revision INTEGER NOT NULL, rule_version TEXT NOT NULL,
        source_turn_id TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(wanderer_id,reflection_date,peer_identity)""",
    "wanderer_affinity_rating_operations": """operation_id TEXT PRIMARY KEY,
        wanderer_id INTEGER NOT NULL, reflection_date TEXT NOT NULL, peer_identity TEXT NOT NULL,
        request_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL""",
}
INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_wanderer_rating_peer ON wanderer_affinity_ratings(wanderer_id,peer_identity,reflection_date)',
    'CREATE INDEX IF NOT EXISTS idx_wanderer_reflection_dispatch_owner ON wanderer_reflection_dispatches(wanderer_id,reflection_date)',
)


def ensure_wanderer_reflection_schema(conn, *, engine=None, allow_schema_changes=False):
    engine = engine or get_configured_db_engine()
    if engine == 'postgres' and not allow_schema_changes:
        for name in TABLES:
            conn.execute(f'SELECT 1 FROM {name} WHERE 1=0')
        return
    if engine == 'postgres':
        conn.execute('SELECT pg_advisory_xact_lock(?)', (742819036125,))
    elif not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    for name, body in TABLES.items():
        conn.execute(f'CREATE TABLE IF NOT EXISTS {name} ({body})')
    for sql in INDEXES:
        conn.execute(sql)
