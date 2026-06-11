from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import AGENT_TASK_RUNTIME_URL, AGENT_TASKS_ENABLED
from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services.agent_task_service import (
    AGENT_TASK_ATTACHMENT_MAX_FILE_BYTES,
    AGENT_TASK_ATTACHMENT_MAX_FILES,
    AGENT_TASK_ATTACHMENT_MAX_TOTAL_BYTES,
    agent_workflow_catalog,
    add_task_supplement,
    append_task_event,
    cancel_agent_task,
    create_agent_task,
    create_follow_up_task,
    create_retry_task,
    delete_agent_task,
    delete_agent_task_history,
    generate_agent_task_title,
    get_agent_task,
    list_agent_tasks,
    list_task_events_after,
    mark_proposed_action_executed,
    set_agent_task_composer,
    task_type_options,
    utcnow_iso,
)

router = APIRouter(prefix="/api/agent-tasks", tags=["agent-tasks"])


def _teacher_id(user: dict[str, Any]) -> int:
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="登录状态无效，请重新登录。") from exc


@router.get("/bootstrap", response_class=JSONResponse)
def bootstrap_agent_task_center(user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, limit=30)
    return {
        "status": "success",
        "enabled": bool(AGENT_TASKS_ENABLED),
        "runtime_configured": bool(AGENT_TASK_RUNTIME_URL),
        "task_types": task_type_options(),
        "workflow_catalog": agent_workflow_catalog(),
        **queue,
    }


@router.get("", response_class=JSONResponse)
def api_list_agent_tasks(
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(get_current_teacher),
):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, limit=limit)
    return {"status": "success", **queue}


_AGENT_ATTACHMENT_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".htm", ".css", ".sql", ".log",
}
_AGENT_ATTACHMENT_DOC_EXTENSIONS = {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".xlsx", ".xls"}
_AGENT_ATTACHMENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


async def _process_agent_attachment(file) -> dict[str, Any]:
    """Agent 附件处理：保留原始字节 + 尽力抽取文本（供 runtime 直接读取）。"""
    contents = await file.read()
    filename = str(getattr(file, "filename", "") or "attachment")
    if len(contents) > AGENT_TASK_ATTACHMENT_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"附件 {filename} 超过 {AGENT_TASK_ATTACHMENT_MAX_FILE_BYTES // (1024 * 1024)}MB 上限。",
        )
    ext = Path(filename).suffix.lower()
    text = ""
    kind = "file"
    if ext in _AGENT_ATTACHMENT_IMAGE_EXTENSIONS:
        kind = "image"
    elif ext in _AGENT_ATTACHMENT_TEXT_EXTENSIONS:
        kind = "text"
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError:
            text = contents.decode("utf-8", errors="replace")
    elif ext in _AGENT_ATTACHMENT_DOC_EXTENSIONS:
        kind = "document"
        import os
        import tempfile

        from ai_assistant_doc_extract import extract_document_text

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(contents)
                tmp_path = tmp.name
            result = extract_document_text(Path(tmp_path), ext)
            text = result.text or ""
        except Exception as exc:
            print(f"[AGENT_TASK] attachment extract failed for {filename}: {exc}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    else:
        raise HTTPException(status_code=400, detail=f"暂不支持的附件类型：{filename}")
    return {"name": filename, "data": contents, "text": text, "kind": kind}


async def _parse_create_request(request: Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    content_type = str(request.headers.get("content-type") or "")
    if "multipart/form-data" not in content_type.lower():
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="请求格式错误。")
        return data, []

    form = await request.form()
    try:
        data = json.loads(str(form.get("payload") or "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="payload 字段必须是 JSON。")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式错误。")
    files = [item for item in form.getlist("files") if getattr(item, "filename", None)]
    if len(files) > AGENT_TASK_ATTACHMENT_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"单个任务最多携带 {AGENT_TASK_ATTACHMENT_MAX_FILES} 个附件。")
    items: list[dict[str, Any]] = []
    total_bytes = 0
    for file in files:
        item = await _process_agent_attachment(file)
        total_bytes += len(item["data"])
        if total_bytes > AGENT_TASK_ATTACHMENT_MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"附件总大小超过 {AGENT_TASK_ATTACHMENT_MAX_TOTAL_BYTES // (1024 * 1024)}MB 上限。",
            )
        items.append(item)
    return data, items


