"""Durable collection evidence, separate from classroom actions and presence."""
from __future__ import annotations


BEHAVIOR_COLLECTION_HEALTH_TABLES = {
    "behavior_collection_observations": """CREATE TABLE IF NOT EXISTS behavior_collection_observations (
        observation_key TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        class_offering_id INTEGER NOT NULL,
        user_pk INTEGER NOT NULL,
        user_role TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('server', 'browser', 'client_diagnostic')),
        received_at TEXT NOT NULL,
        observed_start TEXT,
        observed_end TEXT,
        valid_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(valid_sample_count >= 0),
        rejected_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(rejected_sample_count >= 0),
        learning_delta_seconds INTEGER NOT NULL DEFAULT 0 CHECK(learning_delta_seconds >= 0),
        reason_codes_json TEXT NOT NULL DEFAULT '[]',
        client_diagnostic_count INTEGER NOT NULL DEFAULT 0 CHECK(client_diagnostic_count >= 0),
        diagnostics_json TEXT NOT NULL DEFAULT '{}',
        ip_address TEXT,
        CHECK((source = 'client_diagnostic' AND valid_sample_count = 0 AND rejected_sample_count = 0
               AND learning_delta_seconds = 0 AND observed_start IS NULL AND observed_end IS NULL
               AND client_diagnostic_count = 1)
              OR (source <> 'client_diagnostic' AND client_diagnostic_count = 0)),
        CHECK((observed_start IS NULL AND observed_end IS NULL)
              OR (observed_start IS NOT NULL AND observed_end IS NOT NULL AND observed_start <= observed_end)),
        UNIQUE(user_role, user_pk, class_offering_id, batch_id)
    )""",
}

BEHAVIOR_COLLECTION_HEALTH_REQUIRED_COLUMNS = {
    "behavior_collection_observations": (
        "observation_key", "batch_id", "class_offering_id", "user_pk", "user_role", "source",
        "received_at", "observed_start", "observed_end", "valid_sample_count", "rejected_sample_count",
        "learning_delta_seconds", "reason_codes_json", "client_diagnostic_count", "diagnostics_json", "ip_address",
    ),
}


def ensure_behavior_collection_health_schema(conn, *, engine=None) -> None:
    """Startup-only DDL; no request path may create or repair this schema."""
    for sql in BEHAVIOR_COLLECTION_HEALTH_TABLES.values():
        conn.execute(sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_collection_class_received "
                 "ON behavior_collection_observations(class_offering_id, received_at, user_role, user_pk)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_collection_actor_received "
                 "ON behavior_collection_observations(user_role, user_pk, class_offering_id, received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_behavior_collection_class_observed "
                 "ON behavior_collection_observations(class_offering_id, observed_end, observed_start)")
