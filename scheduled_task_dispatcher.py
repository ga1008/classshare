import argparse
import asyncio
from contextlib import contextmanager
import os
import signal
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


@contextmanager
def _scheduler_signals(stop):
    """Own process signals until Runner and pool cleanup have both finished."""
    previous = []

    def request_stop(signum, _frame):
        if stop["requested"]:
            return  # A second signal must not interrupt the real drain.
        stop["requested"] = True
        print(f"[SCHEDULER] {signal.Signals(signum).name} requested; waiting for active work")
        loop, task = stop.get("loop"), stop.get("task")
        if not stop["draining"] and loop is not None and task is not None:
            stop["cancel_sent"] = True
            loop.call_soon_threadsafe(task.cancel)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old = signal.getsignal(signum)
            signal.signal(signum, request_stop)
            previous.append((signum, old))
        yield
    finally:
        for signum, old in reversed(previous):
            signal.signal(signum, old)


async def _run(args, *, stop=None) -> None:
    from classroom_app.config import ensure_runtime_directories
    from classroom_app.database import init_database
    from classroom_app.services import scheduled_task_handlers  # noqa: F401 - registers handlers
    from classroom_app.services.scheduled_task_service import (
        process_due_scheduled_tasks_once,
        run_scheduler_worker_forever,
        update_scheduler_heartbeat,
        _await_owned_work,
    )

    worker_id = args.worker_id or os.getenv("SCHEDULER_WORKER_ID") or f"scheduler-{socket.gethostname()}"
    workers = []
    initialized = once_completed = False
    primary_error = None
    try:
        ensure_runtime_directories()
        init_database()
        initialized = True
        if stop is not None and stop["requested"]:
            return  # A signal during synchronous initialization must not claim.
        from classroom_app.services.life_tip_service import warm_life_tip_runtime
        try:
            await _await_owned_work(asyncio.create_task(asyncio.to_thread(warm_life_tip_runtime)),
                                    operation="life tip startup warmup")
        except Exception as exc:
            print(f"[LIFE_TIP] Scheduler warmup deferred: {type(exc).__name__}")
        if stop is not None and stop["requested"]:
            return
        if args.once:
            result = await process_due_scheduled_tasks_once()
            once_completed = True
            print(f"[SCHEDULER] processed one batch: {result}")
            return

        from classroom_app.services.wanderer_inbox_worker import run_forever as run_wanderer_inbox
        workers = [
            asyncio.create_task(run_scheduler_worker_forever(worker_id=worker_id, poll_seconds=args.poll_seconds)),
            asyncio.create_task(run_wanderer_inbox()),
        ]
        await asyncio.gather(*workers)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if stop is not None:
            stop["draining"] = True
        for worker in workers:
            if not worker.done() and not worker.cancelling():
                worker.cancel()
        # A child failure/cancellation can end gather before a sibling finishes
        # its owned work. Do not let Runner cancel those draining tasks again.
        await asyncio.gather(*workers, return_exceptions=True)
        failure = primary_error if isinstance(primary_error, Exception) else getattr(primary_error, "__cause__", None)
        for worker in workers:
            try:
                worker.result()
            except asyncio.CancelledError as exc:
                failure = failure or exc.__cause__
            except Exception as exc:
                failure = failure or exc
        if initialized and stop is not None:
            # Worker cancellation can leave a to_thread heartbeat in flight.
            # Main writes this only after Runner has joined its executor, so a
            # late running/once write cannot overwrite the terminal state.
            stop["terminal_heartbeat"] = (
                update_scheduler_heartbeat, worker_id,
                "once" if once_completed else "error" if failure is not None else "stopped",
                type(failure).__name__ if failure is not None else "",
            )
        if failure is not None and (primary_error is None or isinstance(primary_error, asyncio.CancelledError)):
            raise failure from None


async def _run_with_shutdown(args, stop) -> None:
    stop.update(loop=asyncio.get_running_loop(), task=asyncio.current_task())
    try:
        if stop["requested"]:
            return  # A signal before loop startup must not start claiming work.
        try:
            await _run(args, stop=stop)
        except asyncio.CancelledError as exc:
            if not stop.get("cancel_sent") or exc.__cause__ is not None:
                raise
            print("[SCHEDULER] worker stopped")
    finally:
        stop.update(draining=True, loop=None, task=None)


def main() -> None:
    load_dotenv()
    _configure_stdio_encoding()
    parser = argparse.ArgumentParser(description="LanShare unified scheduled-task dispatcher")
    parser.add_argument("--once", action="store_true", help="process one due batch then exit")
    parser.add_argument("--worker-id", default="", help="stable worker id shown in management views")
    parser.add_argument("--poll-seconds", type=int, default=None, help="poll interval for the long-running worker")
    args = parser.parse_args()
    stop = {"requested": False, "cancel_sent": False, "draining": False, "loop": None, "task": None}
    with _scheduler_signals(stop):
        try:
            asyncio.run(_run_with_shutdown(args, stop))
        except KeyboardInterrupt:
            print("[SCHEDULER] worker stopped")
        finally:
            terminal = stop.get("terminal_heartbeat")
            if terminal is not None:
                write_heartbeat, worker_id, status, last_error = terminal
                try:
                    write_heartbeat(worker_id, status=status, last_error=last_error)
                except Exception as exc:
                    print(f"[SCHEDULER] final heartbeat unavailable: {type(exc).__name__}")
            # No application import on --help/partial startup failure. Close
            # after Runner has also drained its executor and remaining tasks.
            postgres = sys.modules.get("classroom_app.db.postgres")
            if postgres is not None:
                postgres.close_connection_pool()


if __name__ == "__main__":
    main()
