import argparse
import asyncio
import os
import socket
import sys
import threading
import time
from typing import Any

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


PARKED_EXPIRY_INTERVAL_SECONDS = 300
_last_parked_expiry = 0.0


def _process_task(task: dict[str, Any]) -> None:
    from classroom_app.services.agent_sdk.runner import run_agent_task

    try:
        # One execution segment on the OpenAI Agents SDK (DeepSeek V4.1 Flash).
        # Platform operations go through the app bridge as the task's user.
        asyncio.run(run_agent_task(task))
    except Exception as exc:
        # A database or transport error does not prove that a runner stopped or
        # that a business transaction did not commit. The lease reconciler owns
        # that decision; never overwrite a newer attempt from this catch block.
        print(f"[AGENT_TASK] task {task['id']} requires reconciliation ({type(exc).__name__})", file=sys.stderr)


def _worker_ids(base_worker_id: str, concurrency: int) -> list[str]:
    worker_id = str(base_worker_id or "").strip() or f"agent-{socket.gethostname()}"
    count = max(1, min(int(concurrency or 1), 4))
    if count == 1:
        return [worker_id]
    return [f"{worker_id}-{index}" for index in range(1, count + 1)]


def _housekeeping() -> None:
    """Loop 1 only: fail tasks whose worker died, expire long-parked tasks."""
    global _last_parked_expiry
    from classroom_app.services.agent_sdk.state import expire_parked_tasks, recover_stale_tasks

    try:
        recovered = recover_stale_tasks()
        if recovered:
            print(f"[AGENT_TASK] recovered {recovered} interrupted task(s)")
        if time.monotonic() - _last_parked_expiry >= PARKED_EXPIRY_INTERVAL_SECONDS:
            _last_parked_expiry = time.monotonic()
            expired = expire_parked_tasks()
            if expired:
                print(f"[AGENT_TASK] closed {expired} long-parked task(s)")
    except Exception as exc:
        print(f"[AGENT_TASK] housekeeping failed ({type(exc).__name__}: {exc})", file=sys.stderr)


def _run_once(worker_id: str, *, cleanup: bool = True) -> bool:
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_task_service import claim_next_agent_task, maybe_cleanup_stale_agent_task_attachments

    if cleanup:
        _housekeeping()

    with get_db_connection() as conn:
        if cleanup:
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


def _worker_loop(
    worker_id: str,
    *,
    once: bool,
    cleanup: bool,
    poll_seconds: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        processed = _run_once(worker_id, cleanup=cleanup)
        if once:
            return
        if not processed and stop_event.wait(max(1, int(poll_seconds or 1))):
            return


def main() -> None:
    load_dotenv()
    _configure_stdio_encoding()

    parser = argparse.ArgumentParser(description="LanShare Agent task worker (OpenAI Agents SDK runtime)")
    parser.add_argument("--once", action="store_true", help="process at most one queued task and exit")
    parser.add_argument("--worker-id", default="", help="stable worker id shown in task events")
    parser.add_argument("--concurrency", type=int, default=0, help="parallel worker loops; defaults to config")
    args = parser.parse_args()

    from classroom_app.config import (
        AGENT_TASK_WORKER_CONCURRENCY,
        AGENT_TASK_WORKER_ID,
        AGENT_TASK_WORKER_POLL_SECONDS,
        ensure_runtime_directories,
    )
    from classroom_app.database import init_database

    ensure_runtime_directories()
    init_database()
    from classroom_app.database import get_db_connection
    from classroom_app.services.agent_runtime_schema import ensure_agent_runtime_schema

    with get_db_connection() as conn:
        ensure_agent_runtime_schema(conn)
        conn.commit()

    worker_id = args.worker_id or AGENT_TASK_WORKER_ID or os.getenv("AGENT_TASK_WORKER_ID") or f"agent-{socket.gethostname()}"
    configured_concurrency = args.concurrency if args.concurrency > 0 else AGENT_TASK_WORKER_CONCURRENCY
    concurrency = 1 if args.once else configured_concurrency
    ids = _worker_ids(worker_id, concurrency)
    print(f"[AGENT_TASK] worker started as {worker_id} (concurrency={len(ids)})")
    stop_event = threading.Event()
    try:
        if len(ids) == 1:
            _worker_loop(
                ids[0],
                once=args.once,
                cleanup=True,
                poll_seconds=AGENT_TASK_WORKER_POLL_SECONDS,
                stop_event=stop_event,
            )
            return

        threads = []
        for index, loop_worker_id in enumerate(ids, start=1):
            threads.append(
                threading.Thread(
                    target=_worker_loop,
                    name=f"agent-task-worker-{index}",
                    kwargs={
                        "worker_id": loop_worker_id,
                        "once": False,
                        "cleanup": index == 1,
                        "poll_seconds": AGENT_TASK_WORKER_POLL_SECONDS,
                        "stop_event": stop_event,
                    },
                )
            )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        stop_event.set()
        print("[AGENT_TASK] worker stopped")


if __name__ == "__main__":
    main()
