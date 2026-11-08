"""Unified scheduled-task scheduler.

A single generic timer used across the app. Callers persist a task with a
``run_at`` and a ``task_kind`` (mapped to a registered handler); a dedicated
worker container claims due tasks and dispatches them. Handlers are intentionally
small and fast — the scheduler decides *when*, and delegates the *what* to the
appropriate existing pipeline (e.g. enqueue an email into ``email_outbox``).

Design goals:
- Runs in its own worker process, never on the main web request path.
- One-shot and recurring tasks share one table and one claim/retry loop.
- Atomic claim works on both SQLite and PostgreSQL (``FOR UPDATE SKIP LOCKED``).
- Handlers may be sync or async; failures retry with exponential backoff.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from ..db.schema_scheduler import ensure_scheduler_schema

TaskHandler = Callable[[dict[str, Any]], "Any | Awaitable[Any]"]

_HANDLERS: dict[str, TaskHandler] = {}

SCHEDULER_POLL_SECONDS = max(5, int(os.getenv("SCHEDULER_POLL_SECONDS", "5")))
SCHEDULER_BATCH_SIZE = max(1, int(os.getenv("SCHEDULER_BATCH_SIZE", "20")))
SCHEDULER_STALE_MINUTES = max(1, int(os.getenv("SCHEDULER_STALE_MINUTES", "15")))
SCHEDULER_HEARTBEAT_TIMEOUT_SECONDS = max(
    60, int(os.getenv("SCHEDULER_HEARTBEAT_TIMEOUT_SECONDS", "180"))
)
MAX_BACKOFF_SECONDS = 3600
SCHEDULER_LEASE_HEARTBEAT_SECONDS = min(30.0, SCHEDULER_STALE_MINUTES * 20.0)
SCHEDULER_FAIR_WAIT_SECONDS = 300
# Each lane has one execution slot. Slow autonomous/AI work cannot reserve
# reminders or the normal projection slot while waiting for a supplier.
DEADLINE_TASK_KINDS = (
    "exam_email_reminder", "todo_due_reminder", "assignment_due_reminder",
    "signature_request_reminder", "approval_request_reminder", "late_window_expiry",
    "late_window_reminder", "assignment_close_sweep", "resubmission_window_expiry",
    "mp_deadline_reminder_scan", "mp_subscribe_dispatch",
    "login_attendance_scan",
)
ELASTIC_TASK_KINDS = (
    "stage_exam_generation", "session_material_generate", "personal_greeting_generate",
    "life_tip_gongwen_refresh", "blog_mention_reply", "blog_content_review",
    "material_search_index", "gongwen_incremental_sync", "gongwen_parse_pending",
    "gongwen_follow_scan", "gongwen_follow_rescan", "cultivation_weekly_snapshot",
    "cultivation_weekly_report", "cultivation_score_event_archive",
)


def register_task_handler(task_kind: str, handler: TaskHandler) -> None:
    """Register a handler for a task kind. Idempotent (last registration wins)."""
    normalized = str(task_kind or "").strip()
    if not normalized:
        raise ValueError("task_kind is required")
    _HANDLERS[normalized] = handler


def get_registered_task_kinds() -> list[str]:
    return sorted(_HANDLERS.keys())


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _to_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def schedule_task(
    conn,
    *,
    task_kind: str,
    run_at: datetime | str,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    recurrence_seconds: int | None = None,
    owner_role: str = "",
    owner_user_pk: int | None = None,
    title: str = "",
    priority: int = 100,
    max_attempts: int = 5,
    replace: bool = True,
) -> int:
    """Persist a scheduled task. When ``dedupe_key`` is set and a task already
    exists, ``replace`` updates it back to pending with the new ``run_at`` /
    payload (so re-arming a reminder is idempotent). Returns the task id."""
    ensure_scheduler_schema(conn)
    normalized_kind = str(task_kind or "").strip()
    if not normalized_kind:
        raise ValueError("task_kind is required")
    run_at_iso = run_at if isinstance(run_at, str) else _to_iso(run_at)
    payload_json = _json_dumps(payload or {})
    now = _now_iso()
    key = (dedupe_key or "").strip() or None

    if key is not None:
        existing = conn.execute(
            "SELECT id FROM scheduled_tasks WHERE dedupe_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if existing is not None:
            if not replace:
                return int(existing["id"])
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET task_kind = ?, title = ?, status = 'pending', priority = ?,
                    run_at = ?, recurrence_seconds = ?, payload_json = ?,
                    owner_role = ?, owner_user_pk = ?, attempt_count = 0,
                    max_attempts = ?, next_attempt_at = NULL, locked_at = NULL,
                    locked_by = '', last_error = '', last_result = '',
                    started_at = NULL, finished_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_kind, title, int(priority), run_at_iso,
                    recurrence_seconds, payload_json, owner_role, owner_user_pk,
                    int(max_attempts), now, int(existing["id"]),
                ),
            )
            return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO scheduled_tasks (
            task_kind, dedupe_key, title, status, priority, run_at,
            recurrence_seconds, payload_json, owner_role, owner_user_pk,
            attempt_count, max_attempts, created_at, updated_at
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            normalized_kind, key, title, int(priority), run_at_iso,
            recurrence_seconds, payload_json, owner_role, owner_user_pk,
            int(max_attempts), now, now,
        ),
    )
    last_id = getattr(cursor, "lastrowid", None)
    if last_id:
        return int(last_id)
    row = conn.execute(
        "SELECT id FROM scheduled_tasks WHERE dedupe_key = ? ORDER BY id DESC LIMIT 1"
        if key is not None
        else "SELECT id FROM scheduled_tasks ORDER BY id DESC LIMIT 1",
        (key,) if key is not None else (),
    ).fetchone()
    return int(row["id"]) if row else 0


def cancel_tasks_by_dedupe(conn, dedupe_key: str) -> int:
    ensure_scheduler_schema(conn)
    key = (dedupe_key or "").strip()
    if not key:
        return 0
    cursor = conn.execute(
        """
        UPDATE scheduled_tasks
        SET status = 'cancelled', updated_at = ?
        WHERE dedupe_key = ? AND status IN ('pending', 'running')
        """,
        (_now_iso(), key),
    )
    return int(cursor.rowcount or 0)


def get_owner_task_by_dedupe(conn, dedupe_key: str) -> dict[str, Any] | None:
    ensure_scheduler_schema(conn)
    key = (dedupe_key or "").strip()
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM scheduled_tasks WHERE dedupe_key = ? LIMIT 1",
        (key,),
    ).fetchone()
    return dict(row) if row else None


def _claim_due_tasks(limit: int, *, lane: str | None = None) -> list[dict[str, Any]]:
    now = _now_iso()
    stale_cutoff = _to_iso(_now() - timedelta(minutes=SCHEDULER_STALE_MINUTES))
    worker_tag = (os.getenv("SCHEDULER_WORKER_ID") or socket.gethostname()) + ":" + uuid.uuid4().hex
    engine = get_configured_db_engine()
    safe_limit = max(1, min(int(limit), 100))
    lane_sql = ""
    lane_params = ()
    if lane is not None:
        kinds = ",".join("?" for _ in DEADLINE_TASK_KINDS)
        lane_params = DEADLINE_TASK_KINDS
        if lane == "deadline":
            lane_sql = f" AND task_kind IN ({kinds})"
        elif lane in {"normal", "elastic"}:
            elastic_kinds = ",".join("?" for _ in ELASTIC_TASK_KINDS)
            heavy = f"(task_kind IN ({elastic_kinds}) OR task_kind LIKE 'wanderer_%')"
            classification = f"priority < 100 AND NOT {heavy}" if lane == "normal" else f"(priority >= 100 OR {heavy})"
            lane_sql = f" AND task_kind NOT IN ({kinds}) AND ({classification})"
            lane_params = (*DEADLINE_TASK_KINDS, *ELASTIC_TASK_KINDS)
        else:
            raise ValueError("Unknown scheduler lane")
    fair_before = _to_iso(_now() - timedelta(seconds=SCHEDULER_FAIR_WAIT_SECONDS))
    with get_db_connection() as conn:
        ensure_scheduler_schema(conn)
        if engine == "postgres":
            rows = conn.execute(
                f"""
                UPDATE scheduled_tasks
                SET status = 'running', locked_at = ?, locked_by = ?,
                    started_at = ?, updated_at = ?
                WHERE id IN (
                    SELECT id FROM scheduled_tasks
                    WHERE ((status = 'pending' AND run_at <= ?)
                       OR (status = 'running' AND (locked_at IS NULL OR COALESCE(NULLIF(updated_at, ''), locked_at) <= ?)))
                    {lane_sql}
                    ORDER BY CASE WHEN run_at <= ? THEN 0 ELSE 1 END, priority ASC, run_at ASC, id ASC
                    LIMIT ?
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (now, worker_tag, now, now, now, stale_cutoff, *lane_params, fair_before, safe_limit),
            ).fetchall()
            conn.commit()
            return [dict(row) for row in rows]

        rows = conn.execute(
            f"""
            SELECT * FROM scheduled_tasks
            WHERE ((status = 'pending' AND run_at <= ?)
               OR (status = 'running' AND (locked_at IS NULL OR COALESCE(NULLIF(updated_at, ''), locked_at) <= ?)))
            {lane_sql}
            ORDER BY CASE WHEN run_at <= ? THEN 0 ELSE 1 END, priority ASC, run_at ASC, id ASC
            LIMIT ?
            """,
            (now, stale_cutoff, *lane_params, fair_before, safe_limit),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'running', locked_at = ?, locked_by = ?,
                    started_at = ?, updated_at = ?
                WHERE id = ?
                  AND (
                    (status = 'pending' AND run_at <= ?)
                    OR (status = 'running' AND (locked_at IS NULL OR COALESCE(NULLIF(updated_at, ''), locked_at) <= ?))
                  )
                """,
                (now, worker_tag, now, now, int(row["id"]), now, stale_cutoff),
            )
            if cursor.rowcount:
                claimed.append({**dict(row), "status": "running", "locked_at": now, "locked_by": worker_tag, "started_at": now, "updated_at": now})
        conn.commit()
        return claimed


async def _dispatch_task(task: dict[str, Any]) -> Any:
    handler = _HANDLERS.get(str(task.get("task_kind") or "").strip())
    if handler is None:
        raise LookupError(f"No handler registered for task_kind={task.get('task_kind')!r}")
    payload = _json_loads(task.get("payload_json"))
    task_with_payload = {**task, "payload": payload}
    if inspect.iscoroutinefunction(handler):
        result = handler(task_with_payload)
    else:
        result = await asyncio.to_thread(handler, task_with_payload)
    if inspect.isawaitable(result):
        result = await result
    return result


def _renew_task_lease(task: dict[str, Any]) -> bool:
    with get_db_connection() as conn:
        changed = conn.execute(
            """UPDATE scheduled_tasks SET updated_at = ?
               WHERE id=? AND status='running' AND locked_by=? AND locked_at=?""",
            (_now_iso(), int(task["id"]), task.get("locked_by"), task.get("locked_at")),
        ).rowcount
    return changed == 1


async def _task_lease_heartbeat(task, stopped):
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=SCHEDULER_LEASE_HEARTBEAT_SECONDS)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if not await asyncio.to_thread(_renew_task_lease, task):
                return
        except Exception as exc:
            # Do not release work whose synchronous handler is still executing.
            # Completion is fenced by the immutable claim token even after a
            # failed renewal or an operator cancellation/replacement.
            print(f"[SCHEDULER] lease renewal deferred task={task['id']}: {type(exc).__name__}")


async def _await_owned_work(work: asyncio.Task, *, operation: str):
    """Observe real work through repeated caller cancellation, then propagate it.

    Cancelling an executor Future cannot stop its SQL/thread. Ownership spans
    claim, handler and finalization; never leave one of those running unseen.
    An internally cancelled task is already settled and must not spin forever.
    """
    cancelled = False
    while not work.done():
        try:
            # wait() cancels only its waiter, never the owned task. Unlike
            # repeatedly shielding a failed Future on Python 3.14, it does not
            # report an error before this owner retrieves the final exception.
            await asyncio.wait((work,))
        except asyncio.CancelledError:
            cancelled = True
    try:
        result = work.result()
    except Exception as exc:
        if cancelled:
            # An unknown claim/commit result cannot be safely invented or
            # returned to pending. Preserve the diagnostic and stale fence.
            print(f"[SCHEDULER] cancelled {operation} ended with {type(exc).__name__}")
            raise asyncio.CancelledError from exc
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _execute_claimed_task(task: dict[str, Any]) -> str:
    stopped = asyncio.Event()
    heartbeat = asyncio.create_task(_task_lease_heartbeat(task, stopped))
    try:
        try:
            result = await _dispatch_task(task)
        except Exception as exc:
            await asyncio.to_thread(_mark_failure, task, f"{type(exc).__name__}: {exc}")
            outcome = "failed" if int(task.get("attempt_count") or 0) + 1 >= int(task.get("max_attempts") or 5) else "retry"
        else:
            await asyncio.to_thread(_mark_success, task, result)
            outcome = "rescheduled" if task.get("recurrence_seconds") else "done"
    finally:
        stopped.set()
        await heartbeat
    return outcome


async def _run_claimed_task(task: dict[str, Any]) -> str:
    execution = asyncio.create_task(_execute_claimed_task(task))
    return await _await_owned_work(execution, operation=f"task {task['id']}")


async def _claim_and_run_due_task(*, lane: str | None) -> str | None:
    tasks = await asyncio.to_thread(_claim_due_tasks, 1, **({"lane": lane} if lane is not None else {}))
    if not tasks:
        return None
    return await _run_claimed_task(tasks[0])


def _mark_success(task: dict[str, Any], result: Any) -> None:
    now = _now_iso()
    result_text = (result if isinstance(result, str) else _json_dumps(result))[:480]
    recurrence = task.get("recurrence_seconds")
    with get_db_connection() as conn:
        if recurrence:
            next_run = _to_iso(_now() + timedelta(seconds=int(recurrence)))
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'pending', run_at = ?, attempt_count = 0,
                    locked_at = NULL, locked_by = '', last_error = '',
                    last_result = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND locked_by = ? AND locked_at = ?
                """,
                (next_run, result_text, now, now, int(task["id"]), task.get("locked_by"), task.get("locked_at")),
            )
        else:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'done', locked_at = NULL, locked_by = '',
                    last_error = '', last_result = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND locked_by = ? AND locked_at = ?
                """,
                (result_text, now, now, int(task["id"]), task.get("locked_by"), task.get("locked_at")),
            )
        conn.commit()


def _mark_failure(task: dict[str, Any], error: str) -> None:
    now = _now_iso()
    attempt_count = int(task.get("attempt_count") or 0) + 1
    max_attempts = int(task.get("max_attempts") or 5)
    final = attempt_count >= max_attempts
    with get_db_connection() as conn:
        if final:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'failed', attempt_count = ?, locked_at = NULL,
                    locked_by = '', last_error = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND locked_by = ? AND locked_at = ?
                """,
                (attempt_count, error[:480], now, now, int(task["id"]), task.get("locked_by"), task.get("locked_at")),
            )
        else:
            backoff = min(MAX_BACKOFF_SECONDS, 60 * (2 ** max(attempt_count - 1, 0)))
            next_run = _to_iso(_now() + timedelta(seconds=backoff))
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'pending', attempt_count = ?, run_at = ?,
                    locked_at = NULL, locked_by = '', last_error = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND locked_by = ? AND locked_at = ?
                """,
                (attempt_count, next_run, error[:480], now, int(task["id"]), task.get("locked_by"), task.get("locked_at")),
            )
        conn.commit()


async def process_due_scheduled_tasks_once(limit: int = SCHEDULER_BATCH_SIZE, *, lane: str | None = None) -> dict[str, int]:
    result = {"claimed": 0, "done": 0, "rescheduled": 0, "failed": 0, "retry": 0}
    for _ in range(max(1, min(int(limit), 100))):
        owned_slot = asyncio.create_task(_claim_and_run_due_task(lane=lane))
        outcome = await _await_owned_work(owned_slot, operation=f"{lane or 'all'} claim slot")
        if outcome is None:
            break
        result["claimed"] += 1
        result[outcome] += 1
    return result


def update_scheduler_heartbeat(worker_id: str, *, status: str, last_error: str = "") -> None:
    with get_db_connection() as conn:
        ensure_scheduler_schema(conn)
        queue_depth = 0
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM scheduled_tasks WHERE status IN ('pending', 'running')"
        ).fetchone()
        if row is not None:
            queue_depth = int((row["c"] if "c" in row.keys() else row[0]) or 0)
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO scheduled_task_worker_heartbeats (worker_id, status, queue_depth, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                status = excluded.status,
                queue_depth = excluded.queue_depth,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (worker_id, status, queue_depth, last_error[:480], now),
        )
        conn.commit()


def scheduler_health_snapshot() -> dict[str, Any]:
    with get_db_connection() as conn:
        ensure_scheduler_schema(conn)
        row = conn.execute(
            "SELECT * FROM scheduled_task_worker_heartbeats ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        depth_row = conn.execute(
            "SELECT COUNT(*) AS c FROM scheduled_tasks WHERE status IN ('pending', 'running')"
        ).fetchone()
        queue_depth = int((depth_row["c"] if depth_row else 0) or 0)
    if not row:
        return {"ok": False, "queue_depth": queue_depth, "status": "missing", "updated_at": "", "last_error": ""}
    try:
        updated_at = datetime.fromisoformat(str(row["updated_at"]))
        ok = (datetime.now() - updated_at).total_seconds() <= SCHEDULER_HEARTBEAT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        ok = False
    return {
        "ok": ok,
        "queue_depth": queue_depth,
        "status": str(row["status"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "last_error": str(row["last_error"] or ""),
    }


async def run_scheduler_worker_forever(worker_id: str = "scheduler", poll_seconds: int | None = None) -> None:
    # Import handlers lazily so every kind is registered before the loop starts.
    from . import scheduled_task_handlers  # noqa: F401

    interval = max(5, int(poll_seconds or SCHEDULER_POLL_SECONDS))
    print(f"[SCHEDULER] worker {worker_id} started (kinds={get_registered_task_kinds()})")
    async def heartbeat_loop():
        while True:
            try:
                await asyncio.to_thread(update_scheduler_heartbeat, worker_id, status="running")
            except Exception as exc:
                print(f"[SCHEDULER] worker heartbeat deferred: {type(exc).__name__}")
            # One awaited maintenance call at a time, after the worker beat.
            # SQL lock/statement budgets live in this narrow handler; task lease
            # heartbeats continue independently while it uses its own thread.
            maintenance = asyncio.create_task(asyncio.to_thread(scheduled_task_handlers.maintain_cultivation_projection_task))
            try:
                await asyncio.shield(maintenance)
            except asyncio.CancelledError:
                await asyncio.gather(maintenance, return_exceptions=True)
                raise
            except Exception as exc:
                print(f"[SCHEDULER] projection timer maintenance deferred: {type(exc).__name__}")
            await asyncio.sleep(min(30, interval))

    async def lane_loop(lane):
        while True:
            try:
                result = await process_due_scheduled_tasks_once(limit=1, lane=lane)
            except Exception as exc:
                print(f"[SCHEDULER] {lane} loop deferred: {type(exc).__name__}")
                await asyncio.sleep(interval)
                continue
            # Drain ready work through each single slot. Empty lanes back off.
            await asyncio.sleep(0.1 if result["claimed"] else interval)

    tasks = [asyncio.create_task(heartbeat_loop())]
    tasks.extend(asyncio.create_task(lane_loop(lane)) for lane in ("deadline", "normal", "elastic"))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
