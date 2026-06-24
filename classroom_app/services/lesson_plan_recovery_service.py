"""Recover stale lesson-plan AI jobs.

Generation/import run as in-process asyncio tasks; if the process restarts or a
task dies, the row can be left stuck in ``generating``/``parsing``. The list
page calls this on load to flip long-stale rows to ``failed`` (so the placeholder
card offers retry/delete) — mirroring ``exam_generation_recovery_service``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from ..db.schema_lesson_plans import ensure_lesson_plan_schema


def expire_stale_lesson_plan_tasks(conn: sqlite3.Connection, *, stale_minutes: int = 30) -> int:
    ensure_lesson_plan_schema(conn)
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(minutes=max(10, int(stale_minutes or 30)))).isoformat()
    cursor = conn.execute(
        """
        UPDATE lesson_plans
        SET status = 'failed',
            ai_gen_status = 'failed',
            ai_gen_error = COALESCE(
                NULLIF(ai_gen_error, ''),
                'AI 任务在服务重启或异常中断后未恢复，请重试或删除。'
            ),
            updated_at = ?
        WHERE status IN ('generating', 'parsing')
          AND COALESCE(ai_gen_status, '') IN ('pending', 'running', '')
          AND COALESCE(updated_at, created_at) < ?
        """,
        (now, cutoff),
    )
    return int(cursor.rowcount or 0)
