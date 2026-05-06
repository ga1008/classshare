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


def main() -> None:
    load_dotenv()
    _configure_stdio_encoding()

    from classroom_app.config import ensure_runtime_directories
    from classroom_app.database import init_database
    from classroom_app.services.email_notification_service import (
        process_due_email_jobs_once,
        run_email_worker_forever,
        update_email_worker_heartbeat,
    )

    ensure_runtime_directories()
    init_database()

    worker_id = os.getenv("EMAIL_WORKER_ID") or f"mailer-{socket.gethostname()}"
    if "--once" in sys.argv:
        result = process_due_email_jobs_once()
        update_email_worker_heartbeat(worker_id, status="once")
        print(f"[EMAIL] processed one batch: {result}")
        return

    try:
        run_email_worker_forever(worker_id=worker_id)
    except KeyboardInterrupt:
        update_email_worker_heartbeat(worker_id, status="stopped")
        print("[EMAIL] worker stopped")


if __name__ == "__main__":
    main()
