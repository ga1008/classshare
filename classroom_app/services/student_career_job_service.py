"""Career/resume contracts on the existing durable AI job ledger.

Domain handlers calculate a candidate outside transactions and apply it in a
short, lease-protected transaction. No independent queue or in-memory job
ownership is introduced here.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from . import ai_durable_job_service as durable
from ..config import CAREER_JOBS_ENABLED
from .career_rollout_service import require_ai_job_admission
from ..db.connection import get_configured_db_engine

Execute = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]
Apply = Callable[[Any, dict[str, Any], dict[str, Any], dict[str, Any]], bool]
Fail = Callable[[Any, dict[str, Any], dict[str, Any], str, str], None]


class CareerJobCapacityError(ValueError):
    """The caller should return 429 with Retry-After, retaining their draft."""


class SupersededCareerJob(Exception):
    """The input no longer represents the current business revision."""


@dataclass(frozen=True)
class CareerJobHandler:
    execute: Execute
    apply: Apply
    fail: Fail | None = None
    timeout_seconds: float = 360
    lane: str = "ai"


_HANDLERS: dict[str, CareerJobHandler] = {}
MAX_ACTIVE_PER_STUDENT = max(1, int(os.getenv("CAREER_JOBS_PER_STUDENT", "4")))
MAX_PENDING_JOBS = max(1, int(os.getenv("CAREER_JOBS_MAX_PENDING", "2000")))
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
UPSTREAM_COOLDOWN_SECONDS = max(120, int(os.getenv("CAREER_UPSTREAM_COOLDOWN_SECONDS", "120")))


def register_student_career_handler(
    task_type: str, *, execute: Execute, apply: Apply, fail: Fail | None = None,
    timeout_seconds: float = 360, lane: str = "ai",
) -> None:
    if lane not in {"ai", "render"}:
        raise ValueError("Unknown student career job lane")
    if not task_type or not callable(execute) or not callable(apply):
        raise ValueError("A durable career job needs execute and apply handlers")
    _HANDLERS[task_type] = CareerJobHandler(execute, apply, fail, max(1, float(timeout_seconds)), lane)
    durable.TASK_POLICIES[task_type] = durable.AIDurableTaskPolicy(
        task_type, 60, 3, max(120, int(timeout_seconds) + 90), durable.JOB_DEAD_LETTER,
    )


def registered_student_career_handlers(*, lane: str | None = None) -> dict[str, CareerJobHandler]:
    return {key: value for key, value in _HANDLERS.items() if lane is None or value.lane == lane}


def _admission_lock(conn, student_id: int | None) -> None:
    if get_configured_db_engine() == "postgres":
        # Admission is a short transaction; use a global lock first so distinct
        # students cannot all pass the bounded queue check simultaneously.
        conn.execute("SELECT pg_advisory_xact_lock(?)", (742819036117,))


def enqueue_student_career_job(
    conn, *, task_type: str, dedupe_key: str, payload: dict[str, Any],
    student_id: int | None = None, scope_type: str = "", scope_id: str = "",
    source_ref: str = "", max_attempts: int = 3, requester_student_id: int | None = None,
) -> dict[str, Any]:
    if task_type not in _HANDLERS:
        raise ValueError(f"Student career handler is not registered: {task_type}")
    if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Career task input is invalid or too large")
    durable.ensure_ai_job_schema(conn)
    _admission_lock(conn, student_id)
    existing = conn.execute("SELECT * FROM ai_jobs WHERE dedupe_key = ?", (str(dedupe_key),)).fetchone()
    if existing:
        existing = dict(existing)
        if existing["task_type"] != task_type or existing.get("owner_user_pk") != student_id:
            raise ValueError("Career task idempotency key belongs to another request")
        return existing
    if not CAREER_JOBS_ENABLED:
        raise CareerJobCapacityError("职业与简历后台处理已暂停，已保存的资料仍可查看和编辑，请稍后重试。")
    require_ai_job_admission(conn, task_type=task_type, lane=_HANDLERS[task_type].lane,
                             student_id=student_id, payload=payload, requester_student_id=requester_student_id)
    kinds = tuple(_HANDLERS)
    placeholders = ",".join("?" for _ in kinds)
    active_sql = f"task_type IN ({placeholders}) AND status IN ('queued','running','retry_wait','result_ready')"
    if student_id is not None:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM ai_jobs WHERE {active_sql} AND owner_role='student' AND owner_user_pk=?", (*kinds, int(student_id))).fetchone()
        if int(row["n"]) >= MAX_ACTIVE_PER_STUDENT:
            raise CareerJobCapacityError("你的后台任务较多，请等待当前任务完成后再试。")
    row = conn.execute(f"SELECT COUNT(*) AS n FROM ai_jobs WHERE {active_sql}", kinds).fetchone()
    if int(row["n"]) >= MAX_PENDING_JOBS:
        raise CareerJobCapacityError("职业与简历任务正在排队，请稍后重试；已保存的资料不会丢失。")
    job, _ = durable.create_ai_job(
        conn, task_type=task_type, dedupe_key=dedupe_key, payload=payload,
        max_attempts=max(1, min(3, int(max_attempts))),
        scope_type=scope_type, scope_id=str(scope_id), source_ref=source_ref,
        owner_role="student" if student_id is not None else "system",
        owner_user_pk=student_id, policy_version="student-career-v1",
    )
    return job


def public_job_state(conn, job_id: int | None, *, student_id: int | None = None) -> dict[str, Any]:
    if not job_id:
        return {}
    row = conn.execute(
        "SELECT id,task_type,status,owner_role,owner_user_pk,attempt_count,max_attempts,"
        "created_at,started_at,updated_at,finished_at,available_at,last_error_code "
        "FROM ai_jobs WHERE id=?", (int(job_id),),
    ).fetchone()
    if not row:
        return {}
    job = dict(row)
    if student_id is not None and (job["owner_role"] != "student" or job["owner_user_pk"] != int(student_id)):
        return {}
    status = str(job["status"])
    error = str(job.get("last_error_code") or "")
    # Never return raw provider exceptions, payloads or another domain's jobs.
    if job["task_type"] not in _HANDLERS:
        return {}
    retry_after = 6
    if status == "retry_wait":
        try:
            retry_after = max(1, min(300, int((datetime.fromisoformat(job["available_at"]) - datetime.now()).total_seconds())))
        except (TypeError, ValueError):
            retry_after = 15
    return {
        "id": job["id"], "job_id": job["id"], "status": status,
        "stage": "applying" if status == "result_ready" else status,
        "task_type": job["task_type"], "attempt_count": job["attempt_count"],
        "max_attempts": job["max_attempts"], "queued_at": job["created_at"],
        "started_at": job["started_at"], "updated_at": job["updated_at"],
        "finished_at": job["finished_at"], "retry_after": retry_after,
        "retryable": status in {"dead_letter", "review_required", "cancelled"},
        "cancellable": status in durable.ACTIVE_JOB_STATUSES,
        "error_code": error if error.isidentifier() and len(error) <= 80 else ("generation_failed" if error else ""),
        "error_message": "处理暂未完成，可稍后重试。已保存的资料和已有结果仍会保留。" if error else "",
    }


def supersede_student_career_jobs(
    conn, *, scope_type: str, scope_id: str, student_id: int | None = None,
) -> int:
    kinds = tuple(_HANDLERS)
    if not kinds:
        return 0
    placeholders = ",".join("?" for _ in kinds)
    owner_filter = " AND owner_role='student' AND owner_user_pk=?" if student_id is not None else ""
    params = [durable._iso(durable._now() + timedelta(seconds=UPSTREAM_COOLDOWN_SECONDS)), durable._iso(), durable._iso(), str(scope_type), str(scope_id), *kinds]
    if student_id is not None:
        params.append(int(student_id))
    cursor = conn.execute(
        f"UPDATE ai_jobs SET capacity_reserved_until=CASE WHEN status='running' THEN ? ELSE capacity_reserved_until END,"
        f"status='superseded',lease_token='',lease_expires_at=NULL,"
        f"locked_at=NULL,locked_by='',updated_at=?,finished_at=? "
        f"WHERE scope_type=? AND scope_id=? AND task_type IN ({placeholders}) "
        f"AND status IN ('queued','running','retry_wait','result_ready'){owner_filter}", params,
    )
    return int(cursor.rowcount or 0)


def cancel_student_career_job(conn, job_id: int, *, student_id: int) -> dict[str, Any]:
    state = public_job_state(conn, job_id, student_id=student_id)
    if not state:
        raise ValueError("未找到你的后台任务")
    if not state["cancellable"]:
        return state
    conn.execute("UPDATE ai_jobs SET capacity_reserved_until=? WHERE id=? AND status='running'",
                 (durable._iso(durable._now() + timedelta(seconds=UPSTREAM_COOLDOWN_SECONDS)), int(job_id)))
    durable.cancel_ai_job_by_id(conn, job_id, reason="cancelled_by_student")
    return public_job_state(conn, job_id, student_id=student_id)


def career_concurrency_lock_key(lane: str) -> int:
    return int.from_bytes(hashlib.sha256(f"lanshare:student-career:{lane}".encode()).digest()[:8], "big", signed=True)
