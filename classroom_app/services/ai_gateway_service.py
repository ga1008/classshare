from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from ..database import get_db_connection
from ..db.schema_cultivation_progress import ensure_cultivation_progress_schema
from .ai_usage_budget_service import (
    AIUsageBudgetError,
    mark_ai_usage_budget_overage_if_needed,
    should_defer_low_priority_ai_task,
)


AI_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
AI_GATEWAY_MAX_CONCURRENT = max(1, int(os.getenv("LANSHARE_AI_GATEWAY_MAX_CONCURRENT", "3")))

_SEQUENCE = count(1)
_GATEWAYS_BY_LOOP: dict[int, "_AIGateway"] = {}


def _priority_label(value: Any) -> str:
    label = str(value or "P1").strip().upper()
    return label if label in AI_PRIORITY_ORDER else "P1"


def _estimate_tokens(value: Any) -> int:
    if value is None:
        return 0
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        text = str(value)
    # A rough, stable estimate is enough for budget trend views until the AI
    # service returns provider-specific token counts.
    return max(1, int(len(text) / 4)) if text else 0


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="ignore")
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception:
        return ""


def record_ai_usage(
    *,
    task_type: str,
    priority: str,
    endpoint: str,
    status: str,
    status_code: int | None = None,
    duration_ms: int = 0,
    prompt_tokens_estimate: int = 0,
    completion_tokens_estimate: int = 0,
    class_offering_id: int | None = None,
    student_id: int | None = None,
    teacher_id: int | None = None,
    source_ref: str = "",
    error_message: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    with get_db_connection() as conn:
        ensure_cultivation_progress_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO ai_usage_log (
                task_type, priority, endpoint, status, status_code, duration_ms,
                prompt_tokens_estimate, completion_tokens_estimate,
                class_offering_id, student_id, teacher_id, source_ref,
                error_message, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                str(task_type or "unknown").strip() or "unknown",
                _priority_label(priority),
                str(endpoint or ""),
                str(status or "unknown"),
                status_code,
                max(0, int(duration_ms or 0)),
                max(0, int(prompt_tokens_estimate or 0)),
                max(0, int(completion_tokens_estimate or 0)),
                class_offering_id,
                student_id,
                teacher_id,
                str(source_ref or ""),
                str(error_message or "")[:1000],
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        usage_log_id = int(getattr(cursor, "lastrowid", 0) or 0)
        try:
            mark_ai_usage_budget_overage_if_needed(
                conn,
                usage_log_id=usage_log_id,
                class_offering_id=class_offering_id,
                task_type=str(task_type or "unknown").strip() or "unknown",
                priority=_priority_label(priority),
            )
        except Exception as exc:  # pragma: no cover - budget telemetry must not break AI work
            print(f"[AI_GATEWAY] budget overage check failed: {exc}")
        conn.commit()
        return usage_log_id


def _safe_record_ai_usage(**payload: Any) -> None:
    try:
        record_ai_usage(**payload)
    except Exception as exc:  # pragma: no cover - telemetry must not break AI work
        print(f"[AI_GATEWAY] usage log write failed: {exc}")


@dataclass(order=True)
class _AIJob:
    priority_rank: int
    sequence: int
    future: asyncio.Future = field(compare=False)
    client: Any = field(compare=False)
    method: str = field(compare=False)
    endpoint: str = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False)
    task_type: str = field(compare=False)
    priority: str = field(compare=False)
    class_offering_id: int | None = field(compare=False)
    student_id: int | None = field(compare=False)
    teacher_id: int | None = field(compare=False)
    source_ref: str = field(compare=False)
    metadata: dict[str, Any] = field(compare=False)
    enqueued_at: float = field(compare=False)
    prompt_tokens_estimate: int = field(compare=False)


class _AIGateway:
    def __init__(self, *, max_concurrent: int) -> None:
        self.queue: asyncio.PriorityQueue[_AIJob] = asyncio.PriorityQueue()
        self.max_concurrent = max(1, int(max_concurrent))
        self.workers: list[asyncio.Task] = []

    def ensure_workers(self) -> None:
        live_workers = [task for task in self.workers if not task.done()]
        self.workers = live_workers
        while len(self.workers) < self.max_concurrent:
            self.workers.append(asyncio.create_task(self._worker()))

    async def submit(self, job: _AIJob) -> Any:
        self.ensure_workers()
        await self.queue.put(job)
        return await job.future

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job.future.cancelled():
                    continue
                await self._execute(job)
            finally:
                self.queue.task_done()

    async def _execute(self, job: _AIJob) -> None:
        started = time.perf_counter()
        queue_wait_ms = int(max(0, started - job.enqueued_at) * 1000)
        try:
            caller = getattr(job.client, job.method)
            response = await caller(job.endpoint, **job.kwargs)
            duration_ms = int(max(0, time.perf_counter() - started) * 1000)
            status_code = getattr(response, "status_code", None)
            status = "success" if status_code is None or int(status_code) < 400 else "http_error"
            _safe_record_ai_usage(
                task_type=job.task_type,
                priority=job.priority,
                endpoint=job.endpoint,
                status=status,
                status_code=status_code,
                duration_ms=duration_ms,
                prompt_tokens_estimate=job.prompt_tokens_estimate,
                completion_tokens_estimate=_estimate_tokens(_response_text(response)),
                class_offering_id=job.class_offering_id,
                student_id=job.student_id,
                teacher_id=job.teacher_id,
                source_ref=job.source_ref,
                metadata={**(job.metadata or {}), "queue_wait_ms": queue_wait_ms},
            )
            if not job.future.done():
                job.future.set_result(response)
        except Exception as exc:
            duration_ms = int(max(0, time.perf_counter() - started) * 1000)
            _safe_record_ai_usage(
                task_type=job.task_type,
                priority=job.priority,
                endpoint=job.endpoint,
                status="failed",
                duration_ms=duration_ms,
                prompt_tokens_estimate=job.prompt_tokens_estimate,
                completion_tokens_estimate=0,
                class_offering_id=job.class_offering_id,
                student_id=job.student_id,
                teacher_id=job.teacher_id,
                source_ref=job.source_ref,
                error_message=f"{type(exc).__name__}: {exc}",
                metadata={**(job.metadata or {}), "queue_wait_ms": queue_wait_ms},
            )
            if not job.future.done():
                job.future.set_exception(exc)


def _get_gateway() -> _AIGateway:
    loop = asyncio.get_running_loop()
    key = id(loop)
    gateway = _GATEWAYS_BY_LOOP.get(key)
    if gateway is None:
        gateway = _AIGateway(max_concurrent=AI_GATEWAY_MAX_CONCURRENT)
        _GATEWAYS_BY_LOOP[key] = gateway
    return gateway


async def ai_gateway_post(
    client: Any,
    endpoint: str,
    *,
    json_payload: Any | None = None,
    timeout: float | None = None,
    task_type: str,
    priority: str = "P1",
    class_offering_id: int | None = None,
    student_id: int | None = None,
    teacher_id: int | None = None,
    source_ref: str = "",
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    normalized_priority = _priority_label(priority)
    if normalized_priority == "P2" and class_offering_id:
        should_defer = False
        try:
            with get_db_connection() as conn:
                should_defer = should_defer_low_priority_ai_task(
                    conn,
                    class_offering_id=int(class_offering_id),
                    task_type=str(task_type or "unknown").strip() or "unknown",
                )
        except Exception as exc:  # pragma: no cover - budget check is best-effort
            print(f"[AI_GATEWAY] low-priority budget check failed: {exc}")
        if should_defer:
            _safe_record_ai_usage(
                task_type=str(task_type or "unknown").strip() or "unknown",
                priority=normalized_priority,
                endpoint=str(endpoint or ""),
                status="deferred",
                status_code=None,
                duration_ms=0,
                prompt_tokens_estimate=_estimate_tokens(json_payload),
                completion_tokens_estimate=0,
                class_offering_id=class_offering_id,
                student_id=student_id,
                teacher_id=teacher_id,
                source_ref=source_ref,
                error_message="weekly budget exceeded",
                metadata={**(metadata or {}), "defer_reason": "weekly_budget_exceeded"},
            )
            raise AIUsageBudgetError("课程 AI 周预算已超额，低优先级任务已暂停。")
    request_kwargs = dict(kwargs)
    if json_payload is not None:
        request_kwargs["json"] = json_payload
    if timeout is not None:
        request_kwargs["timeout"] = timeout
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    job = _AIJob(
        priority_rank=AI_PRIORITY_ORDER[normalized_priority],
        sequence=next(_SEQUENCE),
        future=future,
        client=client,
        method="post",
        endpoint=str(endpoint or ""),
        kwargs=request_kwargs,
        task_type=str(task_type or "unknown").strip() or "unknown",
        priority=normalized_priority,
        class_offering_id=class_offering_id,
        student_id=student_id,
        teacher_id=teacher_id,
        source_ref=source_ref,
        metadata=dict(metadata or {}),
        enqueued_at=time.perf_counter(),
        prompt_tokens_estimate=_estimate_tokens(json_payload),
    )
    return await _get_gateway().submit(job)


def get_ai_gateway_depth() -> int:
    try:
        return int(_get_gateway().queue.qsize())
    except RuntimeError:
        return 0
