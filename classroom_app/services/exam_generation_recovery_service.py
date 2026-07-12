from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


def expire_stale_exam_generation_tasks(
    conn: sqlite3.Connection,
    *,
    stale_minutes: int = 180,
) -> int:
    """Mark abandoned legacy generation records failed, excluding durable jobs."""
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(minutes=max(15, int(stale_minutes or 180)))).isoformat()
    durable_filter = ""
    try:
        conn.execute("SELECT 1 FROM ai_jobs LIMIT 1")
        durable_filter = """
          AND NOT EXISTS (
              SELECT 1 FROM ai_jobs j
              WHERE j.source_ref = 'exam_paper:' || exam_papers.id
                AND j.status IN ('queued', 'running', 'retry_wait', 'result_ready')
          )
        """
    except Exception:
        durable_filter = ""
    cursor = conn.execute(
        f"""
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
          {durable_filter}
        """,
        (now, cutoff),
    )
    return int(cursor.rowcount or 0)
