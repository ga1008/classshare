"""Agent 全平台队列控制：暂停/恢复队列、挂起/暂停/恢复/停止单个任务、清空队列。

- 队列级：``queue_paused`` 开关（超管）。暂停后执行器不再领取新任务，
  正在执行的任务不受影响（需要时逐个停止）。
- 任务级：
  * 未开始的排队任务 → ``held``（挂起，超管）；
  * 已开始的任务 → ``paused``：正在执行时先记 ``pause_requested``，执行器在下一个
    安全点保存对话并让出执行槽；
  * 恢复：``held`` 回到原队列位置；``paused`` 进入 ``resume_pending``，
    以更高优先级优先继续；
  * 停止 = 取消（超管可停止任何人的任务，本人只能停止自己的）。
- 清空队列：取消所有尚未开始的排队任务；可选同时取消已暂停/等待回答的任务。

所有状态都写入 ``agent_task_events``，任务所有者在 Agent 窗口里能看到是谁、何时做了什么。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .agent_runtime_schema import ensure_agent_runtime_schema
from .agent_task_service import (
    PARKED_RUNTIME_STATUSES,
    RESUME_PRIORITY,
    RUNTIME_HELD,
    RUNTIME_PAUSED,
    RUNTIME_RESUME_PENDING,
    RUNTIME_WAITING_INPUT,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    _is_task_owner,
    append_task_event,
    cancel_agent_task,
    serialize_agent_task,
    utcnow_iso,
)

QUEUE_PAUSED_KEY = "queue_paused"


def _control(conn, key: str) -> dict[str, Any] | None:
    ensure_agent_runtime_schema(conn)
    row = conn.execute(
        "SELECT control_value, updated_by, updated_by_name, updated_at FROM agent_queue_controls WHERE control_key = ?",
        (key,),
    ).fetchone()
    return dict(row) if row else None


def queue_pause_state(conn) -> dict[str, Any]:
    row = _control(conn, QUEUE_PAUSED_KEY)
    if not row or str(row.get("control_value") or "") != "1":
        return {"paused": False}
    return {"paused": True, "by_name": row.get("updated_by_name") or "", "at": row.get("updated_at") or ""}


def is_queue_paused(conn) -> bool:
    return bool(queue_pause_state(conn).get("paused"))


def set_queue_paused(conn, *, paused: bool, admin: dict[str, Any]) -> dict[str, Any]:
    ensure_agent_runtime_schema(conn)
    now = utcnow_iso()
    value = "1" if paused else "0"
    existing = conn.execute("SELECT 1 FROM agent_queue_controls WHERE control_key = ?", (QUEUE_PAUSED_KEY,)).fetchone()
    params = (value, int(admin["id"]), str(admin.get("name") or ""), now, QUEUE_PAUSED_KEY)
    if existing:
        conn.execute("UPDATE agent_queue_controls SET control_value=?, updated_by=?, updated_by_name=?, updated_at=? "
                     "WHERE control_key=?", params)
    else:
        conn.execute("INSERT INTO agent_queue_controls (control_value, updated_by, updated_by_name, updated_at, control_key) "
                     "VALUES (?, ?, ?, ?, ?)", params)
    conn.commit()
    return queue_pause_state(conn)


def _load_task(conn, task_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在。")
    return dict(row)


def _authorize(task: dict[str, Any], user: dict[str, Any], *, as_admin: bool) -> None:
    if as_admin:
        return
    if not _is_task_owner(task, int(user["id"]), str(user.get("role") or "teacher")):
        raise HTTPException(403, "只能操作自己的任务。")


def _who(user: dict[str, Any], as_admin: bool) -> str:
    return f"超级管理员{user.get('name') or ''}" if as_admin else "你"


def _serialize(conn, task_id: int, user: dict[str, Any]) -> dict[str, Any]:
    return serialize_agent_task(_load_task(conn, task_id), viewer_teacher_id=int(user["id"]),
                                viewer_role=str(user.get("role") or "teacher"))


def pause_task(conn, task_id: int, *, user: dict[str, Any], as_admin: bool = False) -> dict[str, Any]:
    task = _load_task(conn, task_id)
    _authorize(task, user, as_admin=as_admin)
    status = str(task.get("status") or "")
    runtime = str(task.get("runtime_status") or "")
    now = utcnow_iso()
    if status == TASK_STATUS_QUEUED:
        if runtime in PARKED_RUNTIME_STATUSES:
            return _serialize(conn, task_id, user)
        if not as_admin and not task.get("started_at"):
            raise HTTPException(409, "排队中的任务可以直接取消；挂起排队任务需要超级管理员。")
        target = RUNTIME_PAUSED if task.get("started_at") else RUNTIME_HELD
        conn.execute("UPDATE agent_tasks SET runtime_status=?, updated_at=? WHERE id=? AND status=?",
                     (target, now, int(task_id), TASK_STATUS_QUEUED))
        append_task_event(conn, int(task_id), "task_paused", f"{_who(user, as_admin)}暂停了该任务，恢复前不会占用执行队列。",
                          {"by_admin": as_admin, "runtime_status": target}, commit=False)
    elif status == TASK_STATUS_RUNNING:
        ensure_agent_runtime_schema(conn)
        by = "admin" if as_admin else "owner"
        if conn.execute("SELECT 1 FROM agent_run_states WHERE task_id=?", (int(task_id),)).fetchone():
            conn.execute("UPDATE agent_run_states SET pause_requested=1, pause_requested_by=?, updated_at=? WHERE task_id=?",
                         (by, now, int(task_id)))
        else:
            conn.execute("INSERT INTO agent_run_states (task_id, pause_requested, pause_requested_by, updated_at) VALUES (?, 1, ?, ?)",
                         (int(task_id), by, now))
        append_task_event(conn, int(task_id), "pause_requested",
                          f"{_who(user, as_admin)}请求暂停，Agent 会在当前步骤完成后保存进度并让出队列。",
                          {"by_admin": as_admin}, commit=False)
    else:
        raise HTTPException(409, "任务已结束，无法暂停。")
    conn.commit()
    return _serialize(conn, task_id, user)


def resume_task(conn, task_id: int, *, user: dict[str, Any], as_admin: bool = False) -> dict[str, Any]:
    task = _load_task(conn, task_id)
    _authorize(task, user, as_admin=as_admin)
    status = str(task.get("status") or "")
    runtime = str(task.get("runtime_status") or "")
    now = utcnow_iso()
    if status == TASK_STATUS_RUNNING:
        # A pause requested but not yet honoured is simply withdrawn.
        ensure_agent_runtime_schema(conn)
        conn.execute("UPDATE agent_run_states SET pause_requested=0, pause_requested_by='', updated_at=? WHERE task_id=?",
                     (now, int(task_id)))
        append_task_event(conn, int(task_id), "pause_withdrawn", "暂停请求已撤回，任务继续执行。", {"by_admin": as_admin}, commit=False)
    elif status == TASK_STATUS_QUEUED and runtime == RUNTIME_HELD:
        conn.execute("UPDATE agent_tasks SET runtime_status='', updated_at=? WHERE id=?", (now, int(task_id)))
        append_task_event(conn, int(task_id), "task_resumed", f"{_who(user, as_admin)}恢复了该任务，已回到队列。",
                          {"by_admin": as_admin}, commit=False)
    elif status == TASK_STATUS_QUEUED and runtime == RUNTIME_PAUSED:
        conn.execute("UPDATE agent_tasks SET runtime_status=?, priority=CASE WHEN priority < ? THEN ? ELSE priority END, "
                     "updated_at=? WHERE id=?", (RUNTIME_RESUME_PENDING, RESUME_PRIORITY, RESUME_PRIORITY, now, int(task_id)))
        append_task_event(conn, int(task_id), "task_resumed", f"{_who(user, as_admin)}恢复了该任务，将优先继续执行。",
                          {"by_admin": as_admin}, commit=False)
    elif status == TASK_STATUS_QUEUED and runtime == RUNTIME_WAITING_INPUT:
        raise HTTPException(409, "任务正在等待回答，请回答 Agent 的疑问后自动继续。")
    else:
        raise HTTPException(409, "该任务当前不需要恢复。")
    conn.commit()
    return _serialize(conn, task_id, user)


def stop_task(conn, task_id: int, *, admin: dict[str, Any]) -> dict[str, Any]:
    return cancel_agent_task(conn, int(task_id), teacher_id=int(admin["id"]), actor_role="teacher",
                             as_admin=True, admin_name=str(admin.get("name") or ""))


def clear_queue(conn, *, admin: dict[str, Any], include_parked: bool = False) -> dict[str, Any]:
    """Cancel queued work. Started-and-parked tasks are kept unless include_parked."""
    condition = "status = 'queued'"
    if not include_parked:
        condition += " AND started_at IS NULL AND COALESCE(runtime_status, '') NOT IN ('waiting_input', 'paused')"
    ids = [int(row["id"]) for row in conn.execute(f"SELECT id FROM agent_tasks WHERE {condition} ORDER BY id").fetchall()]
    now = utcnow_iso()
    for task_id in ids:
        conn.execute("UPDATE agent_tasks SET status='canceled', cancel_requested_at=?, completed_at=?, updated_at=? "
                     "WHERE id=? AND status='queued'", (now, now, now, task_id))
        append_task_event(conn, task_id, "canceled", f"超级管理员{admin.get('name') or ''}清空了 Agent 队列，该任务已取消。",
                          {"by_admin": True, "queue_cleared": True}, commit=False)
    conn.commit()
    return {"canceled_count": len(ids), "task_ids": ids}


def admin_queue_overview(conn, *, admin: dict[str, Any], limit: int = 60) -> dict[str, Any]:
    """Every active task across the platform with owner names (super admin only).

    Super admins manage the queue: they see who, what kind and the short title,
    never the private instruction or results (serialize_agent_task hides them)."""
    from .agent_task_service import _queue_positions, get_agent_queue_state

    rows = [dict(row) for row in conn.execute(
        """
        SELECT * FROM agent_tasks
        WHERE status IN ('running', 'queued')
        ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, priority DESC, created_at ASC, id ASC
        LIMIT ?
        """,
        (max(1, min(int(limit), 200)),),
    ).fetchall()]
    positions = _queue_positions(conn)
    items = []
    for row in rows:
        row["queue_position"] = positions.get(int(row["id"]), 0)
        item = serialize_agent_task(row, viewer_teacher_id=int(admin["id"]), viewer_role="teacher")
        item["owner_name"] = row.get("teacher_name") or "某位老师"
        items.append(item)
    return {"tasks": items, "queue_state": get_agent_queue_state(conn, viewer_teacher_id=int(admin["id"]), viewer_role="teacher")}
