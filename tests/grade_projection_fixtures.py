"""Upgrade small legacy material fixtures to the current additive grade schema."""
from classroom_app.db import schema_ai_jobs, schema_study_group_scheme
from classroom_app.db.schema_assignments import ensure_assessment_classification_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema


def add_grade_projection_schema(conn):
    ensure_assessment_classification_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(submissions)")}
    for name, definition in {
        "status": "TEXT DEFAULT 'graded'", "feedback_md": "TEXT DEFAULT ''",
        "submitted_at": "TEXT DEFAULT '2026-09-07'", "resubmission_allowed": "INTEGER DEFAULT 0",
        "is_absence_score": "INTEGER DEFAULT 0", "is_late_submission": "INTEGER DEFAULT 0",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE submissions ADD COLUMN {name} {definition}")
    previous_ai = set(schema_ai_jobs._SCHEMA_READY_ENGINES)
    previous_group = schema_study_group_scheme._SCHEMA_READY
    try:
        schema_ai_jobs._SCHEMA_READY_ENGINES.clear()
        schema_ai_jobs.ensure_ai_job_schema(conn, engine="sqlite")
        ensure_classroom_activity_schema(conn)
        schema_study_group_scheme._SCHEMA_READY = False
        schema_study_group_scheme.ensure_study_group_scheme_schema(conn)
    finally:
        schema_ai_jobs._SCHEMA_READY_ENGINES = previous_ai
        schema_study_group_scheme._SCHEMA_READY = previous_group
    conn.execute("CREATE TABLE learning_stage_exam_attempts (id INTEGER PRIMARY KEY, assignment_id INTEGER)")
