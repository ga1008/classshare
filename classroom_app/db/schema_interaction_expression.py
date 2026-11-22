"""Versioned radar read models. No foreign key or write path into cultivation scores."""

EXPRESSION_MODEL_VERSION = "course-evidence-v1"

EXPRESSION_TABLES = {
    "interaction_expression_snapshots": """CREATE TABLE IF NOT EXISTS interaction_expression_snapshots (
        class_offering_id BIGINT NOT NULL REFERENCES class_offerings(id) ON DELETE CASCADE,
        student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        model_version TEXT NOT NULL, self_json TEXT NOT NULL, teacher_json TEXT NOT NULL,
        calculated_at TEXT NOT NULL, PRIMARY KEY (class_offering_id,student_id)
    )""",
    "interaction_expression_refresh_state": """CREATE TABLE IF NOT EXISTS interaction_expression_refresh_state (
        class_offering_id BIGINT PRIMARY KEY REFERENCES class_offerings(id) ON DELETE CASCADE,
        model_version TEXT NOT NULL DEFAULT '', calculated_at TEXT NOT NULL DEFAULT '',
        retry_after TEXT NOT NULL DEFAULT '', lease_token TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT ''
    )""",
}

EXPRESSION_COLUMNS = {
    "interaction_expression_snapshots": ("class_offering_id", "student_id", "model_version", "self_json", "teacher_json", "calculated_at"),
    "interaction_expression_refresh_state": ("class_offering_id", "model_version", "calculated_at", "retry_after", "lease_token", "lease_until"),
}


def ensure_interaction_expression_schema(conn):
    for ddl in EXPRESSION_TABLES.values():
        conn.execute(ddl)
