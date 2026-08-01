"""Engine-aware schema for paired JWXT final-material synchronization.

One batch represents one teacher + class offering and links the two official
FineReport exports downloaded in the same authenticated academic-system
session.  The canonical parsed/export payload continues to live in
``material_ai_import_records``; this table only owns synchronization,
idempotency, validation, and edit-state metadata.
"""

from __future__ import annotations

from typing import Any

from .connection import get_configured_db_engine


_SCHEMA_READY = False


def _add_column_if_missing(
    conn: Any,
    *,
    engine: str,
    table: str,
    column: str,
    definition: str,
) -> None:
    if engine == "postgres":
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        return
    existing = {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_academic_final_material_schema(conn: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    engine = get_configured_db_engine()
    timestamp_type = "TIMESTAMP" if engine == "postgres" else "TEXT"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS academic_final_material_batches (
            id TEXT PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            class_offering_id INTEGER NOT NULL,
            exam_roster_item_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            exam_course_key TEXT NOT NULL DEFAULT '',
            course_code TEXT NOT NULL DEFAULT '',
            course_name TEXT NOT NULL DEFAULT '',
            teaching_class_id TEXT NOT NULL DEFAULT '',
            teaching_class_name TEXT NOT NULL DEFAULT '',
            grade_entry_status TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'queued',
            grade_record_id INTEGER,
            analysis_record_id INTEGER,
            grade_source_hash TEXT NOT NULL DEFAULT '',
            analysis_source_hash TEXT NOT NULL DEFAULT '',
            grade_source_size INTEGER NOT NULL DEFAULT 0,
            analysis_source_size INTEGER NOT NULL DEFAULT 0,
            validation_status TEXT NOT NULL DEFAULT 'unchecked',
            validation_json TEXT NOT NULL DEFAULT '{{}}',
            edit_state_json TEXT NOT NULL DEFAULT '{{}}',
            sync_options_json TEXT NOT NULL DEFAULT '{{}}',
            last_error TEXT NOT NULL DEFAULT '',
            source_summary_json TEXT NOT NULL DEFAULT '[]',
            synced_at {timestamp_type},
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (teacher_id, class_offering_id),
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE,
            FOREIGN KEY (exam_roster_item_id) REFERENCES teacher_academic_exam_roster_items (id) ON DELETE SET NULL,
            FOREIGN KEY (grade_record_id) REFERENCES material_ai_import_records (id) ON DELETE SET NULL,
            FOREIGN KEY (analysis_record_id) REFERENCES material_ai_import_records (id) ON DELETE SET NULL
        )
        """
    )
    _add_column_if_missing(
        conn,
        engine=engine,
        table="academic_final_material_batches",
        column="sync_options_json",
        definition="TEXT NOT NULL DEFAULT '{}'",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_final_material_teacher_status "
        "ON academic_final_material_batches (teacher_id, sync_status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_final_material_records "
        "ON academic_final_material_batches (grade_record_id, analysis_record_id)"
    )
    _SCHEMA_READY = True
