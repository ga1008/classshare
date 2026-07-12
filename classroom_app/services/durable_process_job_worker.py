from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress
from typing import Any

from ..config import AI_DURABLE_JOBS_ENABLED
from ..database import get_db_connection
from .ai_durable_job_service import (
    JOB_DEAD_LETTER,
    JOB_REVIEW_REQUIRED,
    claim_due_ai_jobs,
    claim_result_ready_ai_jobs,
    cleanup_ai_job_input_files,
    load_ai_job_input_files,
    load_ai_job_payload,
    load_ai_job_result,
    mark_ai_job_succeeded,
    record_ai_job_attempt_started,
    renew_ai_job_lease,
    reschedule_ai_job,
    store_ai_job_result,
)


LOCAL_TASK_TYPES = ("document_import", "document_generation")
WORKER_CONCURRENCY = max(1, min(int(os.getenv("AI_LOCAL_JOB_WORKER_CONCURRENCY", "1")), 2))
POLL_SECONDS = max(0.5, float(os.getenv("AI_LOCAL_JOB_WORKER_POLL_SECONDS", "2")))
LEASE_SECONDS = max(120, int(os.getenv("AI_LOCAL_JOB_WORKER_LEASE_SECONDS", "900")))

_worker_tasks: list[asyncio.Task] = []
_stop_event: asyncio.Event | None = None


_TARGETS = {
    "lesson_plan": ("lesson_plans", "id"),
    "assessment_plan": ("assessment_plans", "id"),
    "teacher_evaluation": ("teacher_evaluations", "id"),
}


def _target_row(payload: dict[str, Any]) -> dict[str, Any]:
    target_type = str(payload.get("target_type") or "")
    table_info = _TARGETS.get(target_type)
    if not table_info:
        raise ValueError(f"unsupported durable process target: {target_type!r}")
    target_id = str(payload.get("target_id") or "").strip()
    if not target_id:
        raise ValueError("durable process target id is missing")
    table, id_column = table_info
    with get_db_connection() as conn:
        row = conn.execute(
            f"SELECT status, ai_gen_status, ai_gen_error FROM {table} WHERE {id_column} = ? LIMIT 1",
            (target_id,),
        ).fetchone()
    if not row:
        raise ValueError("durable process target no longer exists")
    return dict(row)


def _ensure_target_completed(payload: dict[str, Any]) -> dict[str, Any]:
    row = _target_row(payload)
    ai_status = str(row.get("ai_gen_status") or "").strip().lower()
    if ai_status not in {"completed", "completed_with_fallback"}:
        error = str(row.get("ai_gen_error") or "").strip()
        raise RuntimeError(error or f"business task finished with ai_gen_status={ai_status or 'empty'}")
    return {
        "target_type": str(payload.get("target_type") or ""),
        "target_id": str(payload.get("target_id") or ""),
        "business_status": str(row.get("status") or ""),
        "ai_gen_status": ai_status,
    }


async def _dispatch_import(payload: dict[str, Any]) -> None:
    files = await asyncio.to_thread(load_ai_job_input_files, payload.get("input_files") or [])
    target_type = str(payload.get("target_type") or "")
    target_id = str(payload.get("target_id") or "")
    teacher_id = int(payload.get("teacher_id") or 0)
    extra_prompt = str(payload.get("extra_prompt") or "")
    if target_type == "lesson_plan":
        from .lesson_plan_import_service import run_import_job

        await run_import_job(target_id, files, extra_prompt, teacher_id, cleanup_files=False)
    elif target_type == "assessment_plan":
        from .assessment_plan_import_service import run_import_job

        await run_import_job(target_id, files, extra_prompt, teacher_id, cleanup_files=False)
    elif target_type == "teacher_evaluation":
        from .teacher_evaluation_import_service import run_import_job

        await run_import_job(target_id, files, extra_prompt, teacher_id, cleanup_files=False)
    else:
        raise ValueError(f"unsupported durable import target: {target_type!r}")


