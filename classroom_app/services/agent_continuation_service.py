"""Owner-bound continuation context; a previous receipt never authorizes a write."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from .agent_actor_service import task_actor_identity


def continuation_context(parent: dict[str, Any]) -> dict[str, Any]:
    try:
        detail = json.loads(parent.get("result_detail_json") or "{}")
    except (TypeError, ValueError):
        detail = {}
    detail = detail if isinstance(detail, dict) else {}
    return {
        "parent_task_id": int(parent["id"]),
        "parent_instruction": str(parent.get("private_instruction") or "")[:4000],
        "parent_result_summary": str(parent.get("result_summary") or "")[:1600],
        "parent_status": str(parent.get("status") or ""),
        "previous_delivery_excerpt": str(detail.get("deliverable_markdown") or "")[:8000],
        "previous_completion_kind": detail.get("completion_kind"),
        "receipt_lookup": "platform_task_context",
        "continuation_rule": "先读取父任务业务回执与产物；已提交操作不重复提交，异步领域任务按原任务编号继续查询。",
    }


def resolve_continuation_task(conn, grant, requested_id: int | None = None) -> dict[str, Any]:
    current = dict(grant.task)
    target = int(current["id"]) if requested_id is None else requested_id
    if type(target) is not int or not 1 <= target <= 2**63 - 1:
        raise HTTPException(400, "任务编号无效。")
    visited = set()
    for _ in range(16):
        if task_actor_identity(current) != (grant.actor.role, grant.actor.id):
            break
        if int(current["id"]) == target:
            if target != int(grant.task["id"]) and current.get("status") not in {"completed", "failed", "canceled"}:
                raise HTTPException(409, "上次任务尚未结束，不能读取其运行中工作区。")
            return current
        parent_id = current.get("parent_task_id")
        if not parent_id or int(parent_id) in visited:
            break
        visited.add(int(parent_id))
        row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (int(parent_id),)).fetchone()
        if row is None:
            break
        current = dict(row)
    raise HTTPException(403, "只允许读取本任务及同一账号的直接续接历史。")


def read_task_continuation(conn, grant, *, task_id: int | None = None, offset: int = 0, limit: int = 5) -> dict[str, Any]:
    from .agent_task_service import collect_task_workspace_artifacts

    task = resolve_continuation_task(conn, grant, task_id)
    if type(offset) is not int or not 0 <= offset <= 10000 or type(limit) is not int or not 1 <= limit <= 5:
        raise HTTPException(400, "回执分页范围无效。")
    rows = conn.execute(
        "SELECT operation_id,action,status,result_json,error_code,completed_at FROM agent_action_executions "
        "WHERE task_id=? AND actor_role=? AND actor_id=? ORDER BY created_at,id LIMIT ? OFFSET ?",
        (int(task["id"]), grant.actor.role, grant.actor.id, limit + 1, offset),
    ).fetchall()
    operations = []
    for row in rows[:limit]:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json") or "{}")
        operations.append(item)
    requests = conn.execute(
        "SELECT id AS request_id,operation_id,capability_key,status,result_json,settled_at,host_execution_finished_at,"
        "reconciliation_status,reconciliation_resolution,reconciled_at,reconciled_by_role,reconciled_by_id,reconciliation_note "
        "FROM agent_platform_requests WHERE task_id=? AND actor_role=? AND actor_id=? "
        "ORDER BY created_at,id LIMIT ? OFFSET ?",
        (int(task["id"]), grant.actor.role, grant.actor.id, limit + 1, offset),
    ).fetchall()
    observations = []
    for row in requests[:limit]:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json") or "{}")
        item["reconciliation"] = {
            "status": item.pop("reconciliation_status"), "resolution": item.pop("reconciliation_resolution"),
            "at": item.pop("reconciled_at"), "by": {"role": item.pop("reconciled_by_role"), "id": item.pop("reconciled_by_id")},
            "note": item.pop("reconciliation_note") or "", "verified_business": False,
        }
        item.update(verified_business=False, automatic_retry_allowed=False)
        observations.append(item)
    context = continuation_context(task)
    # Current runner files may be changing. Prior task files are inspected only
    # after the stopped task's owner and ancestry checks above have succeeded.
    artifacts = collect_task_workspace_artifacts(int(task["id"])) if task["status"] in {"completed", "failed", "canceled"} else []
    return {"task_id": int(task["id"]), "status": task["status"], "context": context,
            "operations": operations, "has_more_operations": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None, "artifacts": artifacts,
            "platform_requests": observations, "has_more_platform_requests": len(requests) > limit,
            "next_request_offset": offset + limit if len(requests) > limit else None,
            "note": "completed业务回执表示该操作已提交；异步生成须查询原任务最终状态。普通HTTP观察回执不证明业务最终完成，不确定结果不得重复提交。reconciliation是用户独立核对声明，不改写HTTP事实；只有核对后的新任务、新授权可以按当前用户要求继续，不能自动重放。历史读取不授予新权限。"}
