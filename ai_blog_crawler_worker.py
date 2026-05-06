import argparse
import asyncio
import os
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
    from classroom_app.database import get_db_connection, init_database
    from classroom_app.services.blog_news_crawler_service import (
        enqueue_blog_news_crawler_run,
        process_due_blog_news_crawler_runs_once,
        run_blog_news_crawler_worker_forever,
    )

    ensure_runtime_directories()
    init_database()

    worker_id = args.worker_id or os.getenv("BLOG_NEWS_CRAWLER_WORKER_ID") or ""
    if args.enqueue:
        with get_db_connection() as conn:
            run = enqueue_blog_news_crawler_run(conn, trigger_source="manual", worker_id=worker_id)
            conn.commit()
        print(f"[BLOG_NEWS] enqueued run: {run.get('id')}")

    if args.once:
        result = await process_due_blog_news_crawler_runs_once(worker_id=worker_id)
        print(f"[BLOG_NEWS] once result: {result}")
        return

    await run_blog_news_crawler_worker_forever(
        worker_id=worker_id,
        poll_seconds=args.poll_seconds,
    )


def main() -> None:
    load_dotenv()
    _configure_stdio_encoding()
    parser = argparse.ArgumentParser(description="LanShare AI blog news crawler worker")
    parser.add_argument("--once", action="store_true", help="process one due crawler run then exit")
    parser.add_argument("--enqueue", action="store_true", help="enqueue a manual run before processing")
    parser.add_argument("--worker-id", default="", help="stable worker id shown in the management page")
    parser.add_argument("--poll-seconds", type=int, default=None, help="poll interval for the long-running worker")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("[BLOG_NEWS] worker stopped")


if __name__ == "__main__":
    main()
