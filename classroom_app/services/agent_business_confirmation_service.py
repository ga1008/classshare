"""Lock a human business confirmation against account transition and logout.

For domain confirmations which do not change accounts or their authorization.
Account-management confirmations have their own multi-account lock protocol.
The caller owns the transaction and may only take domain locks after this
function; task -> actor transition -> source session -> domain -> receipt.
"""
from fastapi import HTTPException


def claim_business_confirmation(conn, *, user, source_session_id, task_id, operation_id, action, params):
    from .account_credentials_service import lock_actor_authorization_transition
    from .agent_actor_service import task_actor_identity
    from .agent_operation_service import claim_user_agent_operation

    cursor = conn.execute("UPDATE agent_tasks SET status=status WHERE id=? AND status IN ('completed','failed','canceled')", (task_id,))
    if cursor.rowcount != 1:
        raise HTTPException(409, '只有已结束任务的业务提案可由本人确认。')
    row = dict(conn.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone())
    role, actor_id = task_actor_identity(row)
    if user.get('role') != role or user.get('id') != actor_id:
        raise HTTPException(403, '不能确认其他用户任务的操作。')
    lock_actor_authorization_transition(conn, role=role, user_id=actor_id)
    # Logout/session replacement writes this same row. Never infer a valid
    # session from the UPDATE: claim's fresh source check remains authoritative.
    conn.execute('UPDATE user_sessions SET session_id=session_id WHERE session_user_key=?', (f'{role}:{actor_id}',))
    return claim_user_agent_operation(conn, user=user, source_session_id=source_session_id,
        task_id=task_id, operation_id=operation_id, action=action, params=params)
