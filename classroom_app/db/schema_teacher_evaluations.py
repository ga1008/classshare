"""Schema for the teacher评学表 (教师评学表 / 过程材料) content asset.

A *teacher evaluation sheet* (``teacher_evaluations``) is one course-class's
《广西外国语学院教师评学表》 for a given semester: a small block of template fields
(课程名称 / 授课班级 / 所在二级学院 / 任课教师 / 教师职称 / 评价时间 / 学年学期) plus a
FIXED list of 10 评价指标 grouped into 学习态度 / 学习过程 / 学习效果 (each 10 分，合计
100), a computed 综合评价 (优秀/良好/一般/较差), and a free-text
《对学生学习情况的分析和今后教学改革建议》.

Unlike the 考核计划表, the indicator set is fixed by the official template — only the
per-indicator 评价得分 and the analysis text vary — so ``items_json`` stores the 10
scored rows and there are no signature columns.

The document lives in JSON columns:

* ``fields_json``   — the normalized template fields.
* ``items_json``    — the 10 评价指标 rows (``group`` / ``indicator`` / ``max_score`` / ``score``).
* ``analysis``      — the 学习情况分析与教学改革建议 free text.

Sharing follows the same 系部 → 院级 → 校级 ladder as materials/lesson-plans, with the
owner's org unit snapshotted onto the row so visibility needs no join.

The DDL is engine-aware and idempotent, ensured lazily at runtime — mirroring
``schema_assessment_plans`` / ``schema_lesson_plans`` / ``schema_polls``. It is
intentionally NOT added to ``REQUIRED_POSTGRES_TABLES`` (the PG validate path asserts
no DDL). Timestamps are ISO-8601 TEXT and booleans are stored as INTEGER 0/1.
"""

from __future__ import annotations

from typing import Any

_SCHEMA_READY = False


def ensure_teacher_evaluation_schema(conn: Any) -> None:
    """Create the teacher-evaluation table + indexes on either engine (idempotent)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_evaluations (
            id TEXT PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '教师评学表',
            course_id INTEGER,
            class_offering_id INTEGER,
            fields_json TEXT NOT NULL DEFAULT '{}',
            items_json TEXT NOT NULL DEFAULT '[]',
            analysis TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            scope_level TEXT NOT NULL DEFAULT 'private',
            source_type TEXT NOT NULL DEFAULT 'blank',
            status TEXT NOT NULL DEFAULT 'draft',
            ai_gen_task_id TEXT,
            ai_gen_status TEXT,
            ai_gen_error TEXT,
            ai_gen_progress TEXT NOT NULL DEFAULT '{}',
            import_preview_json TEXT NOT NULL DEFAULT '{}',
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
        "CREATE INDEX IF NOT EXISTS idx_teacher_evaluations_owner "
        "ON teacher_evaluations (teacher_id, status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_evaluations_scope "
        "ON teacher_evaluations (scope_level, college, department)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teacher_evaluations_task "
        "ON teacher_evaluations (ai_gen_task_id)"
    )

    _SCHEMA_READY = True
