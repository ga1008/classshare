import argparse
import asyncio
import json
import os
import socket
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv


def _configure_stdio_encoding() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def _runtime_headers(token: str) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _runtime_terminal_status(status: str) -> bool:
    return status in {"completed", "failed", "canceled"}


def _runtime_result_summary(runtime_task: dict[str, Any]) -> str:
    from classroom_app.services.agent_task_service import runtime_result_summary

    return runtime_result_summary(runtime_task)


def _cancel_requested(conn, task_id: int) -> bool:
    row = conn.execute(
        "SELECT cancel_requested_at FROM agent_tasks WHERE id = ? LIMIT 1",
        (int(task_id),),
    ).fetchone()
    return bool(row and row["cancel_requested_at"])


def _request_runtime_cancel(client: httpx.Client, task_id: str) -> None:
    try:
        client.post(f"/v1/tasks/{task_id}/cancel")
    except httpx.HTTPError as exc:
        print(f"[AGENT_TASK] runtime cancel failed for {task_id}: {exc}", file=sys.stderr)


class _RuntimeTaskFailed(Exception):
    """运行时把任务标记为 failed（终态），携带完整运行时快照。"""

    def __init__(self, error_text: str, runtime_task: dict[str, Any]):
        super().__init__(error_text or "DeepSeek-TUI task failed.")
        self.runtime_task = runtime_task


class _RuntimeTaskTimeout(TimeoutError):
    """运行时超过平台上限时，保留最后一次快照用于部分结果挽救。"""

    def __init__(self, error_text: str, runtime_task: dict[str, Any] | None = None):
        super().__init__(error_text)
        self.runtime_task = runtime_task


def _enqueue_runtime_task(
    client: httpx.Client,
    task: dict[str, Any],
    runtime_workspace: str,
    *,
    prompt_suffix: str = "",
) -> dict[str, Any]:
    from classroom_app.config import (
        AGENT_TASK_ALLOW_RUNTIME_SHELL,
        AGENT_TASK_DEEPSEEK_AUTO_APPROVE,
        AGENT_TASK_RUNTIME_MODEL,
    )
    from classroom_app.services.agent_task_service import build_runtime_prompt

    prompt = build_runtime_prompt(task, runtime_workspace)
    if prompt_suffix:
        prompt = f"{prompt}\n\n{prompt_suffix}"
    payload: dict[str, Any] = {
        "prompt": prompt,
        "workspace": runtime_workspace,
        "mode": "agent",
        "allow_shell": AGENT_TASK_ALLOW_RUNTIME_SHELL,
        "trust_mode": False,
        "auto_approve": AGENT_TASK_DEEPSEEK_AUTO_APPROVE,
    }
    deep_thinking = False
    context: dict[str, Any] = {}
    try:
        context = json.loads(str(task.get("context_snapshot_json") or "{}"))
        deep_thinking = bool((context.get("agent_options") or {}).get("deep_thinking"))
    except (TypeError, json.JSONDecodeError):
        deep_thinking = False
    # 追问任务：带上父任务 thread，让运行时尽量续聊（不支持时摘要拼接已在 prompt 内兜底）。
    follow_up = context.get("follow_up") if isinstance(context.get("follow_up"), dict) else {}
    parent_thread_id = str(follow_up.get("parent_thread_id") or "").strip()
    if parent_thread_id:
        payload["thread_id"] = parent_thread_id
    if deep_thinking:
        payload["model"] = "auto"
    elif AGENT_TASK_RUNTIME_MODEL:
        payload["model"] = AGENT_TASK_RUNTIME_MODEL
    response = client.post("/v1/tasks", json=payload)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("DeepSeek-TUI returned an invalid task payload.")
    runtime_id = str(data.get("id") or "").strip()
    if not runtime_id:
        raise RuntimeError("DeepSeek-TUI did not return a task id.")
    return data


def _append_snapshot_diff_events(task_id: int, prev: dict[str, Any] | None, curr: dict[str, Any]) -> None:
    """G1：快照增量 -> 人话化过程事件，前端增量轮询展示。"""
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_progress_service import diff_runtime_snapshot
    from classroom_app.services.agent_task_service import append_task_event

    try:
        events = diff_runtime_snapshot(prev, curr)
    except Exception as exc:
        print(f"[AGENT_TASK] snapshot diff failed for task {task_id}: {exc}", file=sys.stderr)
        return
    if not events:
        return
    with get_db_connection() as conn:
        for event in events:
            append_task_event(
                conn,
                task_id,
                event["event_type"],
                event["message"],
                event.get("detail") or {},
                commit=False,
            )
        conn.commit()


