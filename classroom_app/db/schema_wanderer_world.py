"""Owned migrations for durable wanderer relationships, resources and sanctions.

PostgreSQL schema changes belong to startup. SQLite fixtures may initialize
transactionally; no helper commits its caller or caches uncommitted readiness.
"""
from __future__ import annotations

from typing import Any
from .connection import get_configured_db_engine

VERSION = "5"
AGENT_COLUMNS = {
    "life_epoch": "INTEGER NOT NULL DEFAULT 1",
    "authority_version": "INTEGER NOT NULL DEFAULT 1",
    "daily_wake_limit": "INTEGER NOT NULL DEFAULT 100",
    "life_bonus_seconds": "BIGINT NOT NULL DEFAULT 0",
}

TABLES = (
    """CREATE TABLE IF NOT EXISTS wanderer_knowledge_growth (
        entry_id BIGINT PRIMARY KEY,wanderer_id BIGINT NOT NULL,
        rewarded_chars BIGINT NOT NULL CHECK(rewarded_chars>=0),updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wanderer_gain_debt_payments (
        gain_key TEXT NOT NULL,owner_id BIGINT NOT NULL,component TEXT NOT NULL,
        gain_units BIGINT NOT NULL,paid_units BIGINT NOT NULL,life_epoch INTEGER NOT NULL,
        allocations_json TEXT NOT NULL,created_at TEXT NOT NULL,
        PRIMARY KEY(gain_key,owner_id,component))""",
    """CREATE TABLE IF NOT EXISTS wanderer_talent_awards (
        event_id TEXT NOT NULL,owner_id BIGINT NOT NULL,component TEXT NOT NULL,
        gain_units BIGINT NOT NULL,result_json TEXT NOT NULL,life_epoch INTEGER NOT NULL,
        created_at TEXT NOT NULL,PRIMARY KEY(event_id,owner_id))""",
    """CREATE TABLE IF NOT EXISTS wanderer_penalty_reversals (
        root_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,reason TEXT NOT NULL,
        result_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wanderer_capacity_allocations (
        actor_id BIGINT NOT NULL,resource TEXT NOT NULL,object_key TEXT NOT NULL,
        amount_units BIGINT NOT NULL,lines_json TEXT NOT NULL,updated_at TEXT NOT NULL,
        PRIMARY KEY(actor_id,resource,object_key))""",
    """CREATE TABLE IF NOT EXISTS wanderer_world_schema (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wanderer_bonds (
        id TEXT PRIMARY KEY, pair_key TEXT NOT NULL UNIQUE,
        first_id BIGINT NOT NULL, second_id BIGINT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('active','ended','dormant')),
        version INTEGER NOT NULL DEFAULT 1, reward_granted INTEGER NOT NULL DEFAULT 0,
        first_request_id TEXT NOT NULL, last_request_id TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK(first_id < second_id))""",
    """CREATE TABLE IF NOT EXISTS wanderer_bond_requests (
        id TEXT PRIMARY KEY, pair_key TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('bond','equalize')),
        proposer_id BIGINT NOT NULL, first_id BIGINT NOT NULL, second_id BIGINT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('awaiting_consent','ready','reviewing','approved','rejected','withdrawn','stale')),
        version INTEGER NOT NULL DEFAULT 1, rule_version TEXT NOT NULL,
        first_epoch INTEGER NOT NULL, second_epoch INTEGER NOT NULL,
        first_consent INTEGER NOT NULL DEFAULT 0, second_consent INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '', decision_reason TEXT NOT NULL DEFAULT '',
        result_json TEXT NOT NULL DEFAULT '{}', review_token TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK(first_id < second_id))""",
    """CREATE TABLE IF NOT EXISTS wanderer_resource_usage (
        owner_id BIGINT NOT NULL, resource TEXT NOT NULL, budget_date TEXT NOT NULL,
        used_units BIGINT NOT NULL DEFAULT 0, reserved_units BIGINT NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY(owner_id,resource,budget_date),
        CHECK(used_units>=0 AND reserved_units>=0))""",
    """CREATE TABLE IF NOT EXISTS wanderer_resource_reservations (
        operation_id TEXT PRIMARY KEY, actor_id BIGINT NOT NULL, resource TEXT NOT NULL,
        budget_date TEXT NOT NULL, amount_units BIGINT NOT NULL, request_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('reserved','consumed','released')),
        lines_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wanderer_life_ledger (
        source_key TEXT NOT NULL, owner_id BIGINT NOT NULL, delta_seconds BIGINT NOT NULL,
        kind TEXT NOT NULL, life_epoch INTEGER NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, PRIMARY KEY(source_key,owner_id))""",
    """CREATE TABLE IF NOT EXISTS wanderer_penalty_roots (
        id TEXT PRIMARY KEY, offender_id BIGINT NOT NULL, request_hash TEXT NOT NULL,
        reason TEXT NOT NULL, components_json TEXT NOT NULL, partners_json TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wanderer_penalty_allocations (
        root_id TEXT NOT NULL, beneficiary_id BIGINT NOT NULL, component TEXT NOT NULL,
        amount_units BIGINT NOT NULL, applied_units BIGINT NOT NULL DEFAULT 0,
        detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
        PRIMARY KEY(root_id,beneficiary_id,component))""",
    """CREATE TABLE IF NOT EXISTS wanderer_penalty_debts (
        wanderer_id BIGINT NOT NULL, component TEXT NOT NULL,
        balance_units BIGINT NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        PRIMARY KEY(wanderer_id,component), CHECK(balance_units>=0))""",
    """CREATE TABLE IF NOT EXISTS wanderer_cultivation_awards (
        event_id TEXT NOT NULL, wanderer_id BIGINT NOT NULL, kind TEXT NOT NULL,
        base_units BIGINT NOT NULL, awarded_units BIGINT NOT NULL, debt_paid_units BIGINT NOT NULL DEFAULT 0, bond_multiplier TEXT NOT NULL,
        budget_date TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(event_id,wanderer_id))""",
    """CREATE TABLE IF NOT EXISTS wanderer_dao_history_blocks (
        request_id TEXT NOT NULL, block_id TEXT NOT NULL, content_hash TEXT NOT NULL,
        content_json TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','reviewed')),
        created_at TEXT NOT NULL, reviewed_at TEXT,
        PRIMARY KEY(request_id,block_id))""",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_bond_first ON wanderer_bonds(first_id,state)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_bond_second ON wanderer_bonds(second_id,state)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_bond_request_state ON wanderer_bond_requests(state,created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wanderer_pending_bond_pair ON wanderer_bond_requests(pair_key,kind) WHERE state IN ('awaiting_consent','ready','reviewing')",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_penalty_owner ON wanderer_penalty_allocations(beneficiary_id,component)",
    "CREATE INDEX IF NOT EXISTS idx_wanderer_cultivation_day ON wanderer_cultivation_awards(wanderer_id,kind,budget_date)",
)


def ensure_wanderer_world_schema(conn: Any, *, allow_schema_changes: bool = False, engine: str | None = None) -> None:
    engine = engine or get_configured_db_engine()
    if engine == "postgres":
        present = conn.execute("SELECT to_regclass('wanderer_world_schema') AS name").fetchone()
        exists = bool(present and present["name"])
    else:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='wanderer_world_schema'").fetchone() is not None
    if exists:
        row = conn.execute("SELECT value FROM wanderer_world_schema WHERE key='version'").fetchone()
        if row and str(row["value"]) == VERSION:
            return
    if engine == "postgres" and not allow_schema_changes:
        raise RuntimeError("wanderer world schema requires a startup migration")
    if engine == "postgres":
        conn.execute("SELECT pg_advisory_xact_lock(?)", (742819036121,))
        columns = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='wanderer_agents'").fetchall()}
    else:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(wanderer_agents)").fetchall()}
    if not columns:
        raise RuntimeError("wanderer schema must be initialized before world schema")
    for name, definition in AGENT_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE wanderer_agents ADD COLUMN {name} {definition}")
    for statement in TABLES:
        conn.execute(statement)
    if engine == 'postgres':
        award_columns={r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='wanderer_cultivation_awards'").fetchall()}
    else:
        award_columns={r['name'] for r in conn.execute('PRAGMA table_info(wanderer_cultivation_awards)').fetchall()}
    if 'debt_paid_units' not in award_columns:
        conn.execute('ALTER TABLE wanderer_cultivation_awards ADD COLUMN debt_paid_units BIGINT NOT NULL DEFAULT 0')
    conn.execute("INSERT INTO wanderer_world_schema(key,value) VALUES('version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (VERSION,))
