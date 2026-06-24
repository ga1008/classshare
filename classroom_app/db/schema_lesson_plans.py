"""Schema for the teacher lesson-plan system (教案).

A *lesson plan* (``lesson_plans``) is a teacher-private content asset that
represents the whole-semester teaching plan for one course offering: a cover
page plus one 8x4 table per class session (课次). It mirrors the exam-paper
(``exam_papers``) model — a TEXT uuid primary key, a JSON content blob, tags,
an org-scoped sharing level, and the AI-generation status fields used to drive
placeholder cards while a multi-minute generation/parse job runs in the
background.

The whole document lives in two JSON columns:

* ``cover_json``    — the封面 fields (课程名称/类别/学分/学时/授课教师/教学单位/
  授课班级/使用教材/出版社/学期/学校).
* ``sessions_json`` — an array of per-session objects (授课时间/章节/目的要求/
  重点难点/方法手段/教学内容及过程/旁批/教学后记).

Sharing follows the same 系部 → 院级 → 校级 ladder as materials/exams, with the
owner's org unit snapshotted onto the row (``school_code``/``school_name``/
``college``/``department``) so visibility can be computed without a join.

The DDL is engine-aware and idempotent (``CREATE TABLE IF NOT EXISTS`` on both
SQLite and PostgreSQL), ensured lazily at runtime — mirroring
``schema_polls`` / ``schema_scheduler``. Timestamps are ISO-8601 TEXT and
booleans are stored as INTEGER 0/1, consistent with the rest of the codebase.
"""

from __future__ import annotations

from typing import Any

_SCHEMA_READY = False


def ensure_lesson_plan_schema(conn: Any) -> None:
    """Create the lesson-plan table + indexes on either engine (idempotent)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    # TEXT uuid primary key — identical strategy to ``exam_papers`` so the same
    # id can flow through routers/services/exports unchanged across engines.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lesson_plans (
            id TEXT PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '教案',
            course_id INTEGER,
            class_offering_id INTEGER,
            cover_json TEXT NOT NULL DEFAULT '{}',
            sessions_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            scope_level TEXT NOT NULL DEFAULT 'private',
            source_type TEXT NOT NULL DEFAULT 'blank',
            status TEXT NOT NULL DEFAULT 'draft',
            ai_gen_task_id TEXT,
            ai_gen_status TEXT,
            ai_gen_error TEXT,
            ai_gen_progress TEXT NOT NULL DEFAULT '{}',
            inherited_from TEXT,
            school_code TEXT NOT NULL DEFAULT '',
            school_name TEXT NOT NULL DEFAULT '',
            college TEXT NOT NULL DEFAULT '',
            department TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lesson_plans_owner "
        "ON lesson_plans (teacher_id, status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lesson_plans_scope "
        "ON lesson_plans (scope_level, college, department)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lesson_plans_task "
        "ON lesson_plans (ai_gen_task_id)"
    )

    _SCHEMA_READY = True
