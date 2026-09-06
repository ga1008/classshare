"""Bounded, privacy-safe career queue metrics refreshed by local maintenance.

Health reads only a process-local copy. No schema checks, task registration,
payload parsing or DB connections occur on the read path. Full historical
success rates are deliberately not inferred from recent ledger samples.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from .student_career_job_service import registered_student_career_handlers

ACTIVE_STATUSES = ("queued", "running", "retry_wait", "result_ready")
KNOWN_STATUSES = frozenset((*ACTIVE_STATUSES, "succeeded", "dead_letter", "review_required", "cancelled", "superseded"))
ACTIVE_SAMPLE_LIMIT = 2500
RECENT_LEDGER_LIMIT = 1000
RECENT_ATTEMPT_LIMIT = 1000
STALE_AFTER_SECONDS = 180
# Error codes are an allowlist, not arbitrary exception messages/identifiers.
# This also prevents accidentally embedding a student ID in a custom error class.
KNOWN_ERRORS = frozenset({
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "PoolTimeout", "WriteTimeout",
    "HTTPStatusError", "HTTPException", "ValueError", "RuntimeError", "TypeError",
    "OperationalError", "InterfaceError", "ConnectionError", "ConnectError",
    "ReadError", "WriteError", "RemoteProtocolError", "FileNotFoundError",
    "PermissionError", "OSError", "SupersededCareerJob", "BrokenProcessPool",
    "timeout", "lease_expired", "delivery_failed", "cancelled_by_student", "input_changed",
})
_snapshot_lock = threading.Lock()
_refresh_lock = threading.Lock()
_snapshot: dict[str, Any] = {"available": False, "last_success_at": None, "last_refresh_error": "not_initialized"}
_last_success_monotonic: float | None = None


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # The existing durable ledger writes server-local naive timestamps.
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _duration_ms(start: Any, finish: Any) -> float | None:
    first, last = _timestamp(start), _timestamp(finish)
    if first is None or last is None or last < first:
        return None
    return (last-first).total_seconds()*1000


def _distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {"sample_count": len(ordered), "p50_ms": round(ordered[math.ceil(len(ordered)*.5)-1], 2) if ordered else None,
            "p95_ms": round(ordered[math.ceil(len(ordered)*.95)-1], 2) if ordered else None,
            "maximum_ms": round(ordered[-1], 2) if ordered else None}


def _error_code(value: Any) -> str:
    return str(value) if value in KNOWN_ERRORS else "other_error"


def _status(value: Any) -> str:
    return str(value) if value in KNOWN_STATUSES else "other_status"


def _sample_scope(rows, *, limit, scanned, truncated, timestamp_field="created_at"):
    stamps = [stamp for row in rows if (stamp := _timestamp(row.get(timestamp_field))) is not None]
    return {"ledger_sample_limit": limit, "ledger_rows_scanned": scanned, "career_rows_in_sample": len(rows),
            "ledger_sample_truncated": truncated, "sample_timestamp_field": timestamp_field,
            "oldest_timestamp_in_career_sample": _iso(min(stamps)) if stamps else None,
            "newest_timestamp_in_career_sample": _iso(max(stamps)) if stamps else None}


def collect_career_job_metrics(conn, *, task_types=None, now=None, active_limit=ACTIVE_SAMPLE_LIMIT,
                               recent_limit=RECENT_LEDGER_LIMIT, attempt_limit=RECENT_ATTEMPT_LIMIT):
    """Pure bounded SELECTs; callers provide a dedicated consistent read transaction.

    Active reads use idx_ai_jobs_type_status_created. Each next read is capped by
    the remaining global sample budget. Recent samples read the PK tail before
    filtering by career type, so rare career tasks never cause a historical scan.
    """
    started = time.perf_counter()
    kinds = tuple(sorted(set(task_types if task_types is not None else registered_student_career_handlers())))
    active_limit = max(1, min(ACTIVE_SAMPLE_LIMIT, int(active_limit)))
    recent_limit = max(1, min(RECENT_LEDGER_LIMIT, int(recent_limit)))
    attempt_limit = max(1, min(RECENT_ATTEMPT_LIMIT, int(attempt_limit)))
    observed_at = now or datetime.now(timezone.utc)
    active = []; active_truncated = False; sql_count = 0
    for kind in kinds:
        for status in ACTIVE_STATUSES:
            remaining = active_limit-len(active)
            rows = conn.execute("""SELECT task_type,status,attempt_count,created_at,available_at,started_at
                FROM ai_jobs WHERE task_type=? AND status=? ORDER BY created_at,id LIMIT ?""",
                (kind,status,remaining+1)).fetchall()
            sql_count += 1
            active.extend(dict(row) for row in rows[:remaining])
            if len(rows)>remaining:
                active_truncated = True
                break
        if active_truncated:
            break
    recent = []; recent_attempts = []; scanned = 0; attempts_scanned = 0
    recent_truncated = False; attempts_truncated = False
    if kinds:
        recent_rows = conn.execute("""SELECT task_type,status,attempt_count,created_at,started_at,finished_at,last_error_code
            FROM ai_jobs ORDER BY id DESC LIMIT ?""", (recent_limit+1,)).fetchall()
        recent_truncated = len(recent_rows)>recent_limit; scanned = min(len(recent_rows),recent_limit)
        recent = [dict(row) for row in recent_rows[:recent_limit] if row["task_type"] in kinds]
        attempt_rows = conn.execute("""WITH recent AS (
            SELECT id,job_id,stage,status,attempt_no,started_at,finished_at,error_code
            FROM ai_job_attempts ORDER BY id DESC LIMIT ?)
            SELECT j.task_type,a.stage,a.status,a.attempt_no,a.started_at,a.finished_at,a.error_code
            FROM recent a JOIN ai_jobs j ON j.id=a.job_id ORDER BY a.id DESC""", (attempt_limit+1,)).fetchall()
        attempts_truncated = len(attempt_rows)>attempt_limit; attempts_scanned = min(len(attempt_rows),attempt_limit)
        recent_attempts = [dict(row) for row in attempt_rows[:attempt_limit] if row["task_type"] in kinds]
        sql_count += 2
    type_counts = {kind: {status: 0 for status in ACTIVE_STATUSES} for kind in kinds}
    active_counts = Counter(); active_retried = 0
    waiting = []
    for row in active:
        type_counts[row["task_type"]][row["status"]] += 1; active_counts[row["status"]] += 1
        active_retried += int(row["attempt_count"] or 0)>1
        if row["status"] in ("queued","retry_wait") and (stamp := _timestamp(row["created_at"])):
            waiting.append(stamp)
    oldest = min(waiting) if waiting else None
    errors = Counter(_error_code(row["error_code"]) for row in recent_attempts if row["error_code"])
    job_errors = Counter(_error_code(row["last_error_code"]) for row in recent if row["last_error_code"])
    execute_durations = [duration for row in recent_attempts if row["stage"]=="execute"
                         and (duration := _duration_ms(row["started_at"],row["finished_at"])) is not None]
    first_start_wait = [duration for row in recent if (duration := _duration_ms(row["created_at"],row["started_at"])) is not None]
    return {
        "available": True, "last_success_at": _iso(observed_at), "last_refresh_error": "",
        "active": {"scope": "registered career task types; one consistent read transaction",
                   "sample_limit": active_limit, "sample_count": len(active), "truncated": active_truncated,
                   "counts_are_lower_bounds": active_truncated, "counts_by_type": type_counts,
                   "counts_by_status": {status: active_counts[status] for status in ACTIVE_STATUSES},
                   "oldest_waiting_created_at_in_sample": _iso(oldest),
                   "oldest_waiting_age_seconds_in_sample": round(max(0,(observed_at.astimezone(timezone.utc)-oldest).total_seconds()),2) if oldest else None,
                   "waiting_scope": "queued/retry_wait; age since original admission, includes retry delays",
                   "retried_jobs_in_sample": active_retried},
        "recent_jobs": {**_sample_scope(recent,limit=recent_limit,scanned=scanned,truncated=recent_truncated),
                        "scope": "career rows within latest all-domain job IDs; not a time window or all historical completions",
                        "counts_by_status": dict(Counter(_status(row["status"]) for row in recent)),
                        "counts_by_type": dict(Counter(row["task_type"] for row in recent)),
                        "last_error_codes_in_sample": dict(job_errors),
                        "retried_jobs_in_sample": sum(int(row["attempt_count"] or 0)>1 for row in recent),
                        "first_start_queue_delay": {**_distribution(first_start_wait), "scope": "created_at to first started_at, started jobs in recent job sample"}},
        "recent_attempts": {**_sample_scope(recent_attempts,limit=attempt_limit,scanned=attempts_scanned,truncated=attempts_truncated,timestamp_field="started_at"),
                            "scope": "career rows within latest all-domain attempt IDs; failed/retried attempts included",
                            "error_codes_in_sample": dict(errors),
                            "retry_execute_attempts_in_sample": sum(row["stage"]=="execute" and int(row["attempt_no"] or 0)>1 for row in recent_attempts),
                            "execute_duration": {**_distribution(execute_durations), "scope": "finished execute attempts only; excludes open attempts and apply stage; includes failed attempts"}},
        "refresh": {"sql_statements": sql_count, "sample_rows_upper_bound": active_limit+recent_limit+attempt_limit+3,
                    "collection_ms": round((time.perf_counter()-started)*1000,2), "target_interval_seconds": 60},
    }


def refresh_career_job_metrics(*, connection_factory=None) -> dict[str, Any]:
    """Maintenance hook: threadpool only, safe to call even after recovery fails."""
    global _snapshot, _last_success_monotonic
    if not _refresh_lock.acquire(blocking=False):
        return career_job_metrics_snapshot()
    try:
        with (connection_factory or get_db_connection)() as conn:
            if get_configured_db_engine()=="postgres":
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '2000ms'")
            elif not conn.in_transaction:
                conn.execute("BEGIN")
            fresh = collect_career_job_metrics(conn)
        with _snapshot_lock:
            _snapshot = fresh
            _last_success_monotonic = time.monotonic()
    except Exception:
        # Keep last good aggregate data, never expose a DB/provider error body.
        with _snapshot_lock:
            _snapshot = {**_snapshot, "last_refresh_error": "metrics_refresh_failed"}
    finally:
        _refresh_lock.release()
    return career_job_metrics_snapshot()


def career_job_metrics_snapshot() -> dict[str, Any]:
    """O(aggregate size) in-memory health read, with monotonic freshness tracking."""
    with _snapshot_lock:
        result = copy.deepcopy(_snapshot)
        age = max(0,time.monotonic()-_last_success_monotonic) if _last_success_monotonic is not None else None
    result.update(age_seconds=round(age,2) if age is not None else None,
                  stale=age is None or age>STALE_AFTER_SECONDS or bool(result.get("last_refresh_error")),
                  process_local=True)
    return result
