from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


def expire_stale_exam_generation_tasks(
    conn: sqlite3.Connection,
    *,
    stale_minutes: int = 180,
) -> int:
    """Mark AI exam-generation records as failed when their in-memory task is gone."""
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(minutes=max(15, int(stale_minutes or 180)))).isoformat()
    cursor = conn.execute(
        """
        UPDATE exam_papers
        SET ai_gen_status = 'failed',
            ai_gen_error = COALESCE(
                NULLIF(ai_gen_error, ''),
                'AI 生成任务在服务重启或异常中断后未恢复，请重新生成。'
            ),
            updated_at = ?
        WHERE status = 'generating'
          AND COALESCE(ai_gen_status, '') IN ('pending', 'running')
          AND COALESCE(updated_at, created_at) < ?
        """,
        (now, cutoff),
    )
    return int(cursor.rowcount or 0)
