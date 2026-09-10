"""Durable questions scoped to one actor, task and execution attempt."""


def ensure_agent_interactions_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_task_questions (
        id TEXT PRIMARY KEY, task_id BIGINT NOT NULL, attempt_id TEXT NOT NULL,
        fencing_token BIGINT NOT NULL, delegation_id TEXT NOT NULL,
        actor_role TEXT NOT NULL, actor_id BIGINT NOT NULL, request_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL, questions_json TEXT NOT NULL, answers_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL CHECK(status IN ('pending','answered','canceled','expired')),
        created_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, answered_at BIGINT,
        UNIQUE(attempt_id, request_key)
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_questions_pending ON agent_task_questions(task_id) WHERE status='pending'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_questions_actor ON agent_task_questions(actor_role,actor_id,task_id)")
