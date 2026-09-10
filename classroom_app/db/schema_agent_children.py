"""Monotone task child admissions; runner observations are not host proof."""


def ensure_agent_children_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_task_children (
        id TEXT PRIMARY KEY, task_id BIGINT NOT NULL, attempt_id TEXT NOT NULL,
        fencing_token BIGINT NOT NULL, delegation_id TEXT NOT NULL,
        actor_role TEXT NOT NULL, actor_id BIGINT NOT NULL,
        request_id TEXT NOT NULL, parent_session_id TEXT NOT NULL, child_session_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 4), depth INTEGER NOT NULL CHECK(depth=1),
        created_at BIGINT NOT NULL, runtime_reported_at BIGINT,
        runtime_reported_status TEXT CHECK(runtime_reported_status IN ('completed','aborted','error')),
        UNIQUE(task_id,request_id), UNIQUE(task_id,ordinal), UNIQUE(task_id,child_session_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_children_attempt ON agent_task_children(attempt_id)")
