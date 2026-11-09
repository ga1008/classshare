"""Observed HTTP requests use a separate ledger from transactional domain writes."""
import sqlite3


_CREATE_REQUESTS = """CREATE TABLE IF NOT EXISTS agent_platform_requests (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
        task_id BIGINT, attempt_id TEXT, fencing_token BIGINT NOT NULL,
        delegation_id TEXT, actor_role TEXT NOT NULL, actor_id BIGINT NOT NULL,
        source_session_hash TEXT, authority_fingerprint TEXT NOT NULL,
        principal_kind TEXT NOT NULL DEFAULT 'human_delegation',
        wanderer_id BIGINT, turn_id TEXT, life_epoch BIGINT, authority_version BIGINT,
        capability_key TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
        route_source_sha256 TEXT NOT NULL, request_hash TEXT NOT NULL, intent_hash TEXT NOT NULL,
        request_json TEXT NOT NULL, settlement_hash TEXT NOT NULL,
        mutates INTEGER NOT NULL DEFAULT 1 CHECK(mutates IN (0,1)),
        execution_host_id TEXT NOT NULL DEFAULT '', host_execution_finished_at BIGINT,
        status TEXT NOT NULL CHECK(status IN ('admitted','executing','observed_http_result','submitted','uncertain')),
        result_json TEXT NOT NULL DEFAULT '{}', created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
        reconciliation_status TEXT NOT NULL DEFAULT 'pending' CHECK(reconciliation_status IN ('pending','cleared')),
        reconciliation_resolution TEXT CHECK(reconciliation_resolution IN ('occurred','not_occurred')),
        reconciled_at BIGINT, reconciled_by_role TEXT, reconciled_by_id BIGINT,
        reconciled_session_hash TEXT, reconciliation_note TEXT,
        settled_at BIGINT, UNIQUE(actor_role,actor_id,operation_id),
        CONSTRAINT agent_platform_request_authority_source CHECK (
          (principal_kind='human_delegation' AND task_id IS NOT NULL AND attempt_id IS NOT NULL
            AND delegation_id IS NOT NULL AND source_session_hash IS NOT NULL
            AND wanderer_id IS NULL AND turn_id IS NULL AND life_epoch IS NULL AND authority_version IS NULL)
          OR (principal_kind='wanderer_service' AND task_id IS NULL AND attempt_id IS NULL
            AND delegation_id IS NULL AND source_session_hash IS NULL AND actor_role='student'
            AND wanderer_id IS NOT NULL AND turn_id IS NOT NULL AND life_epoch IS NOT NULL
            AND authority_version IS NOT NULL AND life_epoch>0 AND authority_version>0))
    )"""


def _upgrade_service_source(conn):
    additions = {"principal_kind": "TEXT NOT NULL DEFAULT 'human_delegation'", "wanderer_id": "BIGINT",
                 "turn_id": "TEXT", "life_epoch": "BIGINT", "authority_version": "BIGINT"}
    if isinstance(conn, sqlite3.Connection):
        columns = {row[1]: row for row in conn.execute('PRAGMA table_info("agent_platform_requests")')}
        if "principal_kind" in columns and not columns["task_id"][3]:
            return
        target = "agent_platform_requests_service_migration"
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (target,)).fetchone():
            raise RuntimeError("Unfinished platform request authority migration")
        indexes = [row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='agent_platform_requests' AND sql IS NOT NULL")]
        conn.execute(_CREATE_REQUESTS.replace("agent_platform_requests (", target + " (", 1))
        new_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{target}")')}
        if not set(columns).issubset(new_columns):
            raise RuntimeError("Unknown request receipt columns require explicit migration")
        names = ",".join('"' + name + '"' for name in columns)
        conn.execute(f"INSERT INTO {target} ({names}) SELECT {names} FROM agent_platform_requests")
        conn.execute("DROP TABLE agent_platform_requests")
        conn.execute(f"ALTER TABLE {target} RENAME TO agent_platform_requests")
        for statement in indexes:
            conn.execute(statement)
    else:
        for name, definition in additions.items():
            conn.execute(f"ALTER TABLE agent_platform_requests ADD COLUMN IF NOT EXISTS {name} {definition}")
        for name in ("task_id", "attempt_id", "delegation_id", "source_session_hash"):
            conn.execute(f"ALTER TABLE agent_platform_requests ALTER COLUMN {name} DROP NOT NULL")
        present = conn.execute("SELECT 1 FROM pg_constraint WHERE conrelid='agent_platform_requests'::regclass AND conname='agent_platform_request_authority_source'").fetchone()
        if not present:
            constraint = _CREATE_REQUESTS.split("CONSTRAINT agent_platform_request_authority_source ", 1)[1].rsplit("\n    )", 1)[0]
            conn.execute("ALTER TABLE agent_platform_requests ADD CONSTRAINT agent_platform_request_authority_source " + constraint)


def ensure_agent_platform_requests_schema(conn):
    conn.execute(_CREATE_REQUESTS)
    _upgrade_service_source(conn)
    # Unknown writes stay blocked across new tasks and login sessions. An
    # operator may clear only after actual execution has stopped; its separate
    # declaration never rewrites the HTTP observation. Pure reads can retry.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_platform_request_unresolved_write
        ON agent_platform_requests(actor_role,actor_id,intent_hash)
        WHERE mutates=1 AND reconciliation_status='pending'
          AND status IN ('admitted','executing','submitted','uncertain')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_platform_request_attempt ON agent_platform_requests(task_id,attempt_id,status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_platform_request_wanderer_turn ON agent_platform_requests(wanderer_id,turn_id,status)")