@router.post("", response_class=JSONResponse)
async def api_create_agent_task(
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_teacher),
):
    if not AGENT_TASKS_ENABLED:
        raise HTTPException(status_code=503, detail="任务中心暂未启用。")
    data, attachment_items = await _parse_create_request(request)
    # 来源/优先级等内部字段不接受客户端指定。
    for reserved in ("origin", "parent_task_id", "priority", "title_override", "extra_context", "attachments"):
        data.pop(reserved, None)
    with get_db_connection() as conn:
        task = create_agent_task(conn, user, data)
        if attachment_items:
            from ..services.agent_task_service import save_task_attachments

            metadata = save_task_attachments(int(task["id"]), attachment_items)
            conn.execute(
                "UPDATE agent_tasks SET attachments_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), utcnow_iso(), int(task["id"])),
            )
            append_task_event(
                conn,
                int(task["id"]),
                "attachments_saved",
                f"已接收 {len(metadata)} 个附件，Agent 执行时可直接读取。",
                {"names": [item["name"] for item in metadata]},
                commit=False,
            )
        conn.commit()
        if attachment_items:
            task = get_agent_task(conn, int(task["id"]), teacher_id=_teacher_id(user))
    background_tasks.add_task(generate_agent_task_title, int(task["id"]))
    return {"status": "success", "task": task}


@router.post("/composer", response_class=JSONResponse)
async def api_set_agent_task_composer(request: Request, user: dict = Depends(get_current_teacher)):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式错误。")
    with get_db_connection() as conn:
        queue_state = set_agent_task_composer(
            conn,
            user,
            active=bool(data.get("active")),
            page_context=data.get("page_context") if isinstance(data.get("page_context"), dict) else {},
        )
    return {"status": "success", "queue_state": queue_state}


@router.get("/subscriptions", response_class=JSONResponse)
def api_list_agent_subscriptions(user: dict = Depends(get_current_teacher)):
    from ..services.agent_subscription_service import list_agent_subscriptions

    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = list_agent_subscriptions(conn, teacher_id=teacher_id)
    return {"status": "success", **result}


@router.post("/subscriptions", response_class=JSONResponse)
async def api_set_agent_subscription(request: Request, user: dict = Depends(get_current_teacher)):
    from ..services.agent_subscription_service import set_agent_subscription

    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式错误。")
    hour = data.get("hour")
    with get_db_connection() as conn:
        result = set_agent_subscription(
            conn,
            user,
            template_key=str(data.get("template_key") or ""),
            enabled=bool(data.get("enabled")),
            hour=int(hour) if hour is not None else None,
        )
    return {"status": "success", **result}


@router.delete("/history", response_class=JSONResponse)
def api_delete_agent_task_history(user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = delete_agent_task_history(conn, teacher_id=teacher_id)
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, limit=30)
    return {"status": "success", **result, **queue}


@router.get("/{task_id}", response_class=JSONResponse)
def api_get_agent_task(task_id: int, user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id)
    return {"status": "success", "task": task}


@router.get("/{task_id}/events", response_class=JSONResponse)
def api_list_agent_task_events(
    task_id: int,
    after: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_teacher),
):
    """G1 增量过程事件（2 秒级短轮询通道，仅任务所有者）。"""
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = list_task_events_after(conn, task_id, teacher_id=teacher_id, after_event_id=after)
    return {"status": "success", **result}


