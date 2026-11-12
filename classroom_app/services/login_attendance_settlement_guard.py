"""Fence school writes before replacing a local settlement generation."""
import sqlite3

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine


def _exists(conn, table):
    # Lightweight synthetic fixtures may only initialize the local subsystem.
    if isinstance(conn, sqlite3.Connection):
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
    return True


def settlement_school_guard(conn, round_id, *, pause=True):
    """Caller holds parent/round locks for mutation; dry-run is read-only.

    Submitted school requests must first be read back. An unsent whole-class
    authorization can be revoked atomically, so the new local result does not
    silently inherit a teacher's old permission to push.
    """
    key = str(round_id)
    def busy(message):
        raise HTTPException(409, {'code': 'school_sync_busy', 'message': message})

    if _exists(conn, 'smart_attendance_student_corrections') and conn.execute(
        "SELECT 1 FROM smart_attendance_student_corrections WHERE local_round_id=? AND mode='push' "
        "AND state IN ('queued','checking','outcome_unknown') LIMIT 1", (key,)).fetchone():
        busy('该轮学生改签正在推送或等待学校回读，请完成后再立即结算；现有记录保留。')
    if _exists(conn, 'smart_attendance_sync_runs') and conn.execute(
        "SELECT 1 FROM smart_attendance_sync_runs r WHERE local_round_id=? AND "
        "(state='outcome_unknown' OR EXISTS (SELECT 1 FROM smart_attendance_sync_operations o "
        "WHERE o.run_id=r.id AND o.state='outcome_unknown')) LIMIT 1", (key,)).fetchone():
        busy('学校请求的结果尚未核实，请完成回读后再立即结算；不会覆盖原提交。')
    if not _exists(conn, 'smart_attendance_auto_sync'):
        return
    from . import smart_classroom_auto_sync_service as auto
    from . import smart_classroom_write_service as ledger
    suffix = ' FOR UPDATE' if pause and get_configured_db_engine() == 'postgres' else ''
    plan = conn.execute('SELECT * FROM smart_attendance_auto_sync WHERE local_round_id=?'+suffix, (key,)).fetchone()
    if not plan:
        return
    # Lifecycle submit intents use this same plan row even before a run exists.
    if plan['state'] == 'outcome_unknown' or auto._lifecycle_unknown(plan):
        busy('学校开始或结束点名的结果尚未核实，请完成回读后再立即结算。')
    if not pause or not plan['enabled']:
        return
    now = ledger._now()
    message = '本地正在重新结算，旧整班推送授权已停止；结算完成后请明确点击同步与推送。'
    conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,revision=revision+1,state='awaiting_push',message=?,updated_at=? WHERE local_round_id=?",
                 (message, now, key))
    if plan['last_run_id']:
        changed = conn.execute("UPDATE smart_attendance_sync_runs SET state='superseded',revision=revision+1,message=?,updated_at=? "
            "WHERE id=? AND request_key LIKE 'auto:%' AND state NOT IN ('confirmed','outcome_unknown','superseded')",
            (message, now, plan['last_run_id']))
        if changed.rowcount:
            conn.execute("UPDATE smart_attendance_sync_operations SET state='superseded',reason=?,updated_at=? WHERE run_id=? AND state='pending'",
                         (message, now, plan['last_run_id']))
            ledger._event(conn, plan['last_run_id'], 'local_settlement_replaced_authorization', plan['owner_teacher_id'], {})
    if _exists(conn, 'scheduled_tasks'):
        from .scheduled_task_service import cancel_tasks_by_dedupe
        tasks = conn.execute("SELECT dedupe_key FROM scheduled_tasks WHERE task_kind='smart_attendance_auto_sync' "
            "AND dedupe_key LIKE ? AND status IN ('pending','running')", (f'smart-auto:{round_id}:%',)).fetchall()
        for task in tasks:
            cancel_tasks_by_dedupe(conn, task['dedupe_key'])
