"""Server-owned dispatch for domain-specific human confirmation workflows.

MCP may propose these actions but cannot invoke them or supply the user's
confirmation. The authenticated proposal route owns the token and transaction;
each domain owns its snapshot, inputs and business validation.
"""
from fastapi import HTTPException

from . import agent_grade_confirmation_service as grades
from . import agent_signature_confirmation_service as signatures
from . import agent_teaching_confirmation_service as teaching

_HANDLERS = {action: module for module in (grades, signatures, teaching) for action in module.ACTION_DEFINITIONS}
USER_CONFIRMATION_ACTION_DEFINITIONS = {action: module.ACTION_DEFINITIONS[action] for action,module in _HANDLERS.items()}


def user_confirmation_action_catalog(*, actor_role: str, is_super_admin: bool = False) -> list[dict]:
    return [{"action": action, **definition, "executable": False, "status": "requires_user_confirmation"}
            for action,definition in USER_CONFIRMATION_ACTION_DEFINITIONS.items()
            if actor_role in definition.get('roles', [])]


def _handler(action, user):
    if action not in _HANDLERS or user.get('role') not in USER_CONFIRMATION_ACTION_DEFINITIONS[action]['roles']:
        raise HTTPException(403, '当前身份不能执行该业务确认。')
    return _HANDLERS[action]


def prepare_user_confirmation(conn, *, action: str, params: dict, user: dict) -> dict:
    return _handler(action,user).prepare_user_confirmation(conn,action=action,params=params,user=user)


def dispatch_user_confirmation(conn, *, user: dict, source_session_id: str, task_id: int,
                               operation_id: str, action: str, params: dict, confirmation_inputs: dict) -> dict:
    return _handler(action,user).dispatch_user_confirmation(conn,user=user,source_session_id=source_session_id,
        task_id=task_id,operation_id=operation_id,action=action,params=params,confirmation_inputs=confirmation_inputs)
