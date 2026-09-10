"""Observed HTTP requests use a separate ledger from transactional domain writes."""


def ensure_agent_platform_requests_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_platform_requests (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
        task_id BIGINT NOT NULL, attempt_id TEXT NOT NULL, fencing_token BIGINT NOT NULL,
        delegation_id TEXT NOT NULL, actor_role TEXT NOT NULL, actor_id BIGINT NOT NULL,
        source_session_hash TEXT NOT NULL, authority_fingerprint TEXT NOT NULL,
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
        settled_at BIGINT, UNIQUE(actor_role,actor_id,operation_id)
    )""")
    # Unknown writes stay blocked across new tasks and login sessions. An
    # operator may clear only after actual execution has stopped; its separate
    # declaration never rewrites the HTTP observation. Pure reads can retry.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_platform_request_unresolved_write
        ON agent_platform_requests(actor_role,actor_id,intent_hash)
        WHERE mutates=1 AND reconciliation_status='pending'
          AND status IN ('admitted','executing','submitted','uncertain')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_platform_request_attempt ON agent_platform_requests(task_id,attempt_id,status)")