async def _dispatch_generation(payload: dict[str, Any]) -> None:
    target_type = str(payload.get("target_type") or "")
    target_id = str(payload.get("target_id") or "")
    class_offering_id = int(payload.get("class_offering_id") or 0)
    teacher_id = int(payload.get("teacher_id") or 0)
    prompt = str(payload.get("prompt") or "")
    field_overrides = payload.get("field_overrides") if isinstance(payload.get("field_overrides"), dict) else {}
    if target_type == "lesson_plan":
        from .lesson_plan_generation_service import run_generation_job

        session_plan = payload.get("session_plan") if isinstance(payload.get("session_plan"), list) else None
        await run_generation_job(target_id, class_offering_id, teacher_id, session_plan=session_plan)
    elif target_type == "assessment_plan":
        from .assessment_plan_generation_service import run_generation_job

        await run_generation_job(
            target_id,
            class_offering_id,
            teacher_id,
            prompt,
            field_overrides=field_overrides,
        )
    elif target_type == "teacher_evaluation":
        from .teacher_evaluation_generation_service import run_generation_job

        await run_generation_job(target_id, class_offering_id, teacher_id, prompt, field_overrides)
    else:
        raise ValueError(f"unsupported durable generation target: {target_type!r}")


async def _lease_heartbeat(job: dict[str, Any], stop: asyncio.Event) -> None:
    interval = max(30.0, LEASE_SECONDS / 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            renewed = await asyncio.to_thread(
                renew_ai_job_lease,
                int(job["id"]),
                str(job.get("lease_token") or ""),
                lease_seconds=LEASE_SECONDS,
            )
            if not renewed:
                return


async def _execute(job: dict[str, Any]) -> None:
    payload = load_ai_job_payload(job)
    with get_db_connection() as conn:
        record_ai_job_attempt_started(conn, job)
        conn.commit()
    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(_lease_heartbeat(job, heartbeat_stop))
    try:
        if str(job.get("task_type") or "") == "document_import":
            await _dispatch_import(payload)
        elif str(job.get("task_type") or "") == "document_generation":
            await _dispatch_generation(payload)
        else:
            raise ValueError("unsupported local durable job type")
        result_payload = _ensure_target_completed(payload)
        result = await asyncio.to_thread(
            store_ai_job_result,
            job,
            result_payload,
            policy_version=str(job.get("policy_version") or ""),
        )
        await asyncio.to_thread(
            mark_ai_job_succeeded,
            int(job["id"]),
            int(result["id"]),
            lease_token=str(job.get("lease_token") or ""),
        )
        if str(job.get("task_type") or "") == "document_import":
            await asyncio.to_thread(cleanup_ai_job_input_files, payload.get("input_files") or [])
    except Exception as exc:
        terminal = await asyncio.to_thread(
            reschedule_ai_job,
            job,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if terminal in {JOB_DEAD_LETTER, JOB_REVIEW_REQUIRED} and str(job.get("task_type") or "") == "document_import":
            await asyncio.to_thread(cleanup_ai_job_input_files, payload.get("input_files") or [])
    finally:
        heartbeat_stop.set()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def _finish_result_ready(job: dict[str, Any]) -> None:
    payload = load_ai_job_payload(job)
    result = await asyncio.to_thread(load_ai_job_result, job)
    _ensure_target_completed(payload)
    await asyncio.to_thread(
        mark_ai_job_succeeded,
        int(job["id"]),
        int(result["id"]),
        lease_token=str(job.get("lease_token") or ""),
    )
    if str(job.get("task_type") or "") == "document_import":
        await asyncio.to_thread(cleanup_ai_job_input_files, payload.get("input_files") or [])


async def _worker_loop(index: int, stop: asyncio.Event) -> None:
    worker_id = f"{socket.gethostname()}:main-local:{index}"
    while not stop.is_set():
        try:
            delivery = await asyncio.to_thread(
                claim_result_ready_ai_jobs,
                limit=1,
                worker_id=worker_id,
                lease_seconds=300,
                task_types=LOCAL_TASK_TYPES,
            )
            if delivery:
                await _finish_result_ready(delivery[0])
                continue
            claimed = await asyncio.to_thread(
                claim_due_ai_jobs,
                limit=1,
                worker_id=worker_id,
                lease_seconds=LEASE_SECONDS,
                task_types=LOCAL_TASK_TYPES,
            )
            if claimed:
                await _execute(claimed[0])
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[DURABLE PROCESS WORKER] worker={worker_id} error={exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


def start_durable_process_job_workers() -> int:
    global _stop_event, _worker_tasks
    if not AI_DURABLE_JOBS_ENABLED or _worker_tasks:
        return 0
    _stop_event = asyncio.Event()
    _worker_tasks = [asyncio.create_task(_worker_loop(index + 1, _stop_event)) for index in range(WORKER_CONCURRENCY)]
    return len(_worker_tasks)


async def stop_durable_process_job_workers() -> None:
    global _stop_event, _worker_tasks
    if _stop_event is not None:
        _stop_event.set()
    for task in _worker_tasks:
        task.cancel()
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
    _worker_tasks = []
    _stop_event = None
