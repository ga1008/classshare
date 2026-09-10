"""Count at most four admitted child starts across ALL attempts of one task.

No credential grants separate model or platform rights. Finish is an untrusted
runtime observation: it never refunds an ordinal or verifies a child stopped.
The caller owns commit. Each admission serializes with attempt/cancel transitions.
"""
import time
import uuid

from fastapi import HTTPException

from .agent_delegation_service import verify_task_delegation


def _uuid(value):
    try:
        result = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "子任务引用必须为规范 UUID。") from None
    if value != result:
        raise HTTPException(400, "子任务引用必须为规范 UUID。")
    return result


def _public(row):
    return {"id": row["id"], "admitted": True, "request_id": row["request_id"],
            "child_session_id": row["child_session_id"], "ordinal": int(row["ordinal"]), "total_limit": 4,
            "runtime_reported_status": row["runtime_reported_status"],
            "host_execution_verified": False, "capacity_refunded": False}


def admit_child(conn, token, *, request_id, parent_session_id, child_session_id, depth):
    key, parent, child = map(_uuid, (request_id, parent_session_id, child_session_id))
    if type(depth) is not int or depth != 1 or parent == child:
        raise HTTPException(400, "工作流只允许一层子任务。")
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope="platform:read", lock_task=True)
    rows = [dict(row) for row in conn.execute("SELECT * FROM agent_task_children WHERE task_id=? ORDER BY ordinal", (grant.task["id"],)).fetchall()]
    for row in rows:
        if row["request_id"] == key:
            if (row["attempt_id"] != grant.attempt["id"] or row["delegation_id"] != grant.delegation["id"]
                    or int(row["fencing_token"]) != int(grant.attempt["fencing_token"])
                    or row["parent_session_id"] != parent or row["child_session_id"] != child):
                raise HTTPException(409, "子任务准入编号已绑定其他执行内容。")
            return _public(row)
    if len(rows) >= 4:
        raise HTTPException(429, "本任务累计子任务准入已达 4 次；重试或切换执行器不会重置额度。")
    if any(row["child_session_id"] in (parent, child) for row in rows):
        raise HTTPException(409, "不能重用子任务身份或从子任务继续委派。")
    if any(row["attempt_id"] == grant.attempt["id"] and row["parent_session_id"] != parent for row in rows):
        raise HTTPException(409, "当前执行只允许一个已绑定的父会话。")
    identifier = str(uuid.uuid4())
    conn.execute("""INSERT INTO agent_task_children(id,task_id,attempt_id,fencing_token,delegation_id,
        actor_role,actor_id,request_id,parent_session_id,child_session_id,ordinal,depth,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (identifier, grant.task["id"], grant.attempt["id"],
        grant.attempt["fencing_token"], grant.delegation["id"], grant.actor.role, grant.actor.id,
        key, parent, child, len(rows) + 1, 1, int(time.time())))
    return _public(conn.execute("SELECT * FROM agent_task_children WHERE id=?", (identifier,)).fetchone())


def report_child_finish(conn, token, identifier, *, status):
    identifier = _uuid(identifier)
    if not isinstance(status, str) or status not in {"completed", "aborted", "error"}:
        raise HTTPException(400, "子任务观察状态无效。")
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope="platform:read", lock_task=True)
    row = conn.execute("SELECT * FROM agent_task_children WHERE id=? AND task_id=?", (identifier, grant.task["id"])).fetchone()
    if (not row or row["attempt_id"] != grant.attempt["id"] or row["delegation_id"] != grant.delegation["id"]
            or int(row["fencing_token"]) != int(grant.attempt["fencing_token"])
            or row["actor_role"] != grant.actor.role or int(row["actor_id"]) != grant.actor.id):
        raise HTTPException(404, "当前任务执行无此子任务准入记录。")
    if row["runtime_reported_at"] is not None and row["runtime_reported_status"] != status:
        raise HTTPException(409, "子任务观察结果已记录，不能改写。")
    conn.execute("""UPDATE agent_task_children SET runtime_reported_status=?,runtime_reported_at=?
        WHERE id=? AND runtime_reported_at IS NULL""", (status, int(time.time()), identifier))
    return _public(conn.execute("SELECT * FROM agent_task_children WHERE id=?", (identifier,)).fetchone())
