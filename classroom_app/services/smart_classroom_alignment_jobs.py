"""Commit-coupled classroom alignment signals for the existing scheduler.

Opening/replanning a classroom only records a small durable task. School reads
run after commit, through the workflow's account lease and batched matcher.
An immutable input fingerprint never re-arms or steals a running task.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import sqlite3

from ..database import get_db_connection
from .scheduled_task_service import get_owner_task_by_dedupe, register_task_handler, schedule_task

TASK_KIND = "smart_classroom_align_offering"
SCAN_KIND = "smart_classroom_alignment_bootstrap"
SCAN_KEY = "smart-classroom:alignment-bootstrap"
SCAN_INTERVAL_SECONDS = 300
SCAN_BATCH_SIZE = 20


def _ready(conn):
    """Do not introduce lazy DDL into ordinary teaching mutations or migrations."""
    names = ("scheduled_tasks", "smart_attendance_course_links")
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)", names).fetchall()
    else:
        rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() "
                            "AND table_name IN (?,?)", names).fetchall()
    return len(rows) == len(names)


def _context(conn, teacher_id, offering_id):
    from .smart_classroom_workflow_service import offering_alignment_context
    return offering_alignment_context(conn, {"role": "teacher", "id": int(teacher_id)}, int(offering_id))


def signal_offering_alignment(conn, offering_id, *, expected_teacher_id=None):
    """Record a signal in the caller's transaction; never commit or use network.

    The offering lock serializes the scheduler's existing read/insert dedupe
    implementation, including concurrent first signals on native PostgreSQL.
    Startup/legacy fixtures without the new schema simply have nothing to queue.
    """
    if not _ready(conn):
        return None
    offering_id = int(offering_id)
    if isinstance(conn, sqlite3.Connection):
        conn.execute("UPDATE class_offerings SET id=id WHERE id=?", (offering_id,))
    else:
        # Signals may follow child inserts holding FK KEY SHARE on this row.
        # Serialize competing signals without upgrading those locks to an
        # incompatible FOR UPDATE lock and deadlocking concurrent imports.
        conn.execute("SELECT id FROM class_offerings WHERE id=? FOR NO KEY UPDATE", (offering_id,)).fetchone()
    owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?", (offering_id,)).fetchone()
    if not owner or not owner["teacher_id"]:
        return None
    teacher_id = int(owner["teacher_id"])
    if expected_teacher_id is not None and teacher_id != int(expected_teacher_id):
        return None
    context = _context(conn, teacher_id, offering_id)
    fingerprint = str(context["fingerprint"])
    if not fingerprint:
        raise ValueError("Classroom alignment requires a stable input fingerprint")
    return schedule_task(conn, task_kind=TASK_KIND, run_at=datetime.now() + timedelta(seconds=3),
                         dedupe_key=f"smart-classroom:align:{teacher_id}:{offering_id}:{fingerprint}",
                         payload={"teacher_id": teacher_id, "offering_id": offering_id, "fingerprint": fingerprint},
                         owner_role="teacher", owner_user_pk=teacher_id, title="自动对齐智慧课堂课程与课次",
                         priority=180, max_attempts=8, replace=False)


async def handle_alignment(task):
    from .smart_classroom_workflow_service import align_offering

    payload = task.get("payload") or {}
    teacher_id, offering_id = int(payload.get("teacher_id") or 0), int(payload.get("offering_id") or 0)
    if not teacher_id or not offering_id:
        return {"status": "skipped", "reason": "missing_identity"}
    with get_db_connection() as conn:
        owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?", (offering_id,)).fetchone()
        if not owner or int(owner["teacher_id"] or 0) != teacher_id:
            return {"status": "superseded", "reason": "offering_owner_changed"}
        current = _context(conn, teacher_id, offering_id)
        if current["fingerprint"] != payload.get("fingerprint"):
            # A roster import may follow the first signal inside the same
            # original transaction. Preserve that final committed input as a
            # new immutable task; never execute or re-arm the obsolete one.
            replacement = signal_offering_alignment(conn, offering_id, expected_teacher_id=teacher_id)
            conn.commit()
            return {"status": "superseded", "reason": "classroom_changed", "replacement_queued": replacement is not None}
    # No transaction or connection survives the network await. align_offering
    # revalidates the same evidence before storing either binding or candidates.
    result = await align_offering({"role": "teacher", "id": teacher_id}, offering_id, background=True)
    alignment = result.get("alignment") or {}
    return {"status": "processed", "offering_id": offering_id,
            "alignment_state": str(alignment.get("state") or result.get("state") or result.get("status") or "review")}


def handle_bootstrap_scan(task):
    """Bounded catch-up for existing unbound classrooms with saved school access.

    The cursor advances even while an earlier classroom awaits a human choice.
    Only current/future semesters are discovered; explicit mutation signals
    still cover historical classrooms when a teacher actually edits one.
    """
    payload = task.get("payload") or {}
    cursor = max(0, int(payload.get("after_offering_id") or 0))
    with get_db_connection() as conn:
        if not _ready(conn):
            return {"status": "schema_not_ready"}
        rows = conn.execute(
            "SELECT o.id FROM class_offerings o JOIN academic_semesters s ON s.id=o.semester_id "
            "LEFT JOIN smart_attendance_course_links l ON l.class_offering_id=o.id "
            "WHERE o.id>? AND (l.class_offering_id IS NULL OR (l.state='unmatched' AND l.source_json='{}')) "
            "AND (COALESCE(s.end_date,'')='' OR s.end_date>=?) "
            "AND EXISTS (SELECT 1 FROM teacher_smart_classroom_credentials c WHERE c.teacher_id=o.teacher_id "
            "AND c.enabled=1 AND c.last_status='verified') ORDER BY o.id LIMIT ?",
            (cursor, date.today().isoformat(), SCAN_BATCH_SIZE),
        ).fetchall()
        queued = 0
        for row in rows:
            queued += signal_offering_alignment(conn, row["id"]) is not None
        next_cursor = int(rows[-1]["id"]) if rows else 0
        if task.get("id"):
            changed = conn.execute(
                "UPDATE scheduled_tasks SET payload_json=? WHERE id=? AND status='running' AND locked_by=? AND locked_at=?",
                (json.dumps({"after_offering_id": next_cursor}, separators=(",", ":")),
                 int(task["id"]), task.get("locked_by"), task.get("locked_at")),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Alignment discovery lease changed; signal transaction was not committed")
        conn.commit()
    return {"status": "processed", "examined": len(rows), "queued": queued, "after_offering_id": next_cursor}


def ensure_tasks(conn):
    old = get_owner_task_by_dedupe(conn, SCAN_KEY)
    return schedule_task(conn, task_kind=SCAN_KIND, run_at=datetime.now() + timedelta(seconds=45),
                         payload={"after_offering_id": 0}, dedupe_key=SCAN_KEY,
                         recurrence_seconds=SCAN_INTERVAL_SECONDS, title="补齐新课堂的学校课程关联",
                         priority=190, max_attempts=100,
                         replace=bool(old and old["status"] in {"failed", "done", "cancelled"}))


def register_handlers():
    register_task_handler(TASK_KIND, handle_alignment)
    register_task_handler(SCAN_KIND, handle_bootstrap_scan)
