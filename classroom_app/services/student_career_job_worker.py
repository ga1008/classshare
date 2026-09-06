"""Bounded career/resume workers using ai_jobs leases and result replay."""
from __future__ import annotations

import asyncio
import importlib
import os
import socket
from contextlib import suppress
from datetime import timedelta
from typing import Any

from ..config import CAREER_JOBS_ENABLED
from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from . import ai_durable_job_service as durable
from .career_job_metrics_service import refresh_career_job_metrics, career_job_metrics_snapshot
from .student_career_job_service import (
    SupersededCareerJob, UPSTREAM_COOLDOWN_SECONDS, career_concurrency_lock_key, registered_student_career_handlers,
)

AI_CONCURRENCY = max(1, min(2, int(os.getenv("CAREER_AI_CONCURRENCY", "1"))))
RENDER_CONCURRENCY = 1
POLL_SECONDS = max(1.0, float(os.getenv("CAREER_JOB_POLL_SECONDS", "2")))
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 15
MAX_DELIVERY_ATTEMPTS = 5
_tasks: list[asyncio.Task] = []
_stop: asyncio.Event | None = None
_last_error = ""
_last_maintenance = ""


def _load_domains() -> list[Any]:
    modules = []
    for name in (
        ".career_path_service", ".resume.resume_generation_service",
        ".resume.resume_import_service",
    ):
        modules.append(importlib.import_module(name, package=__package__))
    return modules


def _lock_job(conn, job: dict[str, Any], status: str, *, lock: bool = True) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock and get_configured_db_engine() == "postgres" else ""
    row = conn.execute(
        "SELECT * FROM ai_jobs WHERE id=? AND status=? AND lease_token=? AND lease_expires_at>?" + suffix,
        (int(job["id"]), status, str(job.get("lease_token") or ""), durable._iso()),
    ).fetchone()
    # A database/row-lock wait can outlive the lease checked by the query.
    return dict(row) if row and str(row["lease_expires_at"] or "") > durable._iso() else None


def _apply_result(job: dict[str, Any]) -> bool:
    """Business publication and job completion commit together, or neither does."""
    with get_db_connection() as conn:
        if get_configured_db_engine() == "sqlite":
            conn.execute("BEGIN IMMEDIATE")
        # Domain commands lock their business row before invalidating a job.
        # Follow that order: optimistic lease read, domain CAS, final job CAS.
        # A lease change at any point rolls the entire business write back.
        current = _lock_job(conn, job, "result_ready", lock=False)
        if not current:
            return False
        result = conn.execute("SELECT result_json FROM ai_job_results WHERE id=? AND job_id=?", (current["result_id"], current["id"])).fetchone()
        if not result:
            raise ValueError("Persisted career result is missing")
        handler = registered_student_career_handlers()[current["task_type"]]
        conn.execute("SAVEPOINT career_result_application")
        applied = handler.apply(conn, current, durable.load_ai_job_payload(current), durable._json_loads(result["result_json"]))
        if not applied:
            conn.execute("ROLLBACK TO SAVEPOINT career_result_application")
        conn.execute("RELEASE SAVEPOINT career_result_application")
        status = "succeeded" if applied else "superseded"
        if not _lock_job(conn, current, "result_ready"):
            conn.rollback()
            return False
        now = durable._iso()
        updated = conn.execute(
            "UPDATE ai_jobs SET status=?,locked_at=NULL,locked_by='',lease_token='',"
            "lease_expires_at=NULL,heartbeat_at=NULL,updated_at=?,finished_at=?,last_error='',last_error_code='' "
            "WHERE id=? AND status='result_ready' AND lease_token=? AND result_id=? AND lease_expires_at>?",
            (status, now, now, current["id"], current["lease_token"], current["result_id"], now),
        )
        if updated.rowcount != 1:
            conn.rollback()
            return False
        conn.execute("UPDATE ai_job_results SET status=? WHERE id=?", (status, current["result_id"]))
        durable.record_ai_job_attempt_finished(conn, current, stage="apply", status=status)
        conn.commit()
        return bool(applied)


def _safe_failure(exc: Exception) -> tuple[str, str, bool]:
    status_code = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    code = type(exc).__name__
    # Technical detail stays in the private task ledger, never the public API.
    message = str(exc).strip() or code
    permanent = status_code in {400, 401, 403, 404, 413, 415, 422}
    return code, message[:1200], permanent


