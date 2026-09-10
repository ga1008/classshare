"""Nonce-bound identity for one user-confirmed in-process platform request.

Used only when the authenticated task owner has confirmed a proposed
``platform_route_request`` in the platform UI. The confirming request already
carries the user's live session; this context lets the normal route dependencies
see that same user for exactly one bounded ASGI call, without cookies, headers
or a model-facing credential. The live session and authority are re-verified.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import time

from fastapi import HTTPException, Request


@dataclass
class _UserRouteIdentity:
    actor_role: str
    actor_id: int
    session_id: str = field(repr=False)
    session_hash: str = ""
    authority_fingerprint: str = ""
    method: str = ""
    path: str = ""
    query_string: bytes = b""
    body_hash: str = ""
    route: object = None
    nonce: object = None
    active: bool = True


_user_route_identity: ContextVar[_UserRouteIdentity | None] = ContextVar("lanshare_user_route_request_identity", default=None)
_SCOPE_KEY = "lanshare.user_route_request.binding"


def current_agent_user_route_request_user(request: Request) -> dict | None:
    identity = _user_route_identity.get()
    if identity is None:
        return None
    binding = request.scope.get(_SCOPE_KEY)
    if (not identity.active or not isinstance(binding, tuple) or len(binding) != 2
            or binding[0] is not identity.nonce or binding[1] != identity.body_hash
            or request.scope.get("route") is not identity.route or request.method != identity.method
            or request.url.path != identity.path or request.scope.get("query_string", b"") != identity.query_string):
        raise HTTPException(403, "本人确认的平台操作身份与已核对请求不一致。")
    from ..database import get_db_connection
    from .agent_actor_service import resolve_agent_actor
    from .agent_delegation_service import _assert_session

    with get_db_connection() as conn:
        actor = resolve_agent_actor(conn, identity.actor_role, identity.actor_id)
        if actor.authority_fingerprint != identity.authority_fingerprint:
            raise HTTPException(401, "确认期间账号权限已变化，请重新核对。")
        _assert_session(conn, actor, identity.session_hash, int(time.time()))
    return {**actor.as_user(), "session_id": identity.session_id, "auth_channel": "agent_user_confirmation"}
