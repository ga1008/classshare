"""Nonce-bound identity for one fixed HTTP request; never accepted from headers."""
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class _RequestIdentity:
    delegation_id: str
    actor_role: str
    actor_id: int
    task_id: int
    attempt_id: str
    fencing_token: int
    source_session_hash: str
    authority_fingerprint: str
    operation_id: str
    method: str
    path: str
    query_string: bytes
    body_hash: str
    route: object
    nonce: object
    required_scope: str
    active: bool = True


_request_identity: ContextVar[_RequestIdentity | None] = ContextVar('lanshare_platform_request_identity', default=None)
_SCOPE_KEY = 'lanshare.platform_request.binding'


def current_agent_platform_request_user(request: Request) -> dict | None:
    identity = _request_identity.get()
    if identity is None:
        return None
    binding = request.scope.get(_SCOPE_KEY)
    if (not identity.active or not isinstance(binding, tuple) or len(binding) != 3
            or binding[0] is not identity.nonce or binding[1] != identity.operation_id
            or binding[2] != identity.body_hash or request.scope.get('route') is not identity.route
            or request.method != identity.method or request.url.path != identity.path
            or request.scope.get('query_string', b'') != identity.query_string):
        raise HTTPException(403, 'Agent 平台请求身份与已批准操作不一致。')
    from ..database import get_db_connection
    from .agent_delegation_service import verify_stored_task_delegation
    with get_db_connection() as conn:
        grant = verify_stored_task_delegation(conn, identity.delegation_id, purpose='tools', required_scope=identity.required_scope)
    if (grant.actor.role != identity.actor_role or grant.actor.id != identity.actor_id
            or grant.actor.authority_fingerprint != identity.authority_fingerprint
            or int(grant.task['id']) != identity.task_id or grant.attempt['id'] != identity.attempt_id
            or int(grant.attempt['fencing_token']) != identity.fencing_token
            or grant.delegation['source_session_hash'] != identity.source_session_hash):
        raise HTTPException(401, 'Agent 当前用户权限或执行租约已变化。')
    return {**grant.actor.as_user(), 'auth_channel': 'agent_platform_request'}
