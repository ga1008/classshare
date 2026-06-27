"""Schema for the teacher assessment-plan system (考核计划表 / 过程材料).

An *assessment plan* (``assessment_plans``) is a teacher content asset that
represents one course's 《课程考核计划表》 for a given semester: a small block of
template fields (课程名称/专业年级班级/考核类型/考核方式/命题教师/系主任审核/命题日期…)
plus an ordered list of 考核项目 (考核形式 / 考核技能·内容 / 分值，合计 100) and the
fixed template 注释. It mirrors the lesson-plan (``lesson_plans``) model — a TEXT
uuid primary key, JSON content blobs, tags, an org-scoped sharing level, and the
AI-generation status fields used to drive a placeholder card while a multi-minute
generate/import job runs in the background.

The document lives in JSON columns:

* ``fields_json``  — the normalized template fields (see
  ``material_final_document_service`` field set).
* ``items_json``   — the 考核项目 list (``assessment_form`` / ``content`` / ``score``).
* ``notes_json``   — the 表后注释 (defaults to ``ASSESSMENT_PLAN_NOTES``).

Signatures are referenced (not embedded) by ``examiner_signature_id`` /
``reviewer_signature_id`` pointing at ``electronic_signatures`` rows; the export
resolves them to the stored image and embeds it into the docx.

Sharing follows the same 系部 → 院级 → 校级 ladder as materials/lesson-plans with
the owner's org unit snapshotted onto the row so visibility needs no join.

The DDL is engine-aware and idempotent, ensured lazily at runtime — mirroring
``schema_lesson_plans`` / ``schema_polls``. It is intentionally NOT added to
``REQUIRED_POSTGRES_TABLES`` (the PG validate path asserts no DDL). Timestamps are
ISO-8601 TEXT and booleans are stored as INTEGER 0/1.
"""

from __future__ import annotations

from typing import Any

_SCHEMA_READY = False


def ensure_assessment_plan_schema(conn: Any) -> None:
    """Create the assessment-plan table + indexes on either engine (idempotent)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assessment_plans (
            id TEXT PRIMARY KEY,
            teacher_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '课程考核计划表',
            course_id INTEGER,
            class_offering_id INTEGER,
            fields_json TEXT NOT NULL DEFAULT '{}',
            items_json TEXT NOT NULL DEFAULT '[]',
            notes_json TEXT NOT NULL DEFAULT '[]',
            examiner_signature_id INTEGER,
            reviewer_signature_id INTEGER,
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
        "CREATE INDEX IF NOT EXISTS idx_assessment_plans_owner "
        "ON assessment_plans (teacher_id, status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assessment_plans_scope "
        "ON assessment_plans (scope_level, college, department)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assessment_plans_task "
        "ON assessment_plans (ai_gen_task_id)"
    )

    _SCHEMA_READY = True
