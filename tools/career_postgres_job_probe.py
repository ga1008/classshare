"""Exercise career job admission/leases/publication in an isolated local PG schema.

This checks the real PostgreSQL dialect and competing workers, not model speed
or the capacity of the production server. It never reads application rows.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import sys
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from classroom_app import config
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
from classroom_app.services import ai_durable_job_service as durable
from classroom_app.services import student_career_job_service as service
from classroom_app.services import student_career_job_worker as worker
from classroom_app.services import file_service as files


def probe_file_binding_race(connect) -> dict:
    """Two real connections exercise both advisory-lock acquisition orders."""
    with connect() as conn:
        conn.execute("CREATE TABLE resume_attachments (file_hash TEXT)")
        conn.commit()
    file_hash = hashlib.sha256(b"isolated file reference race").hexdigest()
    with tempfile.TemporaryDirectory(prefix="career-blob-probe-") as directory:
        path = Path(directory) / file_hash
        path.write_bytes(b"isolated file reference race")
        with patch.object(files, "global_file_candidates", return_value=(path,)), patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"):
            started = threading.Event()
            def collect():
                with connect() as conn:
                    started.set()
                    deleted = asyncio.run(files.delete_global_file(file_hash, conn=conn))
                    conn.commit()
                    return deleted
            with ThreadPoolExecutor(max_workers=1) as pool, connect() as binding:
                files.lock_global_file_references(binding, (file_hash,))
                binding.execute("INSERT INTO resume_attachments VALUES (?)", (file_hash,))
                pending = pool.submit(collect)
                assert started.wait(5)
                binding.commit()
                retained = pending.result(timeout=10) is False and path.is_file()
            with connect() as conn:
                conn.execute("DELETE FROM resume_attachments")
                conn.commit()
            started.clear()
            def bind():
                with connect() as conn:
                    started.set()
                    try:
                        files.lock_global_file_references(conn, (file_hash,))
                    except ValueError:
                        conn.rollback()
                        return False
                    conn.execute("INSERT INTO resume_attachments VALUES (?)", (file_hash,))
                    conn.commit()
                    return True
            with ThreadPoolExecutor(max_workers=1) as pool, connect() as collection:
                assert asyncio.run(files.delete_global_file(file_hash, conn=collection)) is False
                # Explicit test-only offline GC simulation. Production request
                # deletion retains shared bytes until all legacy references
                # and pending uploads can be audited safely.
                files.lock_global_file_references(collection, (file_hash,), require_exists=False)
                path.unlink()
                pending = pool.submit(bind)
                assert started.wait(5)
                collection.commit()
                rejected = pending.result(timeout=10) is False and not path.exists()
            with connect() as conn:
                references = conn.execute("SELECT COUNT(*) FROM resume_attachments").fetchone()[0]
    return {"binding_first_preserves_file": retained, "collection_first_rejects_dangling_reference": rejected and references == 0}


def run(count: int, workers: int) -> dict:
    parsed = urlsplit(config.DATABASE_URL)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This probe only permits a local PostgreSQL server")
    schema = "career_probe_" + uuid.uuid4().hex
    created = False
    started = time.perf_counter()
    handlers = dict(service._HANDLERS)
    policies = dict(durable.TASK_POLICIES)
    max_pending = service.MAX_PENDING_JOBS
    service._HANDLERS.clear()
    service.MAX_PENDING_JOBS = max(service.MAX_PENDING_JOBS, count + 1)
    patches = []
    admission_ms = []
    claimed_ids = []
    guard = threading.Lock()

    def connect():
        raw = psycopg.connect(config.DATABASE_URL, connect_timeout=5,
            row_factory=sqlite_compatible_dict_row, options=f"-c search_path={schema}")
        return LanSharePostgresConnection(raw)

    async def execute(job, payload):
        await asyncio.sleep(0.005)
        return {"value": "generated"}

    def apply(conn, job, payload, result):
        return conn.execute("UPDATE probe_documents SET value=?,writes=writes+1 WHERE id=? AND revision=?",
                            (result["value"], payload["document_id"], payload["revision"])).rowcount == 1

    service.register_student_career_handler("career_probe", execute=execute, apply=apply)
    try:
        with psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
        reset_ai_job_schema_guard_for_tests()
        with connect() as conn:
            conn.execute("CREATE TABLE submissions (id BIGINT PRIMARY KEY)")
            conn.execute("CREATE TABLE probe_documents (id BIGINT PRIMARY KEY,revision INTEGER,value TEXT,writes INTEGER NOT NULL DEFAULT 0)")
            ensure_ai_job_schema(conn, engine="postgres")
            conn.executemany("INSERT INTO probe_documents(id,revision,value) VALUES (?,1,'draft')", [(n,) for n in range(count)])
            conn.commit()
        patches = [patch.object(durable, "get_db_connection", connect), patch.object(worker, "get_db_connection", connect)]
        for target in (durable, service, worker):
            patches.append(patch.object(target, "get_configured_db_engine", return_value="postgres"))
        for item in patches:
            item.start()

        def submit(n):
            tick = time.perf_counter()
            with connect() as conn:
                job = service.enqueue_student_career_job(conn, task_type="career_probe", dedupe_key=f"probe:{n}",
                    payload={"document_id": n, "revision": 1}, student_id=n + 1, scope_type="probe", scope_id=str(n))
                conn.commit()
            with guard:
                admission_ms.append((time.perf_counter() - tick) * 1000)
            return job["id"]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            ids = list(pool.map(submit, range(count)))
            duplicate_ids = list(pool.map(submit, range(min(count, 50))))
        assert duplicate_ids == ids[:len(duplicate_ids)]
        peak_running = 0

        def process(index):
            nonlocal peak_running
            while time.perf_counter() - started < 300:
                delivery = durable.claim_result_ready_ai_jobs(limit=1, worker_id=f"probe-deliver-{index}", task_types=("career_probe",))
                if delivery:
                    worker._apply_result(delivery[0])
                    continue
                claimed = durable.claim_due_ai_jobs(limit=1, worker_id=f"probe-{index}", task_types=("career_probe",),
                    max_running=2, concurrency_lock_key=service.career_concurrency_lock_key(schema), fair_owner=True)
                if claimed:
                    with connect() as conn:
                        active = conn.execute("SELECT COUNT(*) AS n FROM ai_jobs WHERE status='running'").fetchone()["n"]
                    with guard:
                        claimed_ids.append(claimed[0]["id"])
                        peak_running = max(peak_running, int(active))
                    asyncio.run(worker._execute(claimed[0]))
                    continue
                with connect() as conn:
                    active = conn.execute("SELECT COUNT(*) AS n FROM ai_jobs WHERE status IN ('queued','running','retry_wait','result_ready')").fetchone()["n"]
                if not active:
                    return
                time.sleep(.03)
            raise TimeoutError("Probe did not drain in its five-minute budget")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(process, range(workers)))
        with connect() as conn:
            statuses = {r["status"]: int(r["n"]) for r in conn.execute("SELECT status,COUNT(*) AS n FROM ai_jobs GROUP BY status")}
            writes = conn.execute("SELECT COUNT(*) AS n FROM probe_documents WHERE writes=1 AND value='generated'").fetchone()["n"]
        # Cancel after delivery has read its lease, before it publishes the
        # business row. The final lease CAS must roll every domain write back.
        with connect() as conn:
            conn.execute("INSERT INTO probe_documents(id,revision,value) VALUES (?,1,'draft')", (count,))
            conn.commit()
        cancel_id = submit(count)
        cancel_job = durable.claim_due_ai_jobs(limit=1, worker_id="cancel-execute", task_types=("career_probe",))[0]
        asyncio.run(worker._execute(cancel_job))
        delivery = durable.claim_result_ready_ai_jobs(limit=1, worker_id="cancel-deliver", task_types=("career_probe",))[0]
        entered, released = threading.Event(), threading.Event()
        def paused_apply(conn, job, payload, result):
            entered.set()
            if not released.wait(10):
                raise TimeoutError("Cancellation probe coordination timed out")
            return apply(conn, job, payload, result)
        service.register_student_career_handler("career_probe", execute=execute, apply=paused_apply)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker._apply_result, delivery)
            try:
                assert entered.wait(10)
                with connect() as conn:
                    service.cancel_student_career_job(conn, cancel_id, student_id=count + 1)
                    conn.commit()
            finally:
                released.set()
            cancelled_publish = future.result(timeout=10) is False
        with connect() as conn:
            cancelled_row = conn.execute("SELECT value,writes FROM probe_documents WHERE id=?", (count,)).fetchone()
        cancellation_safe = cancelled_publish and cancelled_row["value"] == "draft" and cancelled_row["writes"] == 0
        # A real row lock can delay result storage beyond its lease even when
        # no competing worker has reclaimed the token yet.
        lease_id = submit(count + 1)
        lease_job = durable.claim_due_ai_jobs(worker_id="expiry-writer", task_types=("career_probe",))[0]
        entered.clear()
        def store_after_lock():
            entered.set()
            try:
                durable.store_ai_job_result(lease_job, {"value": "late"}, require_valid_lease=True)
            except RuntimeError as exc:
                return "lease changed" in str(exc)
            return False
        with ThreadPoolExecutor(max_workers=1) as pool, connect() as blocker:
            blocker.execute("UPDATE ai_jobs SET lease_expires_at=? WHERE id=?", (durable._iso(durable._now() + timedelta(seconds=2)), lease_id))
            blocker.commit()
            blocker.execute("SELECT id FROM ai_jobs WHERE id=? FOR UPDATE", (lease_id,)).fetchone()
            pending = pool.submit(store_after_lock)
            try:
                assert entered.wait(5)
                time.sleep(2.1)
            finally:
                blocker.commit()
            lock_wait_rejected = pending.result(timeout=10)
        with connect() as conn:
            lock_wait_rejected = lock_wait_rejected and conn.execute("SELECT COUNT(*) FROM ai_job_results WHERE job_id=?", (lease_id,)).fetchone()[0] == 0
        file_race = probe_file_binding_race(connect)
        ordered = sorted(admission_ms)
        return {"ok": statuses == {"succeeded": count} and writes == count and len(set(claimed_ids)) == count and len(claimed_ids) == count and peak_running <= 2 and cancellation_safe and lock_wait_rejected and all(file_race.values()),
                "engine": "postgres", "jobs": count, "competing_workers": workers, "lane_limit": 2,
                "observed_peak_running": peak_running, "published_once": writes, "statuses": statuses,
                "unique_claims": len(set(claimed_ids)), "total_claims": len(claimed_ids),
                "cancellation_during_publish_rolls_back_business": cancellation_safe,
                "row_lock_wait_past_lease_rejects_result": lock_wait_rejected,
                "file_binding_race": file_race,
                "admission_p95_ms": round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 2),
                "elapsed_seconds": round(time.perf_counter() - started, 3), "schema_cleanup": "pending"}
    finally:
        for item in reversed(patches):
            item.stop()
        service._HANDLERS.clear()
        service._HANDLERS.update(handlers)
        durable.TASK_POLICIES.clear()
        durable.TASK_POLICIES.update(policies)
        service.MAX_PENDING_JOBS = max_pending
        reset_ai_job_schema_guard_for_tests()
        if created:
            if not schema.startswith("career_probe_") or len(schema) != len("career_probe_") + 32:
                raise RuntimeError("Refusing to drop an unverified probe schema")
            with psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                assert admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()[0] == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=300)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run(max(1, min(2000, args.jobs)), max(1, min(12, args.workers)))
    report["schema_cleanup"] = "verified"
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    raise SystemExit(0 if report["ok"] else 1)
