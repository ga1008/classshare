"""Trusted AI billing provenance, independent of model JSON and human sessions."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import threading
import uuid

from .ai_usage_budget_service import AIUsageBudgetError


@dataclass
class AIAttribution:
    wanderer_id: int
    student_id: int
    life_epoch: int
    authority_version: int
    operation_id: str
    conn_factory: object = field(repr=False)
    job_id: int | None = None
    job_kind: str = "ai_jobs"
    sequence: int = 0
    lock: object = field(default_factory=threading.Lock, repr=False)

    def next_call(self, endpoint, payload, logical_call_id=None):
        with self.lock:
            self.sequence += 1
            sequence = logical_call_id if logical_call_id is not None else self.sequence
        digest = hashlib.sha256(json.dumps([endpoint, payload], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        # Every new transport invocation may cause another model thought,
        # including native-job retries with the same logical call and input.
        # This token remains stable only inside this gateway invocation, so
        # reservation retries cannot double charge one dispatched attempt.
        attempt = uuid.uuid4().hex
        return f"platform:{self.operation_id}:job:{self.job_kind}:{self.job_id or 0}:call:{sequence}:{digest}:attempt:{attempt}"


_attribution: ContextVar[AIAttribution | None] = ContextVar("wanderer_ai_attribution", default=None)


def current_attribution():
    return _attribution.get()


def from_service_authority(source, actor):
    from ..database import get_db_connection
    return AIAttribution(source.wanderer_id, actor.id, source.life_epoch, actor.authority_version,
                         "", source.conn_factory or get_db_connection)


def validate_attribution(conn, attribution):
    from .wanderer_identity_service import load_wanderer, current_life
    wanderer = load_wanderer(conn, attribution.wanderer_id)
    if (not wanderer or str(wanderer.get("status")) != "active" or current_life(wanderer).get("expired")
            or int(wanderer.get("student_id") or 0) != attribution.student_id
            or int(wanderer.get("life_epoch") or 0) != attribution.life_epoch
            or int(wanderer.get("authority_version") or 0) != attribution.authority_version):
        raise AIUsageBudgetError("散修后台任务的生命或权限已失效，停止新的模型请求。")


def reserve_call(attribution, logical_call_id):
    from .wanderer_ai_service import reserve_attributed_call
    with attribution.conn_factory() as conn:
        validate_attribution(conn, attribution)
    if not reserve_attributed_call(attribution.conn_factory, attribution.wanderer_id, logical_call_id,
            life_epoch=attribution.life_epoch, authority_version=attribution.authority_version):
        raise AIUsageBudgetError("散修当日模型额度或平台预算已耗尽。")


def _job_table(job_kind):
    if job_kind not in {"ai_jobs", "private_message_ai_jobs"}:
        raise ValueError("Unknown trusted AI job kind")
    return "wanderer_platform_private_ai_jobs" if job_kind == "private_message_ai_jobs" else "wanderer_platform_ai_jobs"


def persist_job_attribution(conn, job_id, *, job_kind="ai_jobs"):
    attribution = current_attribution()
    if attribution is None:
        return
    validate_attribution(conn, attribution)
    table = _job_table(job_kind)
    conn.execute(f"""INSERT INTO {table}
        (job_id,wanderer_id,student_id,life_epoch,authority_version,operation_id)
        VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO NOTHING""",
        (int(job_id), attribution.wanderer_id, attribution.student_id, attribution.life_epoch,
         attribution.authority_version, attribution.operation_id))


@contextmanager
def restored_job_attribution(job, conn_factory, *, job_kind="ai_jobs"):
    from .account_control_service import table_exists
    with conn_factory() as conn:
        table = _job_table(job_kind)
        row = conn.execute(f"SELECT * FROM {table} WHERE job_id=?", (int(job["id"]),)).fetchone() if table_exists(conn, table) else None
        attribution = AIAttribution(**dict(row), conn_factory=conn_factory, job_kind=job_kind) if row else None
        if attribution is not None:
            validate_attribution(conn, attribution)
    marker = _attribution.set(attribution)
    try:
        yield attribution
    finally:
        _attribution.reset(marker)


def durable_attribution(function):
    """Restore trusted persisted provenance in workers outside the HTTP task."""
    from functools import wraps
    @wraps(function)
    async def wrapped(job):
        from ..database import get_db_connection
        from .ai_durable_job_service import reschedule_ai_job
        try:
            with restored_job_attribution(job, get_db_connection):
                return await function(job)
        except AIUsageBudgetError as exc:
            # No new model request may be made by a revoked incarnation.
            # Retain the ordinary durable job failure/reconciliation path.
            import asyncio
            from ..db.service_execution_guard import suspend_service_guard
            with suspend_service_guard():
                await asyncio.to_thread(reschedule_ai_job, job, error_code="wanderer_authority_or_budget",
                                        error_message=str(exc), terminal=True)
    return wrapped