def _fail_job(job: dict[str, Any], exc: Exception, *, delivery: bool = False) -> str:
    code, message, permanent = _safe_failure(exc)
    with get_db_connection() as conn:
        if get_configured_db_engine() == "sqlite":
            conn.execute("BEGIN IMMEDIATE")
        expected_status = "result_ready" if delivery else "running"
        current = _lock_job(conn, job, expected_status, lock=False)
        if not current:
            return "superseded"
        cancelled = isinstance(exc, SupersededCareerJob)
        exhausted = int(current.get("delivery_attempt_count") or 0) >= MAX_DELIVERY_ATTEMPTS if delivery else int(current["attempt_count"]) >= int(current["max_attempts"])
        terminal = permanent or exhausted or cancelled
        status = "superseded" if cancelled else ("dead_letter" if terminal else ("result_ready" if delivery else "retry_wait"))
        attempt = int(current.get("delivery_attempt_count") or 1) if delivery else int(current["attempt_count"])
        delay = (15, 60, 180)[min(max(0, attempt - 1), 2)] + int(current["id"]) % 7
        # Timeout cancellation is local. Reserve a conservative cooling period
        # before retrying a provider that may still be computing upstream.
        upstream_uncertain = not delivery and (isinstance(exc, TimeoutError) or "Timeout" in code)
        if upstream_uncertain:
            delay = max(delay, UPSTREAM_COOLDOWN_SECONDS)
        response = getattr(exc, "response", None)
        try:
            headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
            delay = max(delay, min(3600, int(headers.get("Retry-After", "0"))),
                        min(3600, int(getattr(exc, "retry_after", 0))))
        except (TypeError, ValueError):
            pass
        handler = registered_student_career_handlers().get(current["task_type"])
        if terminal and not cancelled and handler and handler.fail:
            handler.fail(conn, current, durable.load_ai_job_payload(current), code, message)
        if not _lock_job(conn, current, expected_status):
            conn.rollback()
            return "superseded"
        now = durable._iso()
        available = durable._iso(durable._now() + timedelta(seconds=delay))
        capacity_hold = durable._iso(durable._now() + timedelta(seconds=UPSTREAM_COOLDOWN_SECONDS)) if upstream_uncertain else current.get("capacity_reserved_until")
        updated = conn.execute(
            "UPDATE ai_jobs SET status=?,available_at=?,locked_at=NULL,locked_by='',lease_token='',"
            "lease_expires_at=NULL,heartbeat_at=NULL,last_error_code=?,last_error=?,updated_at=?,finished_at=?,capacity_reserved_until=? "
            "WHERE id=? AND status=? AND lease_token=? AND lease_expires_at>?",
            (status, available, code, message, now, now if terminal else None, capacity_hold, current["id"], expected_status, current["lease_token"], now),
        )
        if updated.rowcount != 1:
            conn.rollback()
            return "superseded"
        durable.record_ai_job_attempt_finished(conn, current, stage="apply" if delivery else "execute", status="superseded" if cancelled else "error", error_code=code, error_message=message)
        conn.commit()
        return status


