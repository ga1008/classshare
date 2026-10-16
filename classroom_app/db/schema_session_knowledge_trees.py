"""Schema for per-session knowledge trees (课次知识树 / 课次生命周期).

Each ``class_offering_sessions`` row may own one knowledge tree: an AI-built
mind map of the lesson (1-4 major points, 2-3 levels deep) whose leaves carry
an importance weight summing to 100. The teacher ticks finished leaves; the
weighted sum is the session's own completion progress, independent of its
calendar date and of any other offering teaching the same course.

``tree_json`` holds the normalized tree, ``completed_json`` the finished leaf
ids. ``source_fingerprint`` records the bound learning materials (id + file
hash) at generation time so a later document change can flag the tree as
stale. ``status``/``ai_gen_status``/``ai_gen_error`` follow the durable process
job contract (see ``durable_process_job_worker._TARGETS``).

Engine-aware, idempotent, ensured at startup like ``schema_assessment_plans``;
intentionally NOT part of ``REQUIRED_POSTGRES_TABLES``.
"""

from __future__ import annotations

from typing import Any

_SCHEMA_READY = False


def ensure_session_knowledge_tree_schema(conn: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_knowledge_trees (
            id TEXT PRIMARY KEY,
            class_offering_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL UNIQUE,
            teacher_id INTEGER,
            status TEXT NOT NULL DEFAULT 'empty',
            ai_gen_status TEXT,
            ai_gen_error TEXT,
            ai_gen_task_id TEXT,
            ai_gen_started_at TEXT,
            tree_json TEXT NOT NULL DEFAULT '{}',
            completed_json TEXT NOT NULL DEFAULT '[]',
            progress_percent INTEGER NOT NULL DEFAULT 0,
            leaf_count INTEGER NOT NULL DEFAULT 0,
            completed_count INTEGER NOT NULL DEFAULT 0,
            source_fingerprint TEXT NOT NULL DEFAULT '',
            source_label TEXT NOT NULL DEFAULT '',
            generated_at TEXT,
            completed_at TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_knowledge_trees_offering "
        "ON session_knowledge_trees (class_offering_id)"
    )
    _SCHEMA_READY = True
