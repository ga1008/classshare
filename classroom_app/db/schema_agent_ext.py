"""Agent 任务中心扩展列（运行时管理、引擎感知、幂等）。

为 ``agent_tasks`` 增加任务对话/重试/附件/来源等扩展列。和 scheduler/gongwen
表一样，这一步在运行时确保（sqlite 与 postgres 都执行），不进中央 postgres
迁移清单；每进程只执行一次。
"""
from __future__ import annotations

from .connection import get_configured_db_engine

_SCHEMA_READY = False

# 列名 -> (sqlite 定义, postgres 定义)
_AGENT_TASK_EXTENSION_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("parent_task_id", "INTEGER", "BIGINT"),
    ("origin", "TEXT NOT NULL DEFAULT 'manual'", "TEXT NOT NULL DEFAULT 'manual'"),
    ("attachments_json", "TEXT NOT NULL DEFAULT '[]'", "TEXT NOT NULL DEFAULT '[]'"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
)


def ensure_agent_task_extension_schema(conn, *, force: bool = False) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    engine = get_configured_db_engine()
    if engine == "postgres":
        for column_name, _sqlite_def, pg_def in _AGENT_TASK_EXTENSION_COLUMNS:
            conn.execute(
                f"ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS {column_name} {pg_def}"
            )
    else:
        import sqlite3

        try:
            cursor = conn.execute('PRAGMA table_info("agent_tasks")')
            rows = cursor.fetchall()
        except AttributeError:
            # Lightweight test doubles used by write-path tests do not expose
            # fetchall(). They are only verifying SQL routing, not runtime DDL.
            return
        existing = {str(row[1]) for row in rows}
        for column_name, sqlite_def, _pg_def in _AGENT_TASK_EXTENSION_COLUMNS:
            if column_name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE agent_tasks ADD COLUMN {column_name} {sqlite_def}")
            except sqlite3.OperationalError:
                pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_parent ON agent_tasks (parent_task_id)"
    )
    _SCHEMA_READY = True
