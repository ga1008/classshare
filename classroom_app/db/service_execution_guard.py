"""Trusted service-write fencing on the existing business transaction.

Human connections have no guard. Reads retain their original transaction
semantics. Locks and authority checks begin at the first actual SQL mutation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import re
import sys

_suspended = ContextVar("service_execution_guard_suspended", default=False)
_inside = ContextVar("service_execution_guard_inside", default=False)
_MUTATION = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|MERGE|COPY)\b", re.I)
_LITERALS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/", re.S)


def _writes(sql):
    return _MUTATION.search(_LITERALS.sub("", str(sql))) is not None


@contextmanager
def suspend_service_guard():
    """Executor-owned receipt bookkeeping only; never bound from HTTP inputs."""
    marker = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(marker)


def _source():
    attribution_module = sys.modules.get("classroom_app.services.wanderer_ai_attribution")
    attribution = attribution_module.current_attribution() if attribution_module else None
    # An accepted durable worker owns its original life epoch, independently
    # of the completed HTTP turn that scheduled it.
    if attribution is not None and attribution.job_id is not None:
        return "job", attribution
    module = sys.modules.get("classroom_app.services.agent_platform_request_context")
    identity = module._request_identity.get() if module else None
    if identity is not None and identity.service_authority is not None and identity.service_identity_verified:
        if identity.active:
            return "request", identity
        if attribution is None:
            return "request", identity  # Fail closed for a detached stale request.
    return ("job", attribution) if attribution is not None else None


def _verify(conn, source):
    kind, value = source
    if kind == "request":
        from fastapi import HTTPException
        from ..services.platform_execution_authority import verify_platform_authority
        if not value.active:
            raise HTTPException(409, "散修原平台请求已结束，不能提交旧请求的新写入。")
        authority = verify_platform_authority(conn, value.service_authority, required_scope=value.required_scope)
        if authority.actor.id != value.actor_id or authority.actor.authority_fingerprint != value.authority_fingerprint:
            raise HTTPException(409, "散修业务提交身份已经变化。")
    else:
        from ..services.wanderer_ai_attribution import validate_attribution
        validate_attribution(conn, value)


def before_statement(conn, sql):
    if _suspended.get() or _inside.get():
        return
    control = _LITERALS.sub("", str(sql)).strip().upper()
    if control.startswith(("COMMIT", "END")):
        before_commit(conn)
        return
    if control.startswith("ROLLBACK TO"):
        # PostgreSQL releases locks acquired after a savepoint when rolling
        # back to it. Reacquire our owner locks before another write/commit.
        conn._service_execution_needs_lock = True
        return
    if not _writes(sql):
        return
    previous = getattr(conn, "_service_execution_source", None)
    if previous is not None and not getattr(conn, "_service_execution_needs_lock", False):
        return
    source = previous or _source()
    if source is None:
        return
    marker = _inside.set(True)
    try:
        from ..services.wanderer_resource_service import lock_actor_resources
        owner = source[1].service_authority.wanderer_id if source[0] == "request" else source[1].wanderer_id
        lock_actor_resources(conn, owner)
        _verify(conn, source)
        conn._service_execution_source = source
        conn._service_execution_needs_lock = False
    finally:
        _inside.reset(marker)


def before_commit(conn):
    source = getattr(conn, "_service_execution_source", None)
    if source is None or _suspended.get() or _inside.get():
        return
    marker = _inside.set(True)
    try:
        if getattr(conn, "_service_execution_needs_lock", False):
            from ..services.wanderer_resource_service import lock_actor_resources
            owner = source[1].service_authority.wanderer_id if source[0] == "request" else source[1].wanderer_id
            lock_actor_resources(conn, owner)
        _verify(conn, source)
    finally:
        _inside.reset(marker)


def before_script(sql):
    # sqlite3.executescript implicitly commits before executing; normal native
    # CRUD uses execute/executemany and must not silently lose its guard lock.
    if not _suspended.get() and not _inside.get() and _source() is not None and _writes(sql):
        from fastapi import HTTPException
        raise HTTPException(409, "散修业务写入必须使用可核对的普通事务。")


def clear(conn):
    conn._service_execution_source = None
    conn._service_execution_needs_lock = False


def after_statement(conn, sql):
    control = _LITERALS.sub("", str(sql)).strip().upper()
    if control.startswith(("COMMIT", "END")) or (control.startswith("ROLLBACK") and not control.startswith("ROLLBACK TO")):
        clear(conn)
