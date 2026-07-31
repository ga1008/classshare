"""Engine-aware schema for teacher-visible academic course evaluations.

The academic system remains the source of truth.  These tables hold a bounded,
read-only mirror so the dashboard never has to contact JWXT while rendering and
so concurrent app workers can share one low-frequency synchronization lease.
"""

from __future__ import annotations

from typing import Any

from .connection import get_configured_db_engine


def _add_column_if_missing(
    conn: Any,
    *,
    engine: str,
    table: str,
    column: str,
    definition: str,
) -> None:
    """Add a small forward-compatible extension on SQLite and PostgreSQL."""
    if engine == "postgres":
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
        return
    existing = {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_academic_evaluation_schema(conn: Any) -> None:
    engine = get_configured_db_engine()
    timestamp_type = "TIMESTAMP" if engine == "postgres" else "TEXT"
    id_type = "SERIAL PRIMARY KEY" if engine == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_academic_evaluation_sync_state (
            id {id_type},
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idle',
            source_course_count INTEGER NOT NULL DEFAULT 0,
            synced_evaluation_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            attempt_started_at {timestamp_type},
            completed_at {timestamp_type},
            next_allowed_at {timestamp_type},
            lease_token TEXT NOT NULL DEFAULT '',
            lease_expires_at {timestamp_type},
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (teacher_id, school_code, academic_year, academic_term),
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_academic_course_evaluations (
            id {id_type},
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER,
            school_code TEXT NOT NULL DEFAULT 'gxufl',
            academic_year TEXT NOT NULL DEFAULT '',
            academic_term TEXT NOT NULL DEFAULT '',
            source_course_key TEXT NOT NULL,
            course_name TEXT NOT NULL DEFAULT '',
            course_name_key TEXT NOT NULL DEFAULT '',
            hour_type_code TEXT NOT NULL DEFAULT '',
            hour_type_name TEXT NOT NULL DEFAULT '',
            evaluation_target_code TEXT NOT NULL DEFAULT '01',
            campus_name TEXT NOT NULL DEFAULT '',
            course_score REAL,
            teacher_weighted_score REAL,
            institution_percentile_score REAL,
            academic_year_course_score REAL,
            enrolled_count INTEGER NOT NULL DEFAULT 0,
            response_count INTEGER NOT NULL DEFAULT 0,
            valid_response_count INTEGER NOT NULL DEFAULT 0,
            institution_rank INTEGER,
            course_unit_rank INTEGER,
            comment_count INTEGER NOT NULL DEFAULT 0,
            meaningful_comment_count INTEGER NOT NULL DEFAULT 0,
            ai_summary TEXT NOT NULL DEFAULT '',
            ai_keywords_json TEXT NOT NULL DEFAULT '[]',
            ai_keyword_status TEXT NOT NULL DEFAULT 'pending',
            ai_analysis_version TEXT NOT NULL DEFAULT '',
            ai_keyword_model TEXT NOT NULL DEFAULT '',
            ai_keyword_error TEXT NOT NULL DEFAULT '',
            ai_keyword_source_hash TEXT NOT NULL DEFAULT '',
            ai_keyword_updated_at {timestamp_type},
            source_summary_json TEXT NOT NULL DEFAULT '[]',
            sync_status TEXT NOT NULL DEFAULT 'active',
            synced_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (
                teacher_id, school_code, academic_year, academic_term,
                source_course_key, hour_type_code, evaluation_target_code
            ),
            FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
            FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_academic_course_evaluation_metrics (
            id {id_type},
            evaluation_id INTEGER NOT NULL,
            source_metric_key TEXT NOT NULL DEFAULT '',
            sequence_no INTEGER NOT NULL DEFAULT 0,
            metric_name TEXT NOT NULL DEFAULT '',
            mean_score REAL,
            satisfaction_score REAL,
            weight_value REAL,
            hour_type_name TEXT NOT NULL DEFAULT '',
            grade_counts_json TEXT NOT NULL DEFAULT '{{}}',
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (evaluation_id, source_metric_key),
            FOREIGN KEY (evaluation_id) REFERENCES teacher_academic_course_evaluations (id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_academic_course_evaluation_comments (
            id {id_type},
            evaluation_id INTEGER NOT NULL,
            source_comment_key TEXT NOT NULL DEFAULT '',
            sequence_no INTEGER NOT NULL DEFAULT 0,
            comment_text TEXT NOT NULL DEFAULT '',
            comment_hash TEXT NOT NULL DEFAULT '',
            is_meaningful INTEGER NOT NULL DEFAULT 1,
            filter_source TEXT NOT NULL DEFAULT 'unclassified',
            created_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (evaluation_id, source_comment_key),
            FOREIGN KEY (evaluation_id) REFERENCES teacher_academic_course_evaluations (id) ON DELETE CASCADE
        )
        """
    )

    _add_column_if_missing(
        conn,
        engine=engine,
        table="teacher_academic_course_evaluations",
        column="meaningful_comment_count",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        engine=engine,
        table="teacher_academic_course_evaluations",
        column="ai_analysis_version",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        engine=engine,
        table="teacher_academic_course_evaluation_comments",
        column="is_meaningful",
        definition="INTEGER NOT NULL DEFAULT 1",
    )
    _add_column_if_missing(
        conn,
        engine=engine,
        table="teacher_academic_course_evaluation_comments",
        column="filter_source",
        definition="TEXT NOT NULL DEFAULT 'unclassified'",
    )
    # Existing rows predate the filter. Keep them visible until the next
    # explicit analysis instead of silently treating them as discarded.
    conn.execute(
        """
        UPDATE teacher_academic_course_evaluations
        SET meaningful_comment_count = comment_count
        WHERE meaningful_comment_count = 0 AND comment_count > 0
          AND COALESCE(ai_analysis_version, '') = ''
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_evaluation_teacher_term "
        "ON teacher_academic_course_evaluations "
        "(teacher_id, semester_id, sync_status, synced_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_evaluation_course_name "
        "ON teacher_academic_course_evaluations "
        "(teacher_id, course_name_key, semester_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_evaluation_metric_parent "
        "ON teacher_academic_course_evaluation_metrics (evaluation_id, sequence_no)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_evaluation_comment_parent "
        "ON teacher_academic_course_evaluation_comments (evaluation_id, sequence_no)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_evaluation_comment_meaningful "
        "ON teacher_academic_course_evaluation_comments "
        "(evaluation_id, is_meaningful, sequence_no)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_academic_evaluation_sync_due "
        "ON teacher_academic_evaluation_sync_state (teacher_id, next_allowed_at, lease_expires_at)"
    )


__all__ = ["ensure_academic_evaluation_schema"]
