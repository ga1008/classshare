"""Startup-only encrypted, cross-process school session and login grants."""

SMART_CLASSROOM_SESSION_TABLES = {
    "smart_classroom_sessions": """CREATE TABLE IF NOT EXISTS smart_classroom_sessions (
        external_account_key TEXT PRIMARY KEY,
        secret_fingerprint TEXT NOT NULL,
        token_encrypted TEXT NOT NULL DEFAULT '', token_version BIGINT NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'empty'
          CHECK(state IN ('empty','active','needs_teacher_reauth')),
        login_owner TEXT NOT NULL DEFAULT '', login_until_ms BIGINT NOT NULL DEFAULT 0,
        last_login_ms BIGINT NOT NULL DEFAULT 0, next_login_at_ms BIGINT NOT NULL DEFAULT 0,
        recovery_used INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
        updated_at_ms BIGINT NOT NULL
    )""",
    "smart_classroom_session_grants": """CREATE TABLE IF NOT EXISTS smart_classroom_session_grants (
        id TEXT PRIMARY KEY, external_account_key TEXT NOT NULL,
        credential_id BIGINT NOT NULL, credential_generation TEXT NOT NULL, scope_key TEXT NOT NULL,
        expires_at_ms BIGINT NOT NULL, recovery_count INTEGER NOT NULL DEFAULT 0,
        created_at_ms BIGINT NOT NULL,
        UNIQUE(credential_id,credential_generation,scope_key)
    )""",
}

SMART_CLASSROOM_SESSION_REQUIRED_COLUMNS = {
    "smart_classroom_sessions": ("external_account_key", "secret_fingerprint", "token_encrypted",
        "token_version", "state", "login_owner", "login_until_ms", "last_login_ms", "next_login_at_ms",
        "recovery_used", "error_code", "updated_at_ms"),
    "smart_classroom_session_grants": ("id", "external_account_key", "credential_id",
        "credential_generation", "scope_key", "expires_at_ms", "recovery_count", "created_at_ms"),
}


def ensure_smart_classroom_sessions_schema(conn, *, engine=None):
    for ddl in SMART_CLASSROOM_SESSION_TABLES.values():
        conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_classroom_session_grants_credential "
                 "ON smart_classroom_session_grants(credential_id)")
