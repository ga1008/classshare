"""Durable scheduler delivery for ordinary session document generation."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine
from .scheduled_task_service import schedule_task
from . import session_material_generation_service as generation


SESSION_GENERATION_TASK_KIND = "session_material_generate"


def create_scheduled_generation_task(conn, *, teacher_id: int, class_offering_id: int, session_id: int,
                                     trigger_mode: str, document_type: str, requirement_text: str,
                                     example_documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Create job and delivery in the caller transaction; do not run the model."""
    if get_configured_db_engine() == "postgres":
        conn.execute("SELECT id FROM class_offering_sessions WHERE id=? FOR UPDATE", (int(session_id),)).fetchone()
    elif not getattr(conn, "in_transaction", False):
        conn.execute("BEGIN IMMEDIATE")
    session = conn.execute("""SELECT s.title,s.content FROM class_offering_sessions s
        JOIN class_offerings o ON o.id=s.class_offering_id
        JOIN teachers t ON t.id=o.teacher_id AND COALESCE(t.is_active,1)=1
        WHERE s.id=? AND s.class_offering_id=? AND o.teacher_id=?""", (int(session_id), int(class_offering_id), int(teacher_id))).fetchone()
    if not session:
        raise HTTPException(404, "课堂节点不存在或无权操作")
    mode = str(trigger_mode or "guided").strip().lower()
    if mode not in {"guided", "auto"}:
        raise HTTPException(400, "生成方式必须是 guided 或 auto")
    task = generation.create_generation_task(conn, class_offering_id=int(class_offering_id), session_id=int(session_id), teacher_id=int(teacher_id),
             trigger_mode=mode, document_type=generation.normalize_document_type(document_type, session_title=session["title"], session_content=session["content"]),
             requirement_text=generation.normalize_requirement_text(requirement_text), example_documents=example_documents)
    task["delivery_task_id"] = schedule_task(conn, task_kind=SESSION_GENERATION_TASK_KIND, run_at=datetime.now(),
             payload={"generation_task_id": int(task["id"]), "teacher_id": int(teacher_id)}, dedupe_key=f"session-material-generate:{task['id']}",
             owner_role="teacher", owner_user_pk=int(teacher_id), title="生成课时学习文档", max_attempts=5, priority=100, replace=False)
    return task


async def handle_session_material_generation(task: dict[str, Any]) -> str:
    from ..database import get_db_connection

    payload = task.get("payload") or {}
    task_id = int(payload.get("generation_task_id") or 0)
    with get_db_connection() as conn:
        row = generation._ensure_generation_task_authority(conn, task_id)
        if int(row["teacher_id"]) != int(payload.get("teacher_id") or 0):
            raise HTTPException(403, "课时任务执行身份不一致")
        if row["status"] == generation.TASK_STATUS_COMPLETED:
            return "completed: existing session material receipt"
        if row["status"] == generation.TASK_STATUS_RUNNING:
            generation.expire_stale_generation_tasks(conn)
            conn.commit()
            raise RuntimeError("课时文档生成尚未完成，请查看生成任务状态")
        if row["status"] != generation.TASK_STATUS_QUEUED:
            raise RuntimeError("课时生成任务已失败，请在课堂查看原因并重新发起")
    await generation.run_generation_task(task_id)
    with get_db_connection() as conn:
        row = conn.execute("SELECT status,generated_material_id FROM session_material_generation_tasks WHERE id=?", (task_id,)).fetchone()
    if row and row["status"] == generation.TASK_STATUS_COMPLETED and row["generated_material_id"]:
        return f"completed: material {row['generated_material_id']}"
    raise RuntimeError("课时生成任务没有成功材料回执，请查看任务状态")
