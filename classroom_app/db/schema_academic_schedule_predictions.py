"""Durable schedule identities and last complete JWXT prediction snapshot.

Created only by startup migrations; overview GETs never write schema.
All keys are application identities, independent of replaceable sync-item IDs.
"""
from __future__ import annotations

TABLES = (
    "teacher_academic_schedule_sync_state",
    "teacher_academic_schedule_snapshots",
    "academic_schedule_session_bindings",
    "academic_schedule_change_session_links",
)


def academic_schedule_prediction_schema_statements(engine: str = "sqlite") -> tuple[str, ...]:
    # TEXT timestamps are normalized UTC ISO strings, comparable on both engines.
    return (
        """CREATE TABLE IF NOT EXISTS teacher_academic_schedule_sync_state (
            teacher_id BIGINT PRIMARY KEY REFERENCES teachers(id) ON DELETE CASCADE,
            lease_token TEXT NOT NULL DEFAULT '', lease_expires_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idle', last_attempt_at TEXT NOT NULL DEFAULT '',
            last_success_at TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS teacher_academic_schedule_snapshots (
            teacher_id BIGINT NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            semester_id BIGINT NOT NULL REFERENCES academic_semesters(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, published_at TEXT NOT NULL, publication_token TEXT NOT NULL,
            snapshot_json TEXT NOT NULL, request_history_json TEXT NOT NULL DEFAULT '[]',
            lessons_json TEXT NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]',
            covered_offering_ids_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY(teacher_id,semester_id)
        )""",
        """CREATE TABLE IF NOT EXISTS academic_schedule_session_bindings (
            teacher_id BIGINT NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            semester_id BIGINT NOT NULL REFERENCES academic_semesters(id) ON DELETE CASCADE,
            session_id BIGINT NOT NULL REFERENCES class_offering_sessions(id) ON DELETE CASCADE,
            class_offering_id BIGINT NOT NULL REFERENCES class_offerings(id) ON DELETE CASCADE,
            event_key TEXT NOT NULL, identity_json TEXT NOT NULL, original_json TEXT NOT NULL,
            current_json TEXT NOT NULL, evidence TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(teacher_id,semester_id,session_id), UNIQUE(event_key)
        )""",
        """CREATE TABLE IF NOT EXISTS academic_schedule_change_session_links (
            teacher_id BIGINT NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            semester_id BIGINT NOT NULL REFERENCES academic_semesters(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL, detail_id TEXT NOT NULL,
            class_offering_id BIGINT NOT NULL REFERENCES class_offerings(id) ON DELETE CASCADE,
            session_id BIGINT NOT NULL REFERENCES class_offering_sessions(id) ON DELETE CASCADE,
            status TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(teacher_id,semester_id,request_id,detail_id,class_offering_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_academic_schedule_binding_offering ON academic_schedule_session_bindings(class_offering_id,session_id)",
        "CREATE INDEX IF NOT EXISTS idx_academic_schedule_change_session ON academic_schedule_change_session_links(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_academic_schedule_snapshot_semester ON teacher_academic_schedule_snapshots(semester_id,teacher_id)",
    )


def ensure_academic_schedule_prediction_schema(conn, *, engine: str = "sqlite") -> None:
    for statement in academic_schedule_prediction_schema_statements(engine):
        conn.execute(statement)


ACADEMIC_SCHEDULE_PREDICTION_POSTGRES_TABLES = dict(zip(
    TABLES, academic_schedule_prediction_schema_statements("postgres")[:len(TABLES)],
))


def _required_columns() -> dict[str, tuple[str, ...]]:
    import re
    return {
        table: tuple(re.findall(r"(?:\(|,)\s*([a-z][a-z_0-9]*)\s+(?:BIGINT|TEXT|INTEGER)\b", ddl))
        for table, ddl in ACADEMIC_SCHEDULE_PREDICTION_POSTGRES_TABLES.items()
    }


ACADEMIC_SCHEDULE_PREDICTION_REQUIRED_COLUMNS = _required_columns()