def _finish_completed_runtime_task(task_id: int, runtime_task: dict[str, Any]) -> None:
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_action_registry import extract_proposed_actions
    from classroom_app.services.agent_task_service import (
        TASK_STATUS_COMPLETED,
        compact_runtime_detail,
        finish_agent_task,
    )

    detail = compact_runtime_detail(runtime_task)
    try:
        # G3：从最终输出抽取结构化动作提案，渲染为确认按钮。
        sources = [item.get("text") or "" for item in (detail.get("text_outputs") or [])]
        sources.append(str(runtime_task.get("result") or ""))
        proposed = extract_proposed_actions("\n\n".join(sources))
        if proposed:
            detail["proposed_actions"] = proposed
    except Exception as exc:
        print(f"[AGENT_TASK] proposed action parse failed for task {task_id}: {exc}", file=sys.stderr)
    with get_db_connection() as conn:
        finish_agent_task(
            conn,
            task_id,
            status=TASK_STATUS_COMPLETED,
            result_summary=_runtime_result_summary(runtime_task),
            result_detail=detail,
        )


def _run_runtime_attempt(
    client: httpx.Client,
    task: dict[str, Any],
    runtime_workspace: str,
    *,
    prompt_suffix: str = "",
) -> bool:
    """单次运行时执行。返回 True 表示任务已终态落库（含取消）。

    运行时报告 failed 时抛 _RuntimeTaskFailed，由调用方分类决定是否自动重试。
    """
    from classroom_app.config import (
        AGENT_TASK_MAX_RUNTIME_SECONDS,
        AGENT_TASK_RUNTIME_POLL_SECONDS,
    )
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import (
        TASK_STATUS_CANCELED,
        finish_agent_task,
        mark_task_runtime_started,
        update_task_runtime_snapshot,
    )

    task_id = int(task["id"])
    runtime_task = _enqueue_runtime_task(client, task, runtime_workspace, prompt_suffix=prompt_suffix)
    runtime_task_id = str(runtime_task["id"])
    with get_db_connection() as conn:
        mark_task_runtime_started(
            conn,
            task_id,
            runtime_task_id=runtime_task_id,
            runtime_thread_id=str(runtime_task.get("thread_id") or ""),
            runtime_turn_id=str(runtime_task.get("turn_id") or ""),
        )

    started_at = time.monotonic()
    prev_snapshot: dict[str, Any] | None = None
    while True:
        if time.monotonic() - started_at > AGENT_TASK_MAX_RUNTIME_SECONDS:
            _request_runtime_cancel(client, runtime_task_id)
            raise _RuntimeTaskTimeout(
                f"Agent task exceeded {AGENT_TASK_MAX_RUNTIME_SECONDS} seconds.",
                runtime_task=runtime_task,
            )

        with get_db_connection() as conn:
            if _cancel_requested(conn, task_id):
                _request_runtime_cancel(client, runtime_task_id)
                finish_agent_task(
                    conn,
                    task_id,
                    status=TASK_STATUS_CANCELED,
                    result_summary="任务已按教师请求取消。",
                )
                return True

        response = client.get(f"/v1/tasks/{runtime_task_id}")
        response.raise_for_status()
        runtime_task = response.json()
        if not isinstance(runtime_task, dict):
            raise RuntimeError("DeepSeek-TUI returned an invalid task status payload.")

        status = str(runtime_task.get("status") or "")
        with get_db_connection() as conn:
            update_task_runtime_snapshot(conn, task_id, runtime_task)
        _append_snapshot_diff_events(task_id, prev_snapshot, runtime_task)
        prev_snapshot = runtime_task

        if _runtime_terminal_status(status):
            if status == "completed":
                _finish_completed_runtime_task(task_id, runtime_task)
                return True
            if status == "canceled":
                from classroom_app.services.agent_task_service import compact_runtime_detail

                with get_db_connection() as conn:
                    finish_agent_task(
                        conn,
                        task_id,
                        status=TASK_STATUS_CANCELED,
                        result_summary=_runtime_result_summary(runtime_task),
                        result_detail=compact_runtime_detail(runtime_task),
                    )
                return True
            raise _RuntimeTaskFailed(str(runtime_task.get("error") or ""), runtime_task)

        time.sleep(AGENT_TASK_RUNTIME_POLL_SECONDS)


