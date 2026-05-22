import argparse
import asyncio
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


def _enqueue_runtime_task(client: httpx.Client, task: dict[str, Any], runtime_workspace: str) -> dict[str, Any]:
    from classroom_app.config import (
        AGENT_TASK_ALLOW_RUNTIME_SHELL,
        AGENT_TASK_DEEPSEEK_AUTO_APPROVE,
        AGENT_TASK_RUNTIME_MODEL,
    )
    from classroom_app.services.agent_task_service import build_runtime_prompt

    payload: dict[str, Any] = {
        "prompt": build_runtime_prompt(task, runtime_workspace),
        "workspace": runtime_workspace,
        "mode": "agent",
        "allow_shell": AGENT_TASK_ALLOW_RUNTIME_SHELL,
        "trust_mode": False,
        "auto_approve": AGENT_TASK_DEEPSEEK_AUTO_APPROVE,
    }
    if AGENT_TASK_RUNTIME_MODEL:
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


def _process_task(task: dict[str, Any]) -> None:
    from classroom_app.config import (
        AGENT_TASK_MAX_RUNTIME_SECONDS,
        AGENT_TASK_RUNTIME_POLL_SECONDS,
        AGENT_TASK_RUNTIME_TOKEN,
        AGENT_TASK_RUNTIME_URL,
    )
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import (
        TASK_STATUS_CANCELED,
        TASK_STATUS_COMPLETED,
        TASK_STATUS_FAILED,
        append_task_event,
        compact_runtime_detail,
        finish_agent_task,
        mark_task_runtime_started,
        update_task_runtime_snapshot,
        write_task_workspace,
    )
    from classroom_app.services.agent_key_service import sync_active_agent_runtime_config

    task_id = int(task["id"])
    from classroom_app.services.agent_platform_actions import try_execute_platform_agent_task

    if asyncio.run(try_execute_platform_agent_task(task)):
        return

    if not AGENT_TASK_RUNTIME_URL:
        with get_db_connection() as conn:
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                error_message="Agent 运行时未配置 AGENT_TASK_RUNTIME_URL。",
            )
        return

    runtime_task_id = ""
    try:
        with get_db_connection() as conn:
            sync_active_agent_runtime_config(conn)
        runtime_workspace = write_task_workspace(task)
        timeout = httpx.Timeout(60.0, connect=10.0)
        with httpx.Client(
            base_url=AGENT_TASK_RUNTIME_URL,
            headers=_runtime_headers(AGENT_TASK_RUNTIME_TOKEN),
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            runtime_task = _enqueue_runtime_task(client, task, runtime_workspace)
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
            last_status = str(runtime_task.get("status") or "")
            while True:
                if time.monotonic() - started_at > AGENT_TASK_MAX_RUNTIME_SECONDS:
                    _request_runtime_cancel(client, runtime_task_id)
                    raise TimeoutError(f"Agent task exceeded {AGENT_TASK_MAX_RUNTIME_SECONDS} seconds.")

                with get_db_connection() as conn:
                    if _cancel_requested(conn, task_id):
                        _request_runtime_cancel(client, runtime_task_id)
                        finish_agent_task(
                            conn,
                            task_id,
                            status=TASK_STATUS_CANCELED,
                            result_summary="任务已按教师请求取消。",
                        )
                        return

                response = client.get(f"/v1/tasks/{runtime_task_id}")
                response.raise_for_status()
                runtime_task = response.json()
                if not isinstance(runtime_task, dict):
                    raise RuntimeError("DeepSeek-TUI returned an invalid task status payload.")

                status = str(runtime_task.get("status") or "")
                with get_db_connection() as conn:
                    update_task_runtime_snapshot(conn, task_id, runtime_task)
                    if status and status != last_status:
                        append_task_event(
                            conn,
                            task_id,
                            "runtime_status",
                            f"DeepSeek-TUI 状态更新为 {status}。",
                            {"runtime_status": status},
                        )
                last_status = status

                if _runtime_terminal_status(status):
                    with get_db_connection() as conn:
                        if status == "completed":
                            finish_agent_task(
                                conn,
                                task_id,
                                status=TASK_STATUS_COMPLETED,
                                result_summary=_runtime_result_summary(runtime_task),
                                result_detail=compact_runtime_detail(runtime_task),
                            )
                        elif status == "canceled":
                            finish_agent_task(
                                conn,
                                task_id,
                                status=TASK_STATUS_CANCELED,
                                result_summary=_runtime_result_summary(runtime_task),
                                result_detail=compact_runtime_detail(runtime_task),
                            )
                        else:
                            finish_agent_task(
                                conn,
                                task_id,
                                status=TASK_STATUS_FAILED,
                                result_summary=_runtime_result_summary(runtime_task),
                                result_detail=compact_runtime_detail(runtime_task),
                                error_message=str(runtime_task.get("error") or "DeepSeek-TUI task failed."),
                            )
                    return

                time.sleep(AGENT_TASK_RUNTIME_POLL_SECONDS)
    except Exception as exc:
        with get_db_connection() as conn:
            finish_agent_task(
                conn,
                task_id,
                status=TASK_STATUS_FAILED,
                error_message=str(exc),
                result_detail={"runtime_task_id": runtime_task_id} if runtime_task_id else {},
            )
        print(f"[AGENT_TASK] task {task_id} failed: {exc}", file=sys.stderr)


def _run_once(worker_id: str) -> bool:
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import claim_next_agent_task

    with get_db_connection() as conn:
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
