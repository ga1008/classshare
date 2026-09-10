from __future__ import annotations

import asyncio
import io
import json
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..config import AGENT_DSH_ENABLED, AGENT_TASKS_ENABLED
from ..database import get_db_connection
from ..dependencies import get_current_user
from ..services.agent_actor_service import resolve_agent_actor
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
    resolve_task_workspace_artifact,
    set_agent_task_composer,
    task_type_options,
    utcnow_iso,
)

router = APIRouter(prefix="/api/agent-tasks", tags=["agent-tasks"])


def _current_agent_user(user: dict = Depends(get_current_user)) -> dict:
    with get_db_connection() as conn:
        actor = resolve_agent_actor(conn, user.get("role"), user.get("id"))
    return {**user, **actor.as_user()}


def _require_teacher_action(user: dict) -> None:
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="此教学管理功能仅对教师开放。")


def _source_session_id(user: dict) -> str:
    session_id = str(user.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=401, detail="Agent 需要有效的登录会话，请重新登录。")
    return session_id


def _teacher_id(user: dict[str, Any]) -> int:
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="登录状态无效，请重新登录。") from exc


@router.get("/bootstrap", response_class=JSONResponse)
def bootstrap_agent_task_center(user: dict = Depends(_current_agent_user)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, viewer_role=user["role"], limit=30)
    return {
        "status": "success",
        "enabled": bool(AGENT_TASKS_ENABLED),
        "runtime_configured": bool(AGENT_DSH_ENABLED),
        "task_types": task_type_options(),
        "workflow_catalog": agent_workflow_catalog() if user["role"] == "teacher" else [],
        **queue,
    }


@router.get("", response_class=JSONResponse)
def api_list_agent_tasks(
    limit: int = Query(default=30, ge=1, le=80),
    user: dict = Depends(_current_agent_user),
):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, viewer_role=user["role"], limit=limit)
    return {"status": "success", **queue}


_AGENT_ATTACHMENT_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".htm", ".css", ".sql", ".log",
}
_AGENT_ATTACHMENT_DOC_EXTENSIONS = {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".xlsx", ".xls"}
_AGENT_ATTACHMENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_AGENT_ATTACHMENT_CAPACITY = threading.BoundedSemaphore(2)