async def _heartbeat(job: dict[str, Any], done: asyncio.Event, computation: asyncio.Task) -> None:
    global _last_error
    while not done.is_set():
        try:
            await asyncio.wait_for(done.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            try:
                alive = await asyncio.wait_for(
                    asyncio.to_thread(durable.renew_ai_job_lease, int(job["id"]), str(job["lease_token"]), lease_seconds=LEASE_SECONDS),
                    timeout=HEARTBEAT_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                # Ownership is uncertain while storage is unavailable. Stop
                # local work; the persisted lease governs later recovery.
                _last_error = f"heartbeat: {type(exc).__name__}: {exc}"[:500]
                alive = False
            if not alive:
                computation.cancel()
                return


async def _execute(job: dict[str, Any]) -> None:
    handler = registered_student_career_handlers()[job["task_type"]]
    if int(job["attempt_count"]) > int(job["max_attempts"]):
        await asyncio.to_thread(_fail_job, job, RuntimeError("Execution recovery budget exhausted"))
        return
    def start_attempt():
        with get_db_connection() as conn:
            current = _lock_job(conn, job, "running")
            if current:
                durable.record_ai_job_attempt_started(conn, current)
                conn.commit()
            return bool(current)
    if not await asyncio.to_thread(start_attempt):
        return
    done = asyncio.Event()
    computation = asyncio.create_task(handler.execute(job, durable.load_ai_job_payload(job)))
    heartbeat = asyncio.create_task(_heartbeat(job, done, computation))
    try:
        result = await asyncio.wait_for(computation, timeout=handler.timeout_seconds)
        if not isinstance(result, dict):
            raise ValueError("Career handler returned an invalid result")
        await asyncio.to_thread(durable.store_ai_job_result, job, result, policy_version="student-career-v1", require_valid_lease=True)
        # Delivery is claimed separately, so a process exit here is replayable.
    except asyncio.CancelledError:
        if _stop is not None and _stop.is_set():
            raise
        # Superseded/cancelled by another transaction; no domain write.
        return
    except Exception as exc:
        await asyncio.to_thread(_fail_job, job, exc)
    finally:
        done.set()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def _deliver(job: dict[str, Any]) -> None:
    try:
        def start_delivery():
            with get_db_connection() as conn:
                current = _lock_job(conn, job, "result_ready")
                if current:
                    durable.record_ai_job_attempt_started(conn, current, stage="apply")
                    conn.commit()
                return bool(current)
        if not await asyncio.to_thread(start_delivery):
            return
        await asyncio.to_thread(_apply_result, job)
    except Exception as exc:
        await asyncio.to_thread(_fail_job, job, exc, delivery=True)


async def _lane_worker(lane: str, index: int, stop: asyncio.Event) -> None:
    global _last_error
    worker_id = f"{socket.gethostname()}:career:{lane}:{index}"
    while not stop.is_set():
        try:
            kinds = tuple(sorted(registered_student_career_handlers(lane=lane)))
            if kinds:
                delivery = await asyncio.to_thread(durable.claim_result_ready_ai_jobs, limit=1, worker_id=worker_id, lease_seconds=LEASE_SECONDS, task_types=kinds)
                if delivery:
                    await _deliver(delivery[0])
                    continue
                claimed = await asyncio.to_thread(
                    durable.claim_due_ai_jobs, limit=1, worker_id=worker_id, lease_seconds=LEASE_SECONDS,
                    task_types=kinds, max_running=AI_CONCURRENCY if lane == "ai" else RENDER_CONCURRENCY,
                    concurrency_lock_key=career_concurrency_lock_key(lane),
                    fair_owner=True,
                )
                if claimed:
                    await _execute(claimed[0])
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _last_error = f"{type(exc).__name__}: {exc}"[:500]
            print(f"[CAREER WORKER] {worker_id}: {_last_error}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


def _recover_domains() -> None:
    global _last_maintenance
    modules = _load_domains()
    seen = set()
    failures = []
    for module in modules:
        for name in ("recover_career_jobs", "recover_resume_jobs"):
            recover = getattr(module, name, None)
            if callable(recover) and recover not in seen:
                try:
                    with get_db_connection() as conn:
                        recover(conn)
                        conn.commit()
                except Exception as exc:
                    failures.append(f"{name}: {type(exc).__name__}: {exc}")
                seen.add(recover)
    _last_maintenance = durable._iso()
    if failures:
        raise RuntimeError("; ".join(failures))


async def _maintenance(stop: asyncio.Event) -> None:
    global _last_error
    while not stop.is_set():
        started = asyncio.get_running_loop().time()
        try:
            await asyncio.to_thread(_recover_domains)
        except Exception as exc:
            _last_error = f"recovery: {type(exc).__name__}: {exc}"[:500]
            print(f"[CAREER WORKER] {_last_error}")
        await asyncio.to_thread(refresh_career_job_metrics)
        try:
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.wait_for(stop.wait(), timeout=max(1, 60-elapsed))
        except asyncio.TimeoutError:
            pass


def start_student_career_job_workers() -> int:
    global _stop, _tasks
    if _tasks or not CAREER_JOBS_ENABLED:
        return len(_tasks)
    _load_domains()
    _stop = asyncio.Event()
    _tasks = [asyncio.create_task(_lane_worker("ai", i, _stop)) for i in range(AI_CONCURRENCY)]
    _tasks += [asyncio.create_task(_lane_worker("render", 0, _stop)), asyncio.create_task(_maintenance(_stop))]
    return len(_tasks) - 1


async def stop_student_career_job_workers() -> None:
    global _stop, _tasks
    if _stop:
        _stop.set()
    for task in _tasks:
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks = []
    _stop = None


def student_career_worker_snapshot() -> dict[str, Any]:
    return {"enabled": CAREER_JOBS_ENABLED, "running": sum(not task.done() for task in _tasks),
            "ai_concurrency": AI_CONCURRENCY, "render_concurrency": RENDER_CONCURRENCY,
            "last_maintenance": _last_maintenance, "last_error": _last_error,
            "queue_metrics": career_job_metrics_snapshot()}
