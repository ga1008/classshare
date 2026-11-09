"""One explicit, durable school-sync authorization for each local round."""

SMART_CLASSROOM_AUTO_SYNC_TABLES = {
    "smart_attendance_auto_sync": """CREATE TABLE IF NOT EXISTS smart_attendance_auto_sync (
        local_round_id TEXT PRIMARY KEY, owner_teacher_id BIGINT NOT NULL,
        class_offering_id BIGINT REFERENCES class_offerings(id) ON DELETE SET NULL,
        session_id BIGINT REFERENCES class_offering_sessions(id) ON DELETE SET NULL,
        external_account_key TEXT NOT NULL, credential_generation TEXT NOT NULL,
        source_json TEXT NOT NULL, slot_json TEXT NOT NULL,
        remote_checkin_id TEXT, binding_id TEXT REFERENCES smart_attendance_write_bindings(id) ON DELETE RESTRICT,
        enabled INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'paused',
        revision INTEGER NOT NULL DEFAULT 1, request_key TEXT NOT NULL,
        intent_json TEXT NOT NULL DEFAULT '{}', message TEXT NOT NULL DEFAULT '',
        last_run_id TEXT REFERENCES smart_attendance_sync_runs(id) ON DELETE RESTRICT,
        last_context_fingerprint TEXT NOT NULL DEFAULT '', dispatch_seq INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(owner_teacher_id,request_key)
    )""",
}
SMART_CLASSROOM_AUTO_SYNC_REQUIRED_COLUMNS = {
    "smart_attendance_auto_sync": (
        "local_round_id", "owner_teacher_id", "class_offering_id", "session_id",
        "external_account_key", "credential_generation", "source_json", "slot_json",
        "remote_checkin_id", "binding_id", "enabled", "state", "revision", "request_key",
        "intent_json", "message", "last_run_id", "last_context_fingerprint", "dispatch_seq",
        "created_at", "updated_at",
    ),
}


def ensure_smart_classroom_auto_sync_schema(conn, *, engine=None):
    for ddl in SMART_CLASSROOM_AUTO_SYNC_TABLES.values():
        conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_auto_sync_enabled ON "
                 "smart_attendance_auto_sync(enabled,state,updated_at)")
