from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import HTTPException

from ..config import AGENT_TASK_MAX_RUNTIME_SECONDS, AGENT_TASK_RUNTIME_POLL_SECONDS
from ..database import get_db_connection
from .agent_task_service import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    append_task_event,
    finish_agent_task,
    utcnow_iso,
)
from .session_material_generation_service import (
    ACTIVE_TASK_STATUSES as MATERIAL_ACTIVE_TASK_STATUSES,
    TASK_STATUS_COMPLETED as MATERIAL_TASK_COMPLETED,
    create_generation_task,
    get_teacher_session_with_material_state,
    normalize_document_type,
    normalize_requirement_text,
    run_generation_task,
)


def _load_json(raw_value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw_value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_text(value: Any, *, max_chars: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _set_agent_runtime_state(
    conn,
    task_id: int,
    *,
    provider: str = "lanshare-session-material",
    runtime_status: str,
) -> None:
    conn.execute(
        """
        UPDATE agent_tasks
        SET runtime_provider = ?, runtime_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (provider, runtime_status, utcnow_iso(), int(task_id)),
    )


def _target_from_context(task: dict[str, Any]) -> dict[str, Any]:
    context = _load_json(task.get("context_snapshot_json"))
    server_context = context.get("server_context") or {}
    target = server_context.get("lesson_document_target") or {}
    return target if isinstance(target, dict) else {}


def _generation_task_row(conn, generation_task_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM session_material_generation_tasks WHERE id = ? LIMIT 1",
        (int(generation_task_id),),
    ).fetchone()
    return dict(row) if row else None


async def _wait_generation_task(generation_task_id: int) -> dict[str, Any] | None:
    started_at = time.monotonic()
    while True:
        with get_db_connection() as conn:
            row = _generation_task_row(conn, generation_task_id)
        if not row:
            return None
        if str(row.get("status") or "").lower() not in MATERIAL_ACTIVE_TASK_STATUSES:
            return row
        if time.monotonic() - started_at > AGENT_TASK_MAX_RUNTIME_SECONDS:
            return row
        await asyncio.sleep(max(1, AGENT_TASK_RUNTIME_POLL_SECONDS))


def _finish_missing_target(task_id: int) -> None:
    with get_db_connection() as conn:
        _set_agent_runtime_state(conn, task_id, runtime_status="failed")
        finish_agent_task(
            conn,
            task_id,
            status=TASK_STATUS_FAILED,
            result_summary="未能定位要生成学习文档的课堂课时。",
            error_message="请在课堂时间轴选中目标课时，或在任务要求中明确写出第几次课。",
            result_detail={"platform_action": "lesson_document_generation", "reason": "missing_target"},
        )


async def _execute_lesson_document_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    teacher_id = int(task["teacher_id"])
    instruction = _safe_text(task.get("private_instruction"), max_chars=4000)
    target = _target_from_context(task)
    class_offering_id = int(target.get("class_offering_id") or 0)
    session_id = int(target.get("id") or target.get("session_id") or 0)
    if not class_offering_id or not session_id:
        _finish_missing_target(task_id)
        return

    with get_db_connection() as conn:
        session_item = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=teacher_id,
        )
        if not session_item:
            _set_agent_runtime_state(conn, task_id, runtime_status="failed")
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                result_summary="未找到可操作的课堂课时。",
                error_message="目标课时不存在，或当前教师没有该课堂的权限。",
                result_detail={
                    "platform_action": "lesson_document_generation",
                    "class_offering_id": class_offering_id,
                    "session_id": session_id,
                },
            )
            return

        _set_agent_runtime_state(conn, task_id, runtime_status="preparing")
        append_task_event(
            conn,
            task_id,
            "platform_action_started",
            (
                f"已定位到第 {session_item.get('order_index')} 次课"
                f"《{session_item.get('title') or '未命名课时'}》，准备生成并绑定学习文档。"
            ),
            {
                "platform_action": "lesson_document_generation",
                "class_offering_id": class_offering_id,
                "session_id": session_id,
                "target_reason": target.get("reason") or "",
                "previous_bound_count": int(target.get("previous_bound_count") or 0),
            },
            commit=False,
        )

        existing_task = session_item.get("material_generation_task")
        if existing_task and existing_task.get("is_active"):
            generation_task = existing_task
            already_running = True
            append_task_event(
                conn,
                task_id,
                "generation_task_attached",
                "该课时已有学习文档生成任务在执行，任务中心将接管观察结果。",
                {"generation_task_id": generation_task.get("id")},
                commit=False,
            )
        else:
            document_type = normalize_document_type(
                "课堂学习文档",
                session_title=session_item.get("title") or "",
                session_content=session_item.get("content") or "",
            )
            requirement_text = normalize_requirement_text(
                f"{instruction}\n\n由任务中心 Agent 发起：请读取目标课时之前已绑定的学习文档，延续结构与风格，生成当前目标课时文档并自动绑定。"
            )
            generation_task = create_generation_task(
                conn,
                class_offering_id=class_offering_id,
                session_id=session_id,
                teacher_id=teacher_id,
                trigger_mode="auto",
                document_type=document_type,
                requirement_text=requirement_text,
                example_documents=[],
            )
            already_running = bool(generation_task.get("already_running"))
            append_task_event(
                conn,
                task_id,
                "generation_task_created",
                f"已创建课时学习文档生成任务 #{generation_task.get('id')}，开始读取前序文档并生成材料。",
                {"generation_task_id": generation_task.get("id"), "document_type": document_type},
                commit=False,
            )
        _set_agent_runtime_state(conn, task_id, runtime_status="generation_running")
        conn.commit()

    generation_task_id = int(generation_task.get("id") or 0)
    if not already_running:
        await run_generation_task(generation_task_id)
    final_generation_row = await _wait_generation_task(generation_task_id)

    with get_db_connection() as conn:
        final_session = get_teacher_session_with_material_state(
            conn,
            class_offering_id=class_offering_id,
            session_id=session_id,
            teacher_id=teacher_id,
        )
        final_task = (final_session or {}).get("material_generation_task") or {}
        if not final_task and final_generation_row:
            final_task = final_generation_row

        status = str((final_generation_row or final_task).get("status") or "").lower()
        generated_material_id = int((final_generation_row or {}).get("generated_material_id") or 0) or (
            final_task.get("generated_material_id")
        )
        generated_path = _safe_text(
            (final_generation_row or {}).get("generated_material_path")
            or final_task.get("generated_material_path")
            or (final_session or {}).get("learning_material_path")
        )
        detail = {
            "platform_action": "lesson_document_generation",
            "class_offering_id": class_offering_id,
            "session_id": session_id,
            "session_order_index": (final_session or session_item or {}).get("order_index"),
            "session_title": (final_session or session_item or {}).get("title") or "",
            "generation_task": final_task,
            "generated_material_id": generated_material_id,
            "generated_material_path": generated_path,
            "generated_material_viewer_url": (
                final_task.get("generated_material_viewer_url")
                or ((final_session or {}).get("learning_material_viewer_url") or "")
            ),
            "target": target,
        }

        if status == MATERIAL_TASK_COMPLETED and generated_material_id:
            _set_agent_runtime_state(conn, task_id, runtime_status="completed")
            append_task_event(
                conn,
                task_id,
                "platform_action_completed",
                f"学习文档已生成并绑定到第 {detail['session_order_index']} 次课：{generated_path}",
                detail,
                commit=False,
            )
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_COMPLETED,
                result_summary=(
                    f"已成功生成并绑定第 {detail['session_order_index']} 次课"
                    f"《{detail['session_title'] or '未命名课时'}》的学习文档：{generated_path}"
                ),
                result_detail=detail,
            )
            return

        error_message = _safe_text(
            (final_generation_row or final_task).get("error_message"),
            max_chars=1200,
        ) or "学习文档生成任务结束，但没有生成可绑定的 Markdown 文档。"
        _set_agent_runtime_state(conn, task_id, runtime_status="failed")
        append_task_event(
            conn,
            task_id,
            "platform_action_failed",
            f"学习文档生成未完成：{error_message}",
            detail,
            commit=False,
        )
        finish_agent_task(
            conn,
            task_id,
            status=TASK_STATUS_FAILED,
            result_summary="学习文档生成未完成。",
            error_message=error_message,
            result_detail=detail,
        )


async def try_execute_platform_agent_task(task: dict[str, Any]) -> bool:
    if str(task.get("task_type") or "") != "lesson_document":
        return False
    task_id = int(task["id"])
    try:
        await _execute_lesson_document_task(task)
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        with get_db_connection() as conn:
            _set_agent_runtime_state(conn, task_id, runtime_status="failed")
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                result_summary="学习文档业务执行失败。",
                error_message=_safe_text(error_message, max_chars=1200) or "未知错误",
                result_detail={"platform_action": "lesson_document_generation"},
            )
    return True