def _bump_retry_count(task_id: int) -> None:
    from classroom_app.database import get_db_connection

    with get_db_connection() as conn:
        try:
            conn.execute(
                "UPDATE agent_tasks SET retry_count = COALESCE(retry_count, 0) + 1 WHERE id = ?",
                (int(task_id),),
            )
            conn.commit()
        except Exception:
            conn.rollback()


def _finish_failed(task_id: int, *, error_message: str, error_class: str, runtime_task: dict[str, Any] | None) -> None:
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import (
        TASK_STATUS_FAILED,
        build_failed_runtime_detail,
        finish_agent_task,
    )

    detail, summary = build_failed_runtime_detail(
        task_id,
        runtime_task=runtime_task,
        error_class=error_class,
        error_message=error_message,
    )
    with get_db_connection() as conn:
        finish_agent_task(
            conn,
            task_id,
            status=TASK_STATUS_FAILED,
            result_summary=summary,
            result_detail=detail,
            error_message=error_message,
        )


def _process_task(task: dict[str, Any]) -> None:
    from classroom_app.config import (
        AGENT_TASK_AUTO_RETRY_HOURLY_LIMIT,
        AGENT_TASK_AUTO_RETRY_LIMIT,
        AGENT_TASK_RUNTIME_TOKEN,
        AGENT_TASK_RUNTIME_URL,
    )
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_key_service import sync_active_agent_runtime_config
    from classroom_app.services.agent_platform_actions import try_execute_platform_agent_task
    from classroom_app.services.agent_task_progress_service import (
        ERROR_CLASS_CONTENT,
        ERROR_CLASS_TRANSIENT,
        classify_runtime_error,
    )
    from classroom_app.services.agent_task_service import (
        TASK_STATUS_FAILED,
        append_task_event,
        finish_agent_task,
        write_task_workspace,
    )

    task_id = int(task["id"])
    runtime_available = bool(AGENT_TASK_RUNTIME_URL)
    if asyncio.run(try_execute_platform_agent_task(task, runtime_available=runtime_available)):
        return

    if not runtime_available:
        with get_db_connection() as conn:
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                error_message="Agent 运行时未配置 AGENT_TASK_RUNTIME_URL。",
            )
        return

    try:
        with get_db_connection() as conn:
            sync_active_agent_runtime_config(conn)
        runtime_workspace = write_task_workspace(task)
    except Exception as exc:
        _finish_failed(task_id, error_message=str(exc), error_class="fatal", runtime_task=None)
        print(f"[AGENT_TASK] task {task_id} workspace setup failed: {exc}", file=sys.stderr)
        return

    timeout = httpx.Timeout(60.0, connect=10.0)
    prompt_suffix = ""
    max_attempts = 1 + max(0, int(AGENT_TASK_AUTO_RETRY_LIMIT))
    with httpx.Client(
        base_url=AGENT_TASK_RUNTIME_URL,
        headers=_runtime_headers(AGENT_TASK_RUNTIME_TOKEN),
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                if _run_runtime_attempt(client, task, runtime_workspace, prompt_suffix=prompt_suffix):
                    return
            except _RuntimeTaskTimeout as exc:
                _finish_failed(task_id, error_message=str(exc), error_class="timeout", runtime_task=exc.runtime_task)
                print(f"[AGENT_TASK] task {task_id} timed out: {exc}", file=sys.stderr)
                return
            except _RuntimeTaskFailed as exc:
                error_text = str(exc)
                error_class = classify_runtime_error(error_text)
                if attempt < max_attempts and error_class in (ERROR_CLASS_TRANSIENT, ERROR_CLASS_CONTENT):
                    if error_class == ERROR_CLASS_CONTENT:
                        prompt_suffix = (
                            f"上次执行的输出存在问题（{error_text[:200]}），"
                            "请确保本次输出完整、格式正确，JSON 块必须闭合。"
                        )
                    retry_record = _record_auto_retry(task_id, error_text, error_class)
                    if retry_record.get("allowed"):
                        time.sleep(30)
                        continue
                    _finish_failed(
                        task_id,
                        error_message=_retry_budget_error_message(error_text, AGENT_TASK_AUTO_RETRY_HOURLY_LIMIT),
                        error_class=error_class,
                        runtime_task=exc.runtime_task,
                    )
                    return
                _finish_failed(
                    task_id,
                    error_message=error_text or "DeepSeek-TUI task failed.",
                    error_class=error_class,
                    runtime_task=exc.runtime_task,
                )
                return
            except Exception as exc:
                error_text = str(exc)
                error_class = classify_runtime_error(error_text)
                if attempt < max_attempts and error_class == ERROR_CLASS_TRANSIENT:
                    retry_record = _record_auto_retry(task_id, error_text, error_class)
                    if retry_record.get("allowed"):
                        time.sleep(30)
                        continue
                    _finish_failed(
                        task_id,
                        error_message=_retry_budget_error_message(error_text, AGENT_TASK_AUTO_RETRY_HOURLY_LIMIT),
                        error_class=error_class,
                        runtime_task=None,
                    )
                    return
                _finish_failed(task_id, error_message=error_text, error_class=error_class, runtime_task=None)
                print(f"[AGENT_TASK] task {task_id} failed: {exc}", file=sys.stderr)
                return


def _record_auto_retry_unbounded_legacy(task_id: int, error_text: str, error_class: str) -> None:
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import append_task_event

    _bump_retry_count(task_id)
    with get_db_connection() as conn:
        append_task_event(
            conn,
            task_id,
            "auto_retry",
            "遇到临时故障，30 秒后自动重试，无需教师操作。",
            {"error": error_text[:400], "error_class": error_class},
        )
    print(f"[AGENT_TASK] task {task_id} auto retry ({error_class}): {error_text[:200]}", file=sys.stderr)


def _retry_budget_error_message(error_text: str, hourly_limit: int) -> str:
    clean_error = str(error_text or "runtime error").strip()[:240]
    return (
        f"自动重试次数已达上限（每小时 {int(hourly_limit)} 次）。"
        f"系统已暂停自动重试，教师可稍后点击任务卡片上的重试按钮。最后一次错误：{clean_error}"
    )


def _record_auto_retry(task_id: int, error_text: str, error_class: str) -> dict[str, Any]:
    from classroom_app.config import AGENT_TASK_AUTO_RETRY_HOURLY_LIMIT
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import record_agent_auto_retry

    with get_db_connection() as conn:
        result = record_agent_auto_retry(
            conn,
            task_id,
            error_text=error_text,
            error_class=error_class,
            hourly_limit=AGENT_TASK_AUTO_RETRY_HOURLY_LIMIT,
        )
    if result.get("allowed"):
        print(f"[AGENT_TASK] task {task_id} auto retry ({error_class}): {error_text[:200]}", file=sys.stderr)
    else:
        print(
            f"[AGENT_TASK] task {task_id} auto retry budget exhausted ({error_class}): {error_text[:200]}",
            file=sys.stderr,
        )
    return result


def _run_once(worker_id: str) -> bool:
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import claim_next_agent_task, maybe_cleanup_stale_agent_task_attachments

    with get_db_connection() as conn:
        cleanup_result = maybe_cleanup_stale_agent_task_attachments(conn)
        if cleanup_result.get("cleaned_count"):
            print(
                f"[AGENT_TASK] cleaned stale attachment workspaces: {cleanup_result['cleaned_count']}"
            )
        task = claim_next_agent_task(conn, worker_id=worker_id)
    if not task:
        return False
    print(f"[AGENT_TASK] claimed task {task['id']} ({task.get('title') or task.get('task_type')})")
    _process_task(task)
    return True


def main() -> None:
    load_dotenv()
    _configure_stdio_encoding()

    parser = argparse.ArgumentParser(description="LanShare teacher agent task worker")
    parser.add_argument("--once", action="store_true", help="process at most one queued task and exit")
    parser.add_argument("--worker-id", default="", help="stable worker id shown in task events")
    args = parser.parse_args()

    from classroom_app.config import AGENT_TASK_WORKER_ID, AGENT_TASK_WORKER_POLL_SECONDS, ensure_runtime_directories
    from classroom_app.database import init_database

    ensure_runtime_directories()
    init_database()

    worker_id = args.worker_id or AGENT_TASK_WORKER_ID or os.getenv("AGENT_TASK_WORKER_ID") or f"agent-{socket.gethostname()}"
    print(f"[AGENT_TASK] worker started as {worker_id}")
    try:
        while True:
            processed = _run_once(worker_id)
            if args.once:
                return
            if not processed:
                time.sleep(AGENT_TASK_WORKER_POLL_SECONDS)
    except KeyboardInterrupt:
        print("[AGENT_TASK] worker stopped")


if __name__ == "__main__":
    main()