@router.get("/{task_id}/stream")
async def api_stream_agent_task_events(
    task_id: int,
    request: Request,
    after: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_teacher),
):
    """G1 SSE process stream; clients fall back to /events short polling."""
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        # Validate task ownership before returning a streaming response so
        # unauthorized requests still get a normal JSON/HTTP error.
        list_task_events_after(conn, task_id, teacher_id=teacher_id, after_event_id=after)

    async def event_generator():
        last_event_id = int(after or 0)
        terminal_sent = False
        while True:
            if await request.is_disconnected():
                break
            try:
                with get_db_connection() as conn:
                    payload = list_task_events_after(
                        conn,
                        task_id,
                        teacher_id=teacher_id,
                        after_event_id=last_event_id,
                    )
            except Exception as exc:  # noqa: BLE001 - stream errors must degrade cleanly.
                safe_message = str(exc)[:200] or "Agent 过程流暂时不可用。"
                yield f"data: {json.dumps({'status': 'error', 'message': safe_message}, ensure_ascii=False)}\n\n"
                break

            if payload.get("events"):
                last_event_id = int(payload.get("last_event_id") or last_event_id)
                terminal_sent = bool(payload.get("is_terminal"))
                yield f"data: {json.dumps({'status': 'success', **payload}, ensure_ascii=False)}\n\n"
            elif payload.get("is_terminal") and not terminal_sent:
                terminal_sent = True
                yield f"data: {json.dumps({'status': 'success', **payload}, ensure_ascii=False)}\n\n"

            if payload.get("is_terminal"):
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{task_id}/follow-up", response_class=JSONResponse)
async def api_follow_up_agent_task(
    task_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    if not AGENT_TASKS_ENABLED:
        raise HTTPException(status_code=503, detail="任务中心暂未启用。")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式错误。")
    with get_db_connection() as conn:
        current = get_agent_task(conn, task_id, teacher_id=_teacher_id(user))
        if current.get("is_active"):
            task = add_task_supplement(conn, user, task_id, str(data.get("instruction") or ""))
            supplemented = True
        else:
            task = create_follow_up_task(conn, user, task_id, str(data.get("instruction") or ""))
            supplemented = False
        conn.commit()
    return {"status": "success", "task": task, "supplemented": supplemented}


@router.post("/{task_id}/retry", response_class=JSONResponse)
async def api_retry_agent_task(
    task_id: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    if not AGENT_TASKS_ENABLED:
        raise HTTPException(status_code=503, detail="任务中心暂未启用。")
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    with get_db_connection() as conn:
        task = create_retry_task(
            conn,
            user,
            task_id,
            instruction_override=str(data.get("instruction") or ""),
        )
        conn.commit()
    return {"status": "success", "task": task}


@router.post("/{task_id}/actions/{action_index}/preview", response_class=JSONResponse)
async def api_preview_agent_task_action(
    task_id: int,
    action_index: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """G3：教师确认前的参数预览，并签发短时 confirmation token。"""
    from ..services.agent_action_registry import (
        AGENT_ACTION_DEFINITIONS,
        issue_action_confirmation_token,
    )

    teacher_id = _teacher_id(user)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    edited_params = data.get("params") if isinstance(data.get("params"), dict) else {}

    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id)
        if not task.get("is_owner"):
            raise HTTPException(status_code=403, detail="只能预览自己任务的动作提案。")
        proposals = (task.get("result_detail") or {}).get("proposed_actions") or []
        if not (0 <= int(action_index) < len(proposals)):
            raise HTTPException(status_code=404, detail="动作提案不存在。")
        proposal = proposals[int(action_index)]
        if proposal.get("executed"):
            raise HTTPException(status_code=409, detail="该动作已执行过。")
        action = str(proposal.get("action") or "")
        definition = AGENT_ACTION_DEFINITIONS.get(action)
        if not definition:
            raise HTTPException(status_code=400, detail="未知动作。")
        merged_params = {**(proposal.get("params") or {}), **edited_params}
        confirmation = issue_action_confirmation_token(
            teacher_id=teacher_id,
            task_id=task_id,
            action_index=int(action_index),
            action=action,
            params=merged_params,
        )
    return {
        "status": "success",
        "action": action,
        "label": proposal.get("label") or definition["label"],
        "summary": proposal.get("summary") or definition.get("description") or "",
        "risk": definition.get("risk") or "",
        "execution_mode": definition.get("execution_mode") or "execute",
        "fields": definition.get("fields") or {},
        **confirmation,
    }


@router.post("/{task_id}/actions/{action_index}/execute", response_class=JSONResponse)
async def api_execute_agent_task_action(
    task_id: int,
    action_index: int,
    request: Request,
    user: dict = Depends(get_current_teacher),
):
    """G3：教师确认后，以教师身份执行白名单动作（全程审计）。"""
    from ..services.agent_action_registry import (
        AGENT_ACTION_DEFINITIONS,
        execute_proposed_action,
        verify_action_confirmation_token,
    )

    teacher_id = _teacher_id(user)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    edited_params = data.get("params") if isinstance(data.get("params"), dict) else {}

    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id)
        if not task.get("is_owner"):
            raise HTTPException(status_code=403, detail="只能执行自己任务的动作提案。")
        proposals = (task.get("result_detail") or {}).get("proposed_actions") or []
        if not (0 <= int(action_index) < len(proposals)):
            raise HTTPException(status_code=404, detail="动作提案不存在。")
        proposal = proposals[int(action_index)]
        if proposal.get("executed"):
            raise HTTPException(status_code=409, detail="该动作已执行过。")
        action = str(proposal.get("action") or "")
        if action not in AGENT_ACTION_DEFINITIONS:
            raise HTTPException(status_code=400, detail="未知动作。")
        # 教师只能编辑 schema 内字段；以提案参数为底，覆盖教师编辑值。
        merged_params = {**(proposal.get("params") or {}), **edited_params}
        confirmed_params = verify_action_confirmation_token(
            token=str(data.get("confirmation_token") or ""),
            teacher_id=teacher_id,
            task_id=task_id,
            action_index=int(action_index),
            action=action,
            params=merged_params,
        )
        try:
            result = execute_proposed_action(
                conn, teacher_id=teacher_id, action=action, params=confirmed_params
            )
        except HTTPException as exc:
            append_task_event(
                conn,
                task_id,
                "action_failed",
                f"动作「{proposal.get('label') or action}」执行失败：{exc.detail}",
                {"action": action, "action_index": int(action_index)},
                commit=False,
            )
            conn.commit()
            raise
        executed = {
            "at": utcnow_iso(),
            "by_teacher_id": teacher_id,
            "url": result.get("url") or "",
            "label": result.get("label") or "",
            "ref_id": result.get("ref_id"),
        }
        mark_proposed_action_executed(conn, task_id, int(action_index), executed)
        append_task_event(
            conn,
            task_id,
            "action_executed",
            f"教师已确认执行动作「{proposal.get('label') or action}」：{result.get('label') or ''}",
            {
                "action": action,
                "action_index": int(action_index),
                "teacher_id": teacher_id,
                "result_url": result.get("url") or "",
                "params_summary": {
                    key: (str(value)[:80] if isinstance(value, str) else value)
                    for key, value in list(confirmed_params.items())[:6]
                },
            },
            commit=False,
        )
        conn.commit()
        task = get_agent_task(conn, task_id, teacher_id=teacher_id)
    return {"status": "success", "result": result, "task": task}


@router.delete("/{task_id}", response_class=JSONResponse)
def api_delete_agent_task(task_id: int, user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = delete_agent_task(conn, task_id, teacher_id=teacher_id)
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, limit=30)
    return {"status": "success", **result, **queue}


@router.post("/{task_id}/cancel", response_class=JSONResponse)
def api_cancel_agent_task(task_id: int, user: dict = Depends(get_current_teacher)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = cancel_agent_task(conn, task_id, teacher_id=teacher_id)
    return {"status": "success", "task": task}
