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
import json
import os
import socket
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from ..db.schema_scheduler import ensure_scheduler_schema

TaskHandler = Callable[[dict[str, Any]], "Any | Awaitable[Any]"]

_HANDLERS: dict[str, TaskHandler] = {}

SCHEDULER_POLL_SECONDS = max(5, int(os.getenv("SCHEDULER_POLL_SECONDS", "20")))
SCHEDULER_BATCH_SIZE = max(1, int(os.getenv("SCHEDULER_BATCH_SIZE", "20")))
SCHEDULER_STALE_MINUTES = max(1, int(os.getenv("SCHEDULER_STALE_MINUTES", "15")))
SCHEDULER_HEARTBEAT_TIMEOUT_SECONDS = max(
    60, int(os.getenv("SCHEDULER_HEARTBEAT_TIMEOUT_SECONDS", "180"))
)
MAX_BACKOFF_SECONDS = 3600


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


def _claim_due_tasks(limit: int) -> list[dict[str, Any]]:
    now = _now_iso()
    stale_cutoff = _to_iso(_now() - timedelta(minutes=SCHEDULER_STALE_MINUTES))
    worker_tag = os.getenv("SCHEDULER_WORKER_ID") or socket.gethostname()
    engine = get_configured_db_engine()
    safe_limit = max(1, min(int(limit), 100))
    with get_db_connection() as conn:
        ensure_scheduler_schema(conn)
        if engine == "postgres":
            rows = conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'running', locked_at = ?, locked_by = ?,
                    started_at = ?, updated_at = ?
                WHERE id IN (
                    SELECT id FROM scheduled_tasks
                    WHERE (status = 'pending' AND run_at <= ?)
                       OR (status = 'running' AND (locked_at IS NULL OR locked_at <= ?))
                    ORDER BY priority ASC, run_at ASC, id ASC
                    LIMIT ?
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (now, worker_tag, now, now, now, stale_cutoff, safe_limit),
            ).fetchall()
            conn.commit()
            return [dict(row) for row in rows]

        rows = conn.execute(
            """
            SELECT * FROM scheduled_tasks
            WHERE (status = 'pending' AND run_at <= ?)
               OR (status = 'running' AND (locked_at IS NULL OR locked_at <= ?))
            ORDER BY priority ASC, run_at ASC, id ASC
            LIMIT ?
            """,
            (now, stale_cutoff, safe_limit),
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
                    OR (status = 'running' AND (locked_at IS NULL OR locked_at <= ?))
                  )
                """,
                (now, worker_tag, now, now, int(row["id"]), now, stale_cutoff),
            )
            if cursor.rowcount:
                claimed.append(dict(row))
        conn.commit()
        return claimed


async def _dispatch_task(task: dict[str, Any]) -> Any:
    handler = _HANDLERS.get(str(task.get("task_kind") or "").strip())
    if handler is None:
        raise LookupError(f"No handler registered for task_kind={task.get('task_kind')!r}")
    payload = _json_loads(task.get("payload_json"))
    task_with_payload = {**task, "payload": payload}
    result = handler(task_with_payload)
    if asyncio.iscoroutine(result):
        result = await result
    return result


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
                WHERE id = ?
                """,
                (next_run, result_text, now, now, int(task["id"])),
            )
        else:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'done', locked_at = NULL, locked_by = '',
                    last_error = '', last_result = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (result_text, now, now, int(task["id"])),
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
                WHERE id = ?
                """,
                (attempt_count, error[:480], now, now, int(task["id"])),
            )
        else:
            backoff = min(MAX_BACKOFF_SECONDS, 60 * (2 ** max(attempt_count - 1, 0)))
            next_run = _to_iso(_now() + timedelta(seconds=backoff))
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'pending', attempt_count = ?, run_at = ?,
                    locked_at = NULL, locked_by = '', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (attempt_count, next_run, error[:480], now, int(task["id"])),
            )
        conn.commit()


async def process_due_scheduled_tasks_once(limit: int = SCHEDULER_BATCH_SIZE) -> dict[str, int]:
    tasks = _claim_due_tasks(limit)
    result = {"claimed": len(tasks), "done": 0, "rescheduled": 0, "failed": 0, "retry": 0}
    for task in tasks:
        try:
            handler_result = await _dispatch_task(task)
        except Exception as exc:  # noqa: BLE001 - a failing task must not kill the loop
            attempt_count = int(task.get("attempt_count") or 0) + 1
            final = attempt_count >= int(task.get("max_attempts") or 5)
            _mark_failure(task, f"{type(exc).__name__}: {exc}")
            result["failed" if final else "retry"] += 1
            continue
        _mark_success(task, handler_result)
        result["rescheduled" if task.get("recurrence_seconds") else "done"] += 1
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
    update_scheduler_heartbeat(worker_id, status="running")
    while True:
        try:
            result = await process_due_scheduled_tasks_once()
            if result.get("claimed"):
                print(f"[SCHEDULER] processed batch: {result}")
            update_scheduler_heartbeat(worker_id, status="running")
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            print(f"[SCHEDULER] worker loop failed: {error}")
            try:
                update_scheduler_heartbeat(worker_id, status="error", last_error=error)
            except Exception:
                pass
        time.sleep(interval)
