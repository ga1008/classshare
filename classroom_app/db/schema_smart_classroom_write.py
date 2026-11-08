"""Startup-only durable school attendance handoff and verification ledger.

No guessed write protocol is enabled by creating these tables. Domain records
and original imported attendance remain independent and are never overwritten.
"""

SMART_CLASSROOM_WRITE_TABLES = {
    "smart_attendance_write_bindings": """CREATE TABLE IF NOT EXISTS smart_attendance_write_bindings (
        id TEXT PRIMARY KEY, owner_teacher_id BIGINT NOT NULL,
        class_offering_id BIGINT REFERENCES class_offerings(id) ON DELETE SET NULL,
        session_id BIGINT REFERENCES class_offering_sessions(id) ON DELETE SET NULL,
        local_round_id TEXT NOT NULL, external_account_key TEXT NOT NULL,
        credential_id BIGINT NOT NULL, credential_generation TEXT NOT NULL,
        source_json TEXT NOT NULL, remote_checkin_id TEXT NOT NULL,
        remote_snapshot_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active'
          CHECK(state IN ('active','paused','released')),
        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    "smart_attendance_sync_runs": """CREATE TABLE IF NOT EXISTS smart_attendance_sync_runs (
        id TEXT PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES smart_attendance_write_bindings(id) ON DELETE RESTRICT,
        owner_teacher_id BIGINT NOT NULL, local_round_id TEXT NOT NULL,
        local_revision INTEGER NOT NULL, context_fingerprint TEXT NOT NULL,
        credential_generation TEXT NOT NULL, binding_revision INTEGER NOT NULL,
        target_json TEXT NOT NULL, baseline_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('preview','pending','needs_teacher_action','confirmed','conflict',
          'outcome_unknown','needs_teacher_reauth','paused','superseded','failed')),
        request_key TEXT, revision INTEGER NOT NULL DEFAULT 1, message TEXT NOT NULL DEFAULT '',
        verified_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(owner_teacher_id,request_key)
    )""",
    "smart_attendance_sync_operations": """CREATE TABLE IF NOT EXISTS smart_attendance_sync_operations (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES smart_attendance_sync_runs(id) ON DELETE RESTRICT,
        student_id BIGINT NOT NULL, student_number TEXT NOT NULL,
        operation_kind TEXT NOT NULL DEFAULT 'student_status', target_revision INTEGER NOT NULL,
        baseline_status TEXT, target_status TEXT, remote_status TEXT,
        state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
        UNIQUE(run_id,student_id,operation_kind,target_revision)
    )""",
    "smart_attendance_account_leases": """CREATE TABLE IF NOT EXISTS smart_attendance_account_leases (
        external_account_key TEXT PRIMARY KEY, lease_token TEXT NOT NULL, lease_until TEXT NOT NULL,
        generation BIGINT NOT NULL DEFAULT 1
    )""",
    "smart_attendance_sync_events": """CREATE TABLE IF NOT EXISTS smart_attendance_sync_events (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES smart_attendance_sync_runs(id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL, actor_teacher_id BIGINT, detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""",
}

SMART_CLASSROOM_WRITE_REQUIRED_COLUMNS = {
    "smart_attendance_write_bindings": ("id", "owner_teacher_id", "class_offering_id", "session_id", "local_round_id",
        "external_account_key", "credential_id", "credential_generation", "source_json", "remote_checkin_id",
        "remote_snapshot_json", "state", "revision", "created_at", "updated_at"),
    "smart_attendance_sync_runs": ("id", "binding_id", "owner_teacher_id", "local_round_id", "local_revision",
        "context_fingerprint", "credential_generation", "binding_revision", "target_json", "baseline_json", "state",
        "request_key", "revision", "message", "verified_at", "created_at", "updated_at"),
    "smart_attendance_sync_operations": ("id", "run_id", "student_id", "student_number", "operation_kind",
        "target_revision", "baseline_status", "target_status", "remote_status", "state", "reason", "updated_at"),
    "smart_attendance_account_leases": ("external_account_key", "lease_token", "lease_until", "generation"),
    "smart_attendance_sync_events": ("id", "run_id", "event_type", "actor_teacher_id", "detail_json", "created_at"),
}


def ensure_smart_classroom_write_schema(conn, *, engine=None):
    for ddl in SMART_CLASSROOM_WRITE_TABLES.values():
        conn.execute(ddl)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_smart_attendance_write_owner ON "
                 "smart_attendance_write_bindings(external_account_key,remote_checkin_id) WHERE state<>'released'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_attendance_write_round ON "
                 "smart_attendance_write_bindings(owner_teacher_id,class_offering_id,local_round_id,created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_attendance_runs_round ON "
                 "smart_attendance_sync_runs(owner_teacher_id,local_round_id,created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_attendance_operations_run ON "
                 "smart_attendance_sync_operations(run_id,student_id)")
