"""Process-local identity for one verified broker invocation; never headers."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request


@dataclass
class _BrokerIdentity:
    actor_role: str
    actor_id: int
    authority_fingerprint: str
    task_id: int
    attempt_id: str
    fencing_token: int
    path: str
    nonce: object
    active: bool = True


_broker_identity: ContextVar[_BrokerIdentity | None] = ContextVar("lanshare_agent_broker_identity", default=None)
_SCOPE_KEY = "lanshare.agent.broker.nonce"


def current_agent_broker_user(request: Request) -> dict[str, Any] | None:
    identity = _broker_identity.get()
    if identity is None:
        return None
    if (not identity.active or request.scope.get(_SCOPE_KEY) is not identity.nonce
            or request.method != "GET" or request.url.path != identity.path):
        raise HTTPException(403, "Agent 进程内请求身份无效或已结束。")
    from ..database import get_db_connection
    from .agent_actor_service import resolve_agent_actor

    with get_db_connection() as conn:
        actor = resolve_agent_actor(conn, identity.actor_role, identity.actor_id)
    if actor.authority_fingerprint != identity.authority_fingerprint:
        raise HTTPException(401, "当前账号权限已变化，请重新授权 Agent 任务。")
    # No token/cookie or impersonated session is invented. Existing business
    # dependencies still perform their regular action/resource checks.
    return {**actor.as_user(), "auth_channel": "agent_broker"}
