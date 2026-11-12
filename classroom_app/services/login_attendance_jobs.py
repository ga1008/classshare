"""One bounded recurring scan, not one process/timer per student."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..database import get_db_connection
from . import login_attendance_service as attendance
from .login_attendance_access_service import enabled
from .scheduled_task_service import register_task_handler, schedule_task

TASK_KIND = "login_attendance_scan"
RETENTION_KIND = "login_attendance_retention"


def handle_scan(payload):
    if not enabled():
        return {"enabled":False}
    stamp = attendance.now_ms()
    with get_db_connection() as conn:
        # Durable/manual settlements have their own worker and must not occupy
        # the bounded ordinary scan forever (especially a failed generation
        # awaiting teacher review). evaluate_round still rechecks under locks.
        rows=conn.execute("SELECT r.id FROM login_attendance_rounds r WHERE r.is_current=1 AND r.class_offering_id IS NOT NULL AND r.next_evaluation_ms<=? "
            "AND (r.state IN ('scheduled','running','settling') OR (r.state='published' AND r.created_at_ms>=?)) "
            "AND NOT EXISTS (SELECT 1 FROM login_attendance_settlement_runs s WHERE s.round_id=r.id "
            "AND s.generation=r.settlement_generation AND s.state IN ('queued','running','failed')) "
            "ORDER BY r.next_evaluation_ms,r.id LIMIT 12",(stamp,stamp-7*86400_000)).fetchall()
    completed, failures = [], []
    for row in rows:
        try:
            with get_db_connection() as conn:
                attendance.evaluate_round(conn,row["id"],timestamp=stamp)
                conn.commit()
            completed.append(row["id"])
        except Exception as exc:
            # Keep the round due; next scan retries. Avoid exposing raw SQL or
            # student evidence in scheduler results and logs.
            failures.append({"round_id":row["id"],"error_type":type(exc).__name__})
    if failures and not completed:
        raise RuntimeError("Attendance scan could not apply due rounds; retry required")
    return {"completed":completed,"retry_pending":failures}


def handle_retention(payload):
    from .approval_workflow_schema import ensure_approval_workflow_schema

    cutoff=attendance.now_ms()-90*86400_000
    with get_db_connection() as conn:
        # Approvals are lazy in the existing platform. A fresh installation
        # may have observations before anyone has opened an approval page.
        ensure_approval_workflow_schema(conn)
        # Bounded cleanup. Pending appeals pin every related student's window.
        rows=conn.execute("SELECT e.event_key FROM login_attendance_access e WHERE e.received_at_ms<? AND NOT EXISTS ("
            "SELECT 1 FROM login_attendance_records s JOIN login_attendance_rounds r ON r.id=s.round_id "
            "JOIN approval_requests a ON a.subject_id=CAST(s.id AS TEXT) AND a.request_type='login_attendance_appeal' AND a.status='pending' "
            "WHERE s.student_id=e.student_id AND e.observed_at_ms>=r.window_start_ms AND e.observed_at_ms<r.window_end_ms) "
            "ORDER BY e.received_at_ms LIMIT 5000",(cutoff,)).fetchall()
        for offset in range(0,len(rows),250):
            keys=[row["event_key"] for row in rows[offset:offset+250]]
            conn.execute("DELETE FROM login_attendance_access WHERE event_key IN ("+",".join("?" for _ in keys)+")",tuple(keys))
        conn.commit()
    return {"removed_access_observations":len(rows)}


def ensure_tasks(conn):
    from .scheduled_task_service import get_owner_task_by_dedupe
    # 5000 / 10 minutes exceeds 200 continuously online students' daily
    # per-minute intake; a single daily 2500-row batch would never catch up.
    for kind,interval in ((TASK_KIND,15),(RETENTION_KIND,600)):
        key=f"attendance:{kind}"
        old=get_owner_task_by_dedupe(conn,key)
        schedule_task(conn,task_kind=kind,run_at=datetime.now()+timedelta(seconds=5 if kind==TASK_KIND else 3600),
                      payload={},dedupe_key=key,recurrence_seconds=interval,priority=60,
                      title="课堂签到核验" if kind==TASK_KIND else "课堂接入证据保留清理",
                      max_attempts=100,replace=bool(old and old["status"] in {"failed","completed","cancelled"}))


def register_handlers():
    register_task_handler(TASK_KIND,handle_scan)
    register_task_handler(RETENTION_KIND,handle_retention)
    from .login_attendance_settlement_service import TASK_KIND as SETTLEMENT_KIND
    register_task_handler(SETTLEMENT_KIND,handle_settlement)


def handle_settlement(task):
    from .login_attendance_settlement_service import process_settlement
    payload=task['payload']
    return process_settlement(int(payload['teacher_id']),str(payload['run_id']),scheduler_task=task)
