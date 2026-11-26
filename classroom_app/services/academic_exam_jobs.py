"""School-nightly and on-demand exam feeds share a durable job per credential owner."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from ..database import get_db_connection
from ..db.schema_scheduler import ensure_scheduler_schema
from .academic_auto_sync_service import sync_teacher_dashboard_reminders
from .academic_service import china_now
from .offering_membership_service import student_offering_where_by_student_id
from .scheduled_task_service import ScheduledTaskResult, register_task_handler
from .semester_identity_service import current_identity, parse_semester_identity

DAILY = "academic_exam_daily"
SYNC = "academic_exam_sync"
PARTIAL = "部分教务来源暂时不可用，已保留成功更新的安排。"
FAILED = "教务同步未完成，已保留上次安排。"
ELIGIBLE = """FROM teachers t WHERE t.is_active = 1 AND EXISTS (
    SELECT 1 FROM teacher_academic_system_credentials cred
    WHERE cred.teacher_id = t.id AND cred.enabled = 1 AND cred.school_code = 'gxufl')"""


def _scheduler_time(value):
    # The shared scheduler compares naive host-local timestamps.
    return value.astimezone().replace(tzinfo=None, microsecond=0)


def daily_at(now, *, following=False):
    due = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if following and due <= now:
        due += timedelta(days=1)
    return _scheduler_time(due)


def ensure_daily(conn):
    ensure_scheduler_schema(conn)
    due = daily_at(china_now()).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO scheduled_tasks(task_kind,dedupe_key,title,priority,run_at,recurrence_seconds,max_attempts)
           VALUES (?,?,'同步本学期考试和监考',70,?,86400,100)
           ON CONFLICT(dedupe_key) DO UPDATE SET status='pending',run_at=excluded.run_at,
               attempt_count=0,last_error='',next_attempt_at=NULL
           WHERE scheduled_tasks.status IN ('failed','cancelled','done')""",
        (DAILY, DAILY, due),
    )


def eligible_teachers(conn, user=None):
    if user is None:
        return [int(r["id"]) for r in conn.execute("SELECT t.id " + ELIGIBLE + " ORDER BY t.id").fetchall()]
    if user["role"] == "teacher":
        row = conn.execute("SELECT t.id " + ELIGIBLE + " AND t.id=?", (user["id"],)).fetchone()
        return [int(row["id"])] if row else []

    today = china_now().date()
    # Legacy classrooms can have a free-text semester instead of a foreign key.
    member = student_offering_where_by_student_id(require_active=True)
    rows = conn.execute(
        f"""SELECT DISTINCT o.teacher_id,o.semester_id,o.semester
            FROM class_offerings o LEFT JOIN academic_semesters sem ON sem.id=o.semester_id
            WHERE {member} AND o.teacher_id IN (SELECT t.id {ELIGIBLE})
              AND (o.semester_id IS NULL OR (sem.start_date<=? AND sem.end_date>=?))""",
        (user["id"], today.isoformat(), today.isoformat()),
    ).fetchall()
    identity = current_identity(today)
    return sorted({int(r["teacher_id"]) for r in rows
                   if r["semester_id"] or parse_semester_identity(r["semester"]) == identity})


def enqueue(conn, teacher_ids):
    # Never reset a live lease or its retry. Simultaneous students in the same
    # class reuse a recent full pull, even if it finished between their clicks.
    now = datetime.now().replace(microsecond=0)
    stamp = now.isoformat()
    cutoff = (now - timedelta(minutes=2)).isoformat()
    for teacher_id in teacher_ids:
        conn.execute(
            """INSERT INTO scheduled_tasks(task_kind,dedupe_key,title,status,priority,run_at,
                      owner_role,owner_user_pk,max_attempts,created_at,updated_at)
               VALUES (?,?,'同步考试和监考','pending',100,?,'teacher',?,3,?,?)
               ON CONFLICT(dedupe_key) DO UPDATE SET status='pending',run_at=excluded.run_at,
                   attempt_count=0,last_error='',last_result='',next_attempt_at=NULL,
                   started_at=NULL,finished_at=NULL,updated_at=excluded.updated_at
               WHERE scheduled_tasks.status IN ('done','failed','cancelled')
                 AND COALESCE(scheduled_tasks.finished_at,'') <= ?""",
            (SYNC, f"{SYNC}:{teacher_id}", stamp, teacher_id, stamp, stamp, cutoff),
        )


def handle_daily(_task):
    with get_db_connection() as conn:
        ids = eligible_teachers(conn)
        enqueue(conn, ids)
        conn.commit()
    return ScheduledTaskResult(f"已安排 {len(ids)} 位教师的教务同步", daily_at(china_now(), following=True))


def handle_sync(task):
    teacher_id = int(task["owner_user_pk"])
    with get_db_connection() as conn:
        if not eligible_teachers(conn, {"role": "teacher", "id": teacher_id}):
            return "教务账号已停用"
    # The scheduler runs synchronous handlers in its executor. Keep even large
    # synchronous inbox/outbox transactions off the deadline and lease event loop.
    result = asyncio.run(sync_teacher_dashboard_reminders(teacher_id))
    if result["status"] != "success":
        # The scheduler retries with backoff; do not persist provider responses.
        raise RuntimeError(PARTIAL if result["status"] == "partial_success" else FAILED)
    return "本学期考试和监考已更新。"


def sync_state(conn, ids, role):
    if not ids:
        message = "请先在系统设置中绑定教务账号。" if role == "teacher" else "当前学期暂无已对接教务的课堂。"
        return {"status": "unavailable", "message": message, "updated_at": ""}
    marks = ",".join("?" for _ in ids)
    rows = [dict(r) for r in conn.execute(
        f"""SELECT status,last_error,finished_at,attempt_count FROM scheduled_tasks
            WHERE task_kind=? AND owner_role='teacher' AND owner_user_pk IN ({marks})""",
        (SYNC, *ids),
    ).fetchall()]
    updated = max((r["finished_at"] or "" for r in rows), default="")
    statuses = {r["status"] for r in rows}
    if "running" in statuses:
        status, message = "running", "正在从教务系统读取本学期安排…"
    elif "pending" in statuses:
        retrying = any(r["status"] == "pending" and r["attempt_count"] for r in rows)
        status, message = "pending", "教务暂时未响应，正在等待重试…" if retrying else "刷新已排队。"
    elif "failed" in statuses:
        partial = "done" in statuses or any(PARTIAL in (r["last_error"] or "") for r in rows)
        status, message = ("partial_success", PARTIAL) if partial else ("failed", FAILED)
        if role == "teacher":
            message += "请检查教务账号与学期设置后再试。"
    elif statuses == {"done"} and len(rows) == len(ids):
        status, message = "success", "本学期安排已更新。"
    else:
        status, message = "idle", ""
    return {"status": status, "message": message, "updated_at": updated}


def register_handlers():
    register_task_handler(DAILY, handle_daily)
    register_task_handler(SYNC, handle_sync)
