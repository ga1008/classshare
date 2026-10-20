"""School-scoped directory and complete, read-only teacher timetable caches."""
from __future__ import annotations

TABLES = (
    "academic_teacher_directory",
    "academic_teacher_timetables",
    "academic_teacher_query_cache",
    "academic_teacher_query_leases",
)


def academic_teacher_timetable_schema_statements(engine="sqlite"):
    return (
        """CREATE TABLE IF NOT EXISTS academic_teacher_directory (
            school_code TEXT NOT NULL, teacher_no TEXT NOT NULL,
            name TEXT NOT NULL, college_name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', profile_json TEXT NOT NULL,
            synced_at TEXT NOT NULL, PRIMARY KEY(school_code,teacher_no)
        )""",
        """CREATE TABLE IF NOT EXISTS academic_teacher_timetables (
            school_code TEXT NOT NULL, teacher_no TEXT NOT NULL, term_code TEXT NOT NULL,
            profile_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, lessons_json TEXT NOT NULL,
            summary_json TEXT NOT NULL, synced_at TEXT NOT NULL,
            PRIMARY KEY(school_code,teacher_no,term_code)
        )""",
        """CREATE TABLE IF NOT EXISTS academic_teacher_query_cache (
            school_code TEXT NOT NULL, term_code TEXT NOT NULL, query_key TEXT NOT NULL,
            filters_json TEXT NOT NULL, teacher_nos_json TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '{}', synced_at TEXT NOT NULL,
            PRIMARY KEY(school_code,term_code,query_key)
        )""",
        """CREATE TABLE IF NOT EXISTS academic_teacher_query_leases (
            credential_key TEXT PRIMARY KEY, lease_token TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS idx_academic_teacher_name ON academic_teacher_directory(school_code,name)",
        "CREATE INDEX IF NOT EXISTS idx_academic_teacher_term ON academic_teacher_timetables(school_code,term_code)",
    )


def ensure_academic_teacher_timetable_schema(conn, *, engine="sqlite"):
    for sql in academic_teacher_timetable_schema_statements(engine):
        conn.execute(sql)


ACADEMIC_TEACHER_TIMETABLE_POSTGRES_TABLES = dict(zip(
    TABLES, academic_teacher_timetable_schema_statements("postgres")[:len(TABLES)],
))


def _columns():
    import re
    return {table: tuple(re.findall(
        r"(?:\(|,)\s*([a-z][a-z_0-9]*)\s+(?:BIGINT|TEXT|INTEGER)\b", ddl,
    )) for table, ddl in ACADEMIC_TEACHER_TIMETABLE_POSTGRES_TABLES.items()}


ACADEMIC_TEACHER_TIMETABLE_REQUIRED_COLUMNS = _columns()
