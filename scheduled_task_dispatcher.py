import argparse
import asyncio
import os
import socket
import sys

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


async def _run(args) -> None:
    from classroom_app.config import ensure_runtime_directories
    from classroom_app.database import init_database
    from classroom_app.services import scheduled_task_handlers  # noqa: F401 - registers handlers
    from classroom_app.services.scheduled_task_service import (
        process_due_scheduled_tasks_once,
        run_scheduler_worker_forever,
        update_scheduler_heartbeat,
    )

    ensure_runtime_directories()
    init_database()

    worker_id = args.worker_id or os.getenv("SCHEDULER_WORKER_ID") or f"scheduler-{socket.gethostname()}"
    if args.once:
        result = await process_due_scheduled_tasks_once()
        update_scheduler_heartbeat(worker_id, status="once")
        print(f"[SCHEDULER] processed one batch: {result}")
        return

    await run_scheduler_worker_forever(worker_id=worker_id, poll_seconds=args.poll_seconds)


def main() -> None:
    load_dotenv()
    _configure_stdio_encoding()
    parser = argparse.ArgumentParser(description="LanShare unified scheduled-task dispatcher")
    parser.add_argument("--once", action="store_true", help="process one due batch then exit")
    parser.add_argument("--worker-id", default="", help="stable worker id shown in management views")
    parser.add_argument("--poll-seconds", type=int, default=None, help="poll interval for the long-running worker")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("[SCHEDULER] worker stopped")


if __name__ == "__main__":
    main()
