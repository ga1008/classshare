"""Recover stale process-material AI jobs.

Process-material generation/import jobs run as in-process asyncio tasks. If the
server restarts or a task dies, rows can remain stuck in ``generating`` or
``parsing`` forever. List pages call these helpers before rendering so teachers
get a failed card with the correct next action instead of an endless spinner.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ..db.schema_assessment_plans import ensure_assessment_plan_schema
from ..db.schema_teacher_evaluations import ensure_teacher_evaluation_schema


_RECOVERABLE_TABLES = {"assessment_plans", "teacher_evaluations"}


def _expire_stale_tasks(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    ensure_schema: Callable[[Any], None],
    stale_minutes: int,
    teacher_id: int | None = None,
) -> int:
    if table_name not in _RECOVERABLE_TABLES:
        raise ValueError(f"Unsupported process-material table: {table_name}")
    ensure_schema(conn)
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(minutes=max(10, int(stale_minutes or 30)))).isoformat()
    teacher_filter = ""
    params: list[Any] = [now, cutoff]
    if teacher_id is not None:
        teacher_filter = " AND teacher_id = ?"
        params.append(int(teacher_id))
    cursor = conn.execute(
        f"""
        UPDATE {table_name}
        SET status = 'failed',
            ai_gen_status = 'failed',
            ai_gen_error = COALESCE(
                NULLIF(ai_gen_error, ''),
                CASE
                    WHEN source_type = 'import'
                    THEN 'AI 解析任务在服务重启或异常中断后未恢复，请重新上传文件再解析或删除。'
                    ELSE 'AI 生成任务在服务重启或异常中断后未恢复，请重试生成或删除。'
                END
            ),
            updated_at = ?
        WHERE status IN ('generating', 'parsing')
          AND COALESCE(ai_gen_status, '') IN ('pending', 'running', '')
          AND COALESCE(updated_at, created_at) < ?
          {teacher_filter}
        """,
        params,
    )
    return int(cursor.rowcount or 0)


def expire_stale_assessment_plan_tasks(
    conn: sqlite3.Connection,
    *,
    stale_minutes: int = 30,
    teacher_id: int | None = None,
) -> int:
    return _expire_stale_tasks(
        conn,
        table_name="assessment_plans",
        ensure_schema=ensure_assessment_plan_schema,
        stale_minutes=stale_minutes,
        teacher_id=teacher_id,
    )


def expire_stale_teacher_evaluation_tasks(
    conn: sqlite3.Connection,
    *,
    stale_minutes: int = 30,
    teacher_id: int | None = None,
) -> int:
    return _expire_stale_tasks(
        conn,
        table_name="teacher_evaluations",
        ensure_schema=ensure_teacher_evaluation_schema,
        stale_minutes=stale_minutes,
        teacher_id=teacher_id,
    )
