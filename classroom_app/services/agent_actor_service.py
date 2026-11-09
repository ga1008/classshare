"""Resolve a task's live platform identity without trusting model context.

No login cookies, password hashes or infrastructure credentials are returned.
This is an identity resolver, not an authorization shortcut: each business tool
still calls the same resource/action policy as the corresponding platform UI.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from .organization_scope_service import load_teacher_org_memberships, org_scope_from_row
from .student_lifecycle_service import STUDENT_STATUS_ACTIVE, normalize_student_enrollment_status


@dataclass(frozen=True)
class AgentActor:
    role: str
    id: int
    name: str
    is_super_admin: bool
    memberships: tuple[dict[str, str], ...]
    class_id: int | None = None
    wanderer_id: int | None = None
    life_epoch: int = 0
    authority_version: int = 0

    @property
    def key(self) -> str:
        return f"{self.role}:{self.id}"

    def as_user(self) -> dict[str, Any]:
        result = {"id": self.id, "role": self.role, "name": self.name}
        if self.wanderer_id is not None:
            result.update(is_wanderer=True, wanderer_id=self.wanderer_id, control_mode="ai",
                          life_epoch=self.life_epoch, authority_version=self.authority_version)
        return result

    def public_context(self) -> dict[str, Any]:
        return {
            **self.as_user(),
            "actor_key": self.key,
            "is_super_admin": self.is_super_admin,
            "organizations": [dict(scope) for scope in self.memberships],
            "class_id": self.class_id,
        }

    @property
    def authority_fingerprint(self) -> str:
        # Display names and ordering do not change authority. Revoked admin or
        # membership/class privileges do. This is a delegation invalidation
        # input, never a replacement for per-resource authorization.
        scopes = sorted({
            (item.get("school_code", ""), item.get("college", ""), item.get("department", ""))
            for item in self.memberships
        })
        payload = [self.key, self.is_super_admin, self.class_id, scopes]
        if self.wanderer_id is not None:
            payload.extend(["ai", self.wanderer_id, self.life_epoch, self.authority_version])
        return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def resolve_agent_actor(conn, role: str, actor_id: Any, *, service_wanderer: dict | None = None) -> AgentActor:
    normalized_role = str(role or "").strip().lower()
    try:
        if isinstance(actor_id, bool):
            raise ValueError
        pk = int(actor_id)
        if pk <= 0 or str(pk) != str(actor_id).strip():
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Agent 执行身份无效。") from None
    if normalized_role == "teacher":
        row = conn.execute(
            "SELECT id, name, COALESCE(is_active, 1) AS is_active, "
            "COALESCE(is_super_admin, 0) AS is_super_admin FROM teachers WHERE id = ? LIMIT 1",
            (pk,),
        ).fetchone()
        if not row or int(row["is_active"] or 0) != 1:
            raise HTTPException(status_code=403, detail="任务所属教师账号已停用或不存在。")
        return AgentActor(
            role=normalized_role, id=pk, name=str(row["name"] or ""),
            is_super_admin=int(row["is_super_admin"] or 0) == 1,
            memberships=tuple(load_teacher_org_memberships(conn, pk)),
        )
    if normalized_role == "student":
        row = conn.execute(
            "SELECT id, name, class_id, school_code, school_name, college, department, "
            "COALESCE(enrollment_status, 'active') AS enrollment_status "
            "FROM students WHERE id = ? LIMIT 1", (pk,),
        ).fetchone()
        if not row or normalize_student_enrollment_status(row["enrollment_status"]) != STUDENT_STATUS_ACTIVE:
            raise HTTPException(status_code=403, detail="任务所属学生账号已停用或不存在。")
        from .account_control_service import student_is_ai_controlled
        is_ai = student_is_ai_controlled(conn, pk)
        if is_ai and (service_wanderer is None or int(service_wanderer.get("student_id") or 0) != pk):
            raise HTTPException(403, "AI 专管账号必须通过散修运行授权执行。")
        if service_wanderer is not None and not is_ai:
            raise HTTPException(403, "散修运行身份与账号控制方式不一致。")
        return AgentActor(
            role=normalized_role, id=pk, name=str(row["name"] or ""),
            is_super_admin=False, memberships=(org_scope_from_row(row),),
            class_id=None if is_ai else (int(row["class_id"]) if row["class_id"] is not None else None),
            wanderer_id=int(service_wanderer["id"]) if is_ai else None,
            life_epoch=int(service_wanderer.get("life_epoch") or 1) if is_ai else 0,
            authority_version=int(service_wanderer.get("authority_version") or 1) if is_ai else 0,
        )
    raise HTTPException(status_code=403, detail="Agent 仅支持有效的教师或学生身份。")


def task_actor_identity(task: dict[str, Any]) -> tuple[str, int]:
    """Read trusted task columns, with explicit compatibility for old teachers."""
    role = str(task.get("actor_role") or "teacher").strip().lower()
    actor_id = task.get("actor_id")
    if actor_id is None and role == "teacher":
        actor_id = task.get("teacher_id")
    try:
        if isinstance(actor_id, bool) or role not in {"teacher", "student"}:
            raise ValueError
        pk = int(actor_id)
        if pk <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="任务未绑定有效的执行身份。") from None
    return role, pk


def resolve_live_task_actor(conn, task_id: int) -> tuple[dict[str, Any], AgentActor]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="任务不存在或授权已撤销。")
    task = dict(row)
    # Only a running attempt may call tools. A queued, completed, canceled or
    # waiting task cannot reuse an old runtime bearer to keep reading data.
    if task.get("status") != "running" or task.get("cancel_requested_at"):
        raise HTTPException(status_code=401, detail="任务已停止执行，工具授权已撤销。")
    role, actor_id = task_actor_identity(task)
    return task, resolve_agent_actor(conn, role, actor_id)
