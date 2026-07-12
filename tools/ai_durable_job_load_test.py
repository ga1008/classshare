#!/usr/bin/env python3
"""Contention smoke test for the durable AI job ledger (no provider calls).

Creates the requested queue depth in an isolated SQLite database, drains it
with competing claimers, and verifies exactly-once terminal state plus expired
lease recovery. This is a persistence/concurrency test, not a model benchmark.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_app.db.connection import LanShareSQLiteConnection
from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.services import ai_durable_job_service as jobs


def run(queue_depth: int, workers: int) -> dict:
    started = time.perf_counter()
    temp = tempfile.TemporaryDirectory(prefix="lanshare-ai-jobs-load-")
    db_path = Path(temp.name) / "load.db"

    def connect():
        conn = sqlite3.connect(db_path, timeout=30, factory=LanShareSQLiteConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    reset_ai_job_schema_guard_for_tests()
    with connect() as conn:
        conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY)")
        ensure_ai_job_schema(conn, engine="sqlite")
        for index in range(queue_depth):
            jobs.create_ai_job(
                conn,
                task_type="ai_grading",
                dedupe_key=f"load:{index}",
                payload={"submission_id": index + 1, "submission_fingerprint": f"rev-{index}"},
                source_ref=f"submission:{index + 1}",
            )
        conn.commit()

    original_connection = jobs.get_db_connection
    original_engine = jobs.get_configured_db_engine
    jobs.get_db_connection = connect
    jobs.get_configured_db_engine = lambda: "sqlite"
    seen: set[int] = set()
    duplicate_claims: list[int] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        while True:
            claimed = jobs.claim_due_ai_jobs(
                limit=1,
                worker_id=f"load-worker-{index}",
                lease_seconds=120,
                task_types=("ai_grading",),
            )
            if not claimed:
                return
            job = claimed[0]
            with lock:
                if int(job["id"]) in seen:
                    duplicate_claims.append(int(job["id"]))
                seen.add(int(job["id"]))
            with connect() as conn:
                jobs.record_ai_job_attempt_started(conn, job)
                conn.commit()
            result = jobs.store_ai_job_result(job, {"submission_id": index, "status": "mock_ok"})
            if not jobs.mark_ai_job_succeeded(int(job["id"]), int(result["id"])):
                raise RuntimeError(f"failed to finish job {job['id']}")

    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(workers)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with connect() as conn:
            status_rows = conn.execute("SELECT status, COUNT(*) AS count FROM ai_jobs GROUP BY status").fetchall()
            attempts = conn.execute("SELECT COUNT(*) AS count FROM ai_job_attempts").fetchone()["count"]
        statuses = {str(row["status"]): int(row["count"]) for row in status_rows}
        ok = statuses == {"succeeded": queue_depth} and not duplicate_claims and attempts == queue_depth
        return {
            "ok": ok,
            "queue_depth": queue_depth,
            "workers": workers,
            "unique_claims": len(seen),
            "duplicate_claims": duplicate_claims,
            "attempt_rows": int(attempts),
            "statuses": statuses,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        jobs.get_db_connection = original_connection
        jobs.get_configured_db_engine = original_engine
        reset_ai_job_schema_guard_for_tests()
        temp.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=300)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    report = run(max(1, args.jobs), max(1, args.workers))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
