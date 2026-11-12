"""One explicit, durable school-sync authorization for each local round."""

SMART_CLASSROOM_AUTO_SYNC_TABLES = {
    "smart_attendance_student_corrections": """CREATE TABLE IF NOT EXISTS smart_attendance_student_corrections (
        id TEXT PRIMARY KEY, owner_teacher_id BIGINT NOT NULL,
        request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        class_offering_id BIGINT REFERENCES class_offerings(id) ON DELETE SET NULL,
        session_id BIGINT REFERENCES class_offering_sessions(id) ON DELETE SET NULL,
        origin_scope_json TEXT NOT NULL, local_round_id TEXT NOT NULL,
        student_id BIGINT NOT NULL, student_number TEXT NOT NULL,
        local_revision INTEGER NOT NULL, target_status TEXT NOT NULL,
        mode TEXT NOT NULL, state TEXT NOT NULL,
        source_json TEXT NOT NULL DEFAULT '{}', slot_json TEXT NOT NULL DEFAULT '{}',
        binding_revision INTEGER, external_account_key TEXT, credential_generation TEXT,
        remote_checkin_id TEXT, run_id TEXT REFERENCES smart_attendance_sync_runs(id) ON DELETE RESTRICT,
        baseline_status TEXT, code TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
        dispatch_seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(owner_teacher_id,request_key)
    )""",
    "smart_attendance_course_links": """CREATE TABLE IF NOT EXISTS smart_attendance_course_links (
        class_offering_id BIGINT PRIMARY KEY REFERENCES class_offerings(id) ON DELETE CASCADE,
        owner_teacher_id BIGINT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
        source_json TEXT NOT NULL DEFAULT '{}', alignment_json TEXT NOT NULL DEFAULT '{}',
        message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
    )""",
    "smart_attendance_alignment_requests": """CREATE TABLE IF NOT EXISTS smart_attendance_alignment_requests (
        owner_teacher_id BIGINT NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        class_offering_id BIGINT NOT NULL, session_id BIGINT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(owner_teacher_id,request_key)
    )""",
    "smart_attendance_workflow_requests": """CREATE TABLE IF NOT EXISTS smart_attendance_workflow_requests (
        owner_teacher_id BIGINT NOT NULL, request_key TEXT NOT NULL, local_round_id TEXT NOT NULL,
        context_fingerprint TEXT NOT NULL, binding_revision INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(owner_teacher_id,request_key)
    )""",
    "smart_attendance_session_links": """CREATE TABLE IF NOT EXISTS smart_attendance_session_links (
        session_id BIGINT PRIMARY KEY REFERENCES class_offering_sessions(id) ON DELETE CASCADE,
        class_offering_id BIGINT NOT NULL REFERENCES class_offerings(id) ON DELETE CASCADE,
        owner_teacher_id BIGINT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
        source_json TEXT NOT NULL DEFAULT '{}', slot_json TEXT NOT NULL DEFAULT '{}',
        diagnostics_json TEXT NOT NULL DEFAULT '[]', message TEXT NOT NULL DEFAULT '',
        alignment_json TEXT NOT NULL DEFAULT '{}', course_link_revision INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""",
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
    "smart_attendance_student_corrections": tuple("id owner_teacher_id request_key request_hash class_offering_id session_id origin_scope_json local_round_id student_id student_number local_revision target_status mode state source_json slot_json binding_revision external_account_key credential_generation remote_checkin_id run_id baseline_status code message dispatch_seq created_at updated_at".split()),
    "smart_attendance_course_links": tuple("class_offering_id owner_teacher_id revision state context_fingerprint source_json alignment_json message updated_at".split()),
    "smart_attendance_alignment_requests": tuple("owner_teacher_id request_key request_hash class_offering_id session_id created_at".split()),
    "smart_attendance_workflow_requests": tuple("owner_teacher_id request_key local_round_id context_fingerprint binding_revision created_at".split()),
    "smart_attendance_session_links": tuple("session_id class_offering_id owner_teacher_id revision state context_fingerprint source_json slot_json diagnostics_json message updated_at alignment_json course_link_revision".split()),
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
    if engine == 'postgres':
        columns = {r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='smart_attendance_session_links'").fetchall()}
    else:
        columns = {r[1] for r in conn.execute('PRAGMA table_info(smart_attendance_session_links)').fetchall()}
    for name, declaration in (("alignment_json", "TEXT NOT NULL DEFAULT '{}'"), ("course_link_revision", "INTEGER NOT NULL DEFAULT 0")):
        if name not in columns:
            conn.execute(f'ALTER TABLE smart_attendance_session_links ADD COLUMN {name} {declaration}')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_auto_sync_enabled ON "
                 "smart_attendance_auto_sync(enabled,state,updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_session_links_offering ON smart_attendance_session_links(class_offering_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_course_links_owner ON smart_attendance_course_links(owner_teacher_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_student_correction_round ON smart_attendance_student_corrections(local_round_id,student_id,created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_student_correction_offering ON smart_attendance_student_corrections(class_offering_id,state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_student_correction_session ON smart_attendance_student_corrections(session_id)")