def _describe_agent_image_attachment(filename: str, contents: bytes) -> str:
    """Validate an image upload and return a deterministic text fallback for the runtime prompt."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(contents)) as image:
            width, height = image.size
            image_format = (image.format or Path(filename).suffix.lstrip(".") or "image").upper()
            image.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"图片附件 {filename} 无法读取，请确认文件未损坏。") from exc
    return (
        f"图片附件：{filename}。平台已验证图片可读取，格式 {image_format}，尺寸 {width}x{height} 像素。"
        "原图位于任务 workspace 的 attachments/ 子目录；如果运行时支持视觉，请直接读取原图。"
        "如果运行时不支持视觉，请明确告知教师需要补充截图中的文字或关键信息。"
    )


async def _process_agent_attachment(file) -> dict[str, Any]:
    """Agent 附件处理：保留原始字节 + 尽力抽取文本（供 runtime 直接读取）。"""
    from starlette.concurrency import run_in_threadpool

    if not _AGENT_ATTACHMENT_CAPACITY.acquire(blocking=False):
        raise HTTPException(429, "附件处理繁忙，请稍后重新提交，附件内容会保留在当前表单。")
    work = None
    try:
        contents = await file.read(AGENT_TASK_ATTACHMENT_MAX_FILE_BYTES + 1)
        filename = str(getattr(file, "filename", "") or "attachment")
        work = asyncio.create_task(run_in_threadpool(_process_agent_attachment_bytes, filename, contents))
        return await asyncio.shield(work)
    finally:
        # Cancellation cannot terminate a native document parser. Keep its
        # bounded slot until the real worker ends, while allowing the app's
        # event loop to continue serving unrelated platform users.
        if work is not None:
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not work.cancelled():
                work.exception()
        _AGENT_ATTACHMENT_CAPACITY.release()


def _process_agent_attachment_bytes(filename: str, contents: bytes) -> dict[str, Any]:
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
        text = _describe_agent_image_attachment(filename, contents)
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
    user: dict = Depends(_current_agent_user),
):
    if not AGENT_TASKS_ENABLED:
        raise HTTPException(status_code=503, detail="任务中心暂未启用。")
    data, attachment_items = await _parse_create_request(request)
    # 来源/优先级等内部字段不接受客户端指定。
    for reserved in ("origin", "parent_task_id", "priority", "title_override", "extra_context", "attachments", "actor_role", "actor_id", "source_session_id", "source_session_hash", "source_session_key", "runtime_provider"):
        data.pop(reserved, None)
    with get_db_connection() as conn:
        task = create_agent_task(conn, user, data, source_session_id=_source_session_id(user))
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
            task = get_agent_task(conn, int(task["id"]), teacher_id=_teacher_id(user), actor_role=user["role"])
    background_tasks.add_task(generate_agent_task_title, int(task["id"]))
    return {"status": "success", "task": task}


@router.post("/composer", response_class=JSONResponse)
async def api_set_agent_task_composer(request: Request, user: dict = Depends(_current_agent_user)):
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
def api_list_agent_subscriptions(user: dict = Depends(_current_agent_user)):
    from ..services.agent_subscription_service import list_agent_subscriptions

    if user["role"] != "teacher":
        return {"status": "success", "supported": False, "templates": [], "subscriptions": [], "recent_tasks": []}
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = list_agent_subscriptions(conn, teacher_id=teacher_id)
    return {"status": "success", **result}


@router.post("/subscriptions", response_class=JSONResponse)
async def api_set_agent_subscription(request: Request, user: dict = Depends(_current_agent_user)):
    _require_teacher_action(user)
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
def api_delete_agent_task_history(user: dict = Depends(_current_agent_user)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = delete_agent_task_history(conn, teacher_id=teacher_id, actor_role=user["role"])
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, viewer_role=user["role"], limit=30)
    return {"status": "success", **result, **queue}


@router.get("/{task_id}", response_class=JSONResponse)
def api_get_agent_task(task_id: int, user: dict = Depends(_current_agent_user)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
        if task["is_owner"]:
            from ..services.agent_question_service import list_user_questions
            task["questions"] = list_user_questions(conn, user, task_id)
    return {"status": "success", "task": task}


@router.post("/{task_id}/questions/{question_id}/answer", response_class=JSONResponse)
async def api_answer_agent_question(task_id: int, question_id: str, request: Request, user: dict = Depends(_current_agent_user)):
    from ..services.agent_question_service import answer_question
    from starlette.concurrency import run_in_threadpool
    _source_session_id(user)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 20000:
            raise HTTPException(413, "回答内容过长。")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "回答格式无效。") from None
    if not isinstance(payload, dict) or set(payload) != {"answers"}:
        raise HTTPException(400, "回答格式无效。")

    def save():
        with get_db_connection() as conn:
            result = answer_question(conn, user, task_id, question_id, payload["answers"])
            conn.commit()
            return result

    return {"status": "success", "question": await run_in_threadpool(save)}


@router.get("/{task_id}/artifacts/{artifact_path:path}", response_class=FileResponse)
def api_download_agent_task_artifact(
    task_id: int,
    artifact_path: str,
    user: dict = Depends(_current_agent_user),
):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
    if not task.get("is_owner"):
        raise HTTPException(status_code=403, detail="只能下载自己 Agent 任务的中间产物。")
    try:
        payload = resolve_task_workspace_artifact(task_id, artifact_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        payload["path"],
        filename=payload["filename"],
        media_type=payload["media_type"],
    )


@router.get("/{task_id}/events", response_class=JSONResponse)
def api_list_agent_task_events(
    task_id: int,
    after: int = Query(default=0, ge=0),
    user: dict = Depends(_current_agent_user),
):
    """G1 增量过程事件（2 秒级短轮询通道，仅任务所有者）。"""
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = list_task_events_after(conn, task_id, teacher_id=teacher_id, actor_role=user["role"], after_event_id=after)
    return {"status": "success", **result}


@router.get("/{task_id}/stream")
async def api_stream_agent_task_events(
    task_id: int,
    request: Request,
    after: int = Query(default=0, ge=0),
    user: dict = Depends(_current_agent_user),
):
    """G1 SSE process stream; clients fall back to /events short polling."""
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        # Validate task ownership before returning a streaming response so
        # unauthorized requests still get a normal JSON/HTTP error.
        list_task_events_after(conn, task_id, teacher_id=teacher_id, actor_role=user["role"], after_event_id=after)

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
                        actor_role=user["role"],
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
    user: dict = Depends(_current_agent_user),
):
    if not AGENT_TASKS_ENABLED:
        raise HTTPException(status_code=503, detail="任务中心暂未启用。")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求格式错误。")
    with get_db_connection() as conn:
        current = get_agent_task(conn, task_id, teacher_id=_teacher_id(user), actor_role=user["role"])
        if current.get("is_active"):
            task = add_task_supplement(conn, user, task_id, str(data.get("instruction") or ""))
            supplemented = True
        else:
            task = create_follow_up_task(conn, user, task_id, str(data.get("instruction") or ""), source_session_id=_source_session_id(user))
            supplemented = False
        conn.commit()
    return {"status": "success", "task": task, "supplemented": supplemented}


@router.post("/{task_id}/retry", response_class=JSONResponse)
async def api_retry_agent_task(
    task_id: int,
    request: Request,
    user: dict = Depends(_current_agent_user),
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
            source_session_id=_source_session_id(user),
        )
        conn.commit()
    return {"status": "success", "task": task}


@router.post("/{task_id}/actions/{action_index}/preview", response_class=JSONResponse)
async def api_preview_agent_task_action(
    task_id: int,
    action_index: int,
    request: Request,
    user: dict = Depends(_current_agent_user),
):
    """Preview public proposal parameters and issue a short-lived confirmation."""
    from ..services.agent_action_registry import (
        AGENT_ACTION_DEFINITIONS,
        ensure_action_actor_role,
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
        task = get_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
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
        ensure_action_actor_role(action, user["role"])
        if definition.get("requires_super_admin"):
            from ..services.agent_actor_service import resolve_agent_actor

            actor = resolve_agent_actor(conn, user["role"], teacher_id)
            if not actor.is_super_admin:
                raise HTTPException(403, "当前账号没有该管理权限。")
        merged_params = {**(proposal.get("params") or {}), **edited_params}
        confirmation = issue_action_confirmation_token(
            teacher_id=teacher_id,
            actor_role=user["role"],
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
        "secure_fields": definition.get("secure_fields") or [],
        **confirmation,
    }


@router.post("/{task_id}/actions/{action_index}/execute", response_class=JSONResponse)
async def api_execute_agent_task_action(
    task_id: int,
    action_index: int,
    request: Request,
    user: dict = Depends(_current_agent_user),
):
    """Fresh session confirmation and business mutation share one receipt transaction."""
    from starlette.concurrency import run_in_threadpool

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Domain locks, password hashing and synchronous SQL must not block the
    # event loop serving streaming tasks and other users' platform requests.
    return await run_in_threadpool(_execute_agent_task_action, task_id=task_id,
                                  action_index=action_index, data=data, user=user)


def _execute_agent_task_action(*, task_id, action_index, data, user):
    from ..services.agent_action_registry import (
        AGENT_ACTION_DEFINITIONS,
        ensure_action_actor_role,
        verify_action_confirmation_token,
    )
    from ..services.agent_platform_write_service import dispatch_user_write
    from ..services.agent_secure_account_actions import SECURE_ACTION_DEFINITIONS, dispatch_user_secure_action

    teacher_id = _teacher_id(user)
    edited_params = data.get("params") if isinstance(data.get("params"), dict) else {}

    with get_db_connection() as conn:
        task = get_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
        if not task.get("is_owner"):
            raise HTTPException(status_code=403, detail="只能执行自己任务的动作提案。")
        proposals = (task.get("result_detail") or {}).get("proposed_actions") or []
        if not (0 <= int(action_index) < len(proposals)):
            raise HTTPException(status_code=404, detail="动作提案不存在。")
        proposal = proposals[int(action_index)]
        action = str(proposal.get("action") or "")
        if action not in AGENT_ACTION_DEFINITIONS:
            raise HTTPException(status_code=400, detail="未知动作。")
        ensure_action_actor_role(action, user["role"])
        # 用户只能编辑 schema 内字段；以提案参数为底，覆盖用户编辑值。
        merged_params = {**(proposal.get("params") or {}), **edited_params}
        confirmed_params = verify_action_confirmation_token(
            token=str(data.get("confirmation_token") or ""),
            teacher_id=teacher_id,
            actor_role=user["role"],
            task_id=task_id,
            action_index=int(action_index),
            action=action,
            params=merged_params,
        )
        operation_id = f"proposal:{int(task_id)}:{int(action_index)}"
        session_id = _source_session_id(user)
        try:
            if action in SECURE_ACTION_DEFINITIONS:
                outcome = dispatch_user_secure_action(
                    conn, user=user, source_session_id=session_id, task_id=task_id,
                    operation_id=operation_id, action=action, params=confirmed_params,
                    secure_inputs=data.get("secure_inputs"),
                )
            else:
                if data.get("secure_inputs"):
                    raise HTTPException(400, "该动作不接收安全输入。")
                outcome = dispatch_user_write(
                    conn, user=user, source_session_id=session_id, task_id=task_id,
                    operation_id=operation_id, action=action, params=confirmed_params,
                )
            result = outcome["result"]
            if outcome["replayed"]:
                conn.commit()
                return {"status": "success", "result": result, "task": get_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"]), "replayed": True}
        except HTTPException as exc:
            conn.rollback()
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
        except Exception:
            conn.rollback()
            raise
        executed = {
            "at": utcnow_iso(),
            "by_teacher_id": teacher_id,
            "by_actor_role": user["role"],
            "by_actor_id": teacher_id,
            "operation_id": operation_id,
            "url": result.get("url") or "",
            "label": result.get("label") or "",
            "ref_id": result.get("ref_id"),
        }
        mark_proposed_action_executed(conn, task_id, int(action_index), executed)
        append_task_event(
            conn,
            task_id,
            "action_executed",
            f"用户已确认执行动作「{proposal.get('label') or action}」：{result.get('label') or ''}",
            {
                "action": action,
                "action_index": int(action_index),
                "teacher_id": teacher_id,
                "actor_role": user["role"],
                "actor_id": teacher_id,
                "result_url": result.get("url") or "",
                "params_summary": {
                    key: (str(value)[:80] if isinstance(value, str) else value)
                    for key, value in list(confirmed_params.items())[:6]
                },
            },
            commit=False,
        )
        conn.commit()
        task = get_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
    return {"status": "success", "result": result, "task": task}


@router.delete("/{task_id}", response_class=JSONResponse)
def api_delete_agent_task(task_id: int, user: dict = Depends(_current_agent_user)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        result = delete_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
        queue = list_agent_tasks(conn, viewer_teacher_id=teacher_id, viewer_role=user["role"], limit=30)
    return {"status": "success", **result, **queue}


@router.post("/{task_id}/cancel", response_class=JSONResponse)
def api_cancel_agent_task(task_id: int, user: dict = Depends(_current_agent_user)):
    teacher_id = _teacher_id(user)
    with get_db_connection() as conn:
        task = cancel_agent_task(conn, task_id, teacher_id=teacher_id, actor_role=user["role"])
    return {"status": "success", "task": task}


@router.get("/{task_id}/platform-requests", response_class=JSONResponse)
def api_list_agent_platform_requests(task_id: int, limit: int = Query(default=20, ge=1, le=50),
                                     offset: int = Query(default=0, ge=0, le=10000),
                                     user: dict = Depends(_current_agent_user)):
    from ..services.agent_platform_request_reconciliation import list_user_platform_requests
    with get_db_connection() as conn:
        result = list_user_platform_requests(conn, user=user, source_session_id=_source_session_id(user), task_id=task_id, limit=limit, offset=offset)
    return {"status": "success", **result}


@router.get("/{task_id}/platform-requests/{request_id}", response_class=JSONResponse)
def api_get_agent_platform_request(task_id: int, request_id: str, user: dict = Depends(_current_agent_user)):
    from ..services.agent_platform_request_reconciliation import get_user_platform_request
    with get_db_connection() as conn:
        result = get_user_platform_request(conn, user=user, source_session_id=_source_session_id(user), task_id=task_id, request_id=request_id)
    return {"status": "success", "request": result}


@router.post("/{task_id}/platform-requests/{request_id}/reconcile", response_class=JSONResponse)
async def api_reconcile_agent_platform_request(task_id: int, request_id: str, request: Request,
                                               user: dict = Depends(_current_agent_user)):
    from starlette.concurrency import run_in_threadpool
    from ..services.agent_platform_request_reconciliation import reconcile_user_platform_request
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 16384:
            raise HTTPException(413, "人工核对请求内容过长。")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "人工核对请求必须是有效JSON。") from None
    if not isinstance(payload, dict) or set(payload) != {"resolution", "note", "expected_revision"}:
        raise HTTPException(400, "请提供完整的核对声明、依据和请求版本。")
    def save_declaration():
        with get_db_connection() as conn:
            try:
                result = reconcile_user_platform_request(conn, user=user, source_session_id=_source_session_id(user),
                    task_id=task_id, request_id=request_id, **payload)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
    result = await run_in_threadpool(save_declaration)
    return {"status": "success", **result}
