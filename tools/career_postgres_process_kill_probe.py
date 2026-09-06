"""Kill this tool's own spawned workers against an isolated local PostgreSQL schema.

No model calls, production rows, process-name termination, or production
constant changes. Recovery accelerates only owned test-row lease timestamps;
it does not measure the production 120-second wall-clock recovery interval.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import multiprocessing
import os
import re
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reuse the existing PG job probe's driver, connection wrapper and service
# imports, preserving its localhost + generated-schema safety boundary.
from tools.career_postgres_job_probe import (
    config, durable, worker, service, psycopg, sql,
    LanSharePostgresConnection, sqlite_compatible_dict_row,
    ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests,
)
from classroom_app.db import schema_ai_jobs

TASK_TYPE = "career_process_kill_probe"
SCHEMA_PATTERN = re.compile(r"career_kill_probe_[0-9a-f]{32}\Z")
PHASES = ("after_claim", "execute_wait", "result_persisted", "apply_uncommitted")


def validate_boundary(database_url: str, schema: str) -> None:
    if urlsplit(database_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Process-kill probe permits only localhost PostgreSQL")
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("Process-kill probe requires its own generated schema")


def _connect_factory(schema):
    validate_boundary(config.DATABASE_URL, schema)
    def connect():
        raw = psycopg.connect(config.DATABASE_URL, connect_timeout=5,
            row_factory=sqlite_compatible_dict_row,
            options=f"-c search_path={schema} -c statement_timeout=15000 -c lock_timeout=5000",
            application_name=schema)
        return LanSharePostgresConnection(raw)
    return connect


async def _execute_normal(job, payload):
    return {"value": f"generated-revision-{payload['revision']}"}


def _apply_normal(conn, job, payload, result):
    changed = conn.execute(
        "UPDATE probe_documents SET value=?,writes=writes+1 WHERE id=? AND revision=? AND current_job_id=?",
        (result["value"], payload["document_id"], payload["revision"], job["id"]),
    ).rowcount == 1
    if changed:
        # Deliberately no unique constraint: the assertion must detect duplicate
        # application, rather than letting a test-table constraint hide it.
        conn.execute("INSERT INTO probe_publications(document_id,revision,job_id) VALUES(?,?,?)",
                     (payload["document_id"], payload["revision"], job["id"]))
    return changed


@contextmanager
def _runtime(schema, *, execute=_execute_normal, apply=_apply_normal):
    connect = _connect_factory(schema)
    handlers, policies = dict(service._HANDLERS), dict(durable.TASK_POLICIES)
    reset_ai_job_schema_guard_for_tests()
    try:
        with ExitStack() as stack:
            for module in (durable, worker):
                stack.enter_context(patch.object(module, "get_db_connection", connect))
            for module in (durable, worker, service, schema_ai_jobs):
                stack.enter_context(patch.object(module, "get_configured_db_engine", return_value="postgres"))
            stack.enter_context(patch.object(service, "CAREER_JOBS_ENABLED", True))
            service._HANDLERS.clear()
            service.register_student_career_handler(TASK_TYPE, execute=execute, apply=apply, timeout_seconds=90)
            yield connect
    finally:
        service._HANDLERS.clear(); service._HANDLERS.update(handlers)
        durable.TASK_POLICIES.clear(); durable.TASK_POLICIES.update(policies)
        reset_ai_job_schema_guard_for_tests()


@contextmanager
def isolated_process_kill_postgres():
    schema = "career_kill_probe_" + uuid.uuid4().hex
    validate_boundary(config.DATABASE_URL, schema)
    created = False
    state = {"schema": schema, "schema_removed": False, "owned_sessions_remaining": None}
    try:
        with psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
        with _runtime(schema) as connect:
            state["connect"] = connect
            with connect() as conn:
                conn.execute("CREATE TABLE submissions(id BIGINT PRIMARY KEY)")
                conn.execute("CREATE TABLE probe_documents(id BIGINT PRIMARY KEY,revision INTEGER NOT NULL,current_job_id BIGINT,value TEXT,writes INTEGER NOT NULL DEFAULT 0)")
                conn.execute("CREATE TABLE probe_publications(document_id BIGINT,revision INTEGER,job_id BIGINT)")
                ensure_ai_job_schema(conn, engine="postgres")
                conn.commit()
            yield state
    finally:
        if created:
            validate_boundary(config.DATABASE_URL, schema)
            with psycopg.connect(config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
                admin.execute("SET statement_timeout='15s'")
                admin.execute("SET lock_timeout='5s'")
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                state["schema_removed"] = admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()[0] == 0
                state["owned_sessions_remaining"] = admin.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE application_name=%s", (schema,)).fetchone()[0]
                assert state["schema_removed"] and state["owned_sessions_remaining"] == 0


def _claim_execute():
    return durable.claim_due_ai_jobs(limit=1, worker_id="owned-kill-probe-execute", task_types=(TASK_TYPE,), lease_seconds=worker.LEASE_SECONDS)


def _claim_apply():
    return durable.claim_result_ready_ai_jobs(limit=1, worker_id="owned-kill-probe-apply", task_types=(TASK_TYPE,), lease_seconds=worker.LEASE_SECONDS)


def _child_main(schema, phase, channel):
    """Spawn-safe entry; all pauses expire even if the controller disappears."""
    validate_boundary(config.DATABASE_URL, schema)
    if phase not in PHASES:
        raise ValueError("Unknown kill checkpoint")

    def checkpoint(job):
        channel.send({"phase": phase, "pid": os.getpid(), "job": job})

    async def execute(job, payload):
        if phase == "execute_wait":
            checkpoint(job)
            await asyncio.sleep(45)
            raise TimeoutError("Controller did not terminate its test worker")
        return await _execute_normal(job, payload)

    def apply(conn, job, payload, result):
        changed = _apply_normal(conn, job, payload, result)
        if phase == "apply_uncommitted":
            assert changed
            checkpoint(job)
            if not channel.poll(45):
                raise TimeoutError("Controller did not terminate its uncommitted worker")
            raise RuntimeError("A kill checkpoint must not resume")
        return changed

    try:
        with _runtime(schema, execute=execute, apply=apply):
            jobs = _claim_execute()
            assert len(jobs) == 1
            job = jobs[0]
            if phase == "after_claim":
                checkpoint(job)
            else:
                asyncio.run(worker._execute(job))
                if phase == "result_persisted":
                    checkpoint(job)
                elif phase == "apply_uncommitted":
                    delivery = _claim_apply()
                    assert len(delivery) == 1
                    asyncio.run(worker._deliver(delivery[0]))
                    raise AssertionError("Uncommitted checkpoint unexpectedly completed")
            if not channel.poll(45):
                raise TimeoutError("Controller did not terminate its paused worker")
    except BaseException as exc:
        try:
            channel.send({"error_type": type(exc).__name__, "pid": os.getpid()})
        finally:
            raise
    finally:
        channel.close()


@contextmanager
def owned_paused_worker(schema, phase):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=_child_main, args=(schema, phase, child), name="career-owned-kill-probe")
    process.start(); child.close()
    message = {}
    try:
        if not parent.poll(30):
            raise TimeoutError("Owned worker did not reach checkpoint in 30 seconds")
        message = parent.recv()
        if message.get("error_type"):
            raise RuntimeError("Owned worker failed: " + message["error_type"])
        assert process.pid == message["pid"] and message["phase"] == phase and process.is_alive()
        yield process, message
    finally:
        # Terminate only this Process handle, created immediately above. No
        # process enumeration, name matching, taskkill, or arbitrary PID API.
        if process.is_alive():
            process.kill()
            message["force_kill_called"] = True
        process.join(timeout=10)
        if process.is_alive():
            raise RuntimeError("Could not terminate this tool's own child")
        message["exit_code"] = process.exitcode
        message["joined"] = True
        parent.close(); process.close()


def _submit(connect, document_id, *, revision=1, create=True):
    with connect() as conn:
        if create:
            conn.execute("INSERT INTO probe_documents(id,revision,value) VALUES(?,?,'draft')", (document_id, revision))
        job = service.enqueue_student_career_job(conn, task_type=TASK_TYPE,
            dedupe_key=f"kill:{document_id}:{revision}", payload={"document_id": document_id, "revision": revision},
            student_id=document_id, scope_type="kill-probe", scope_id=str(document_id))
        conn.execute("UPDATE probe_documents SET current_job_id=? WHERE id=? AND revision=?", (job["id"], document_id, revision))
        conn.commit()
    return job


def _inspect(connect, job_id, document_id):
    with connect() as conn:
        return {
            "job": dict(conn.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()),
            "document": dict(conn.execute("SELECT * FROM probe_documents WHERE id=?", (document_id,)).fetchone()),
            "result_count": conn.execute("SELECT COUNT(*) FROM ai_job_results WHERE job_id=?", (job_id,)).fetchone()[0],
            "publications": [dict(row) for row in conn.execute("SELECT revision,job_id FROM probe_publications WHERE document_id=?", (document_id,))],
        }


def _expire_owned_test_lease(connect, job_id):
    # Test-time advancement is applied only after the child has died. It changes
    # neither production defaults nor the claim/CAS implementation being tested.
    with connect() as conn:
        count = conn.execute("UPDATE ai_jobs SET lease_expires_at=? WHERE id=? AND task_type=? AND status IN ('running','result_ready')",
                             (durable._iso(durable._now() - timedelta(seconds=1)), job_id, TASK_TYPE)).rowcount
        assert count == 1
        conn.commit()


def _reject_late_result(job):
    try:
        durable.store_ai_job_result(job, {"value": "stale-worker-result"}, require_valid_lease=True)
    except RuntimeError as exc:
        assert "lease changed" in str(exc)
        return True
    raise AssertionError("Stale worker result was accepted")


def _recover(connect, original, phase):
    if phase in {"after_claim", "execute_wait"}:
        assert _claim_execute() == [], "active lease must block recovery before test expiry"
        _expire_owned_test_lease(connect, original["id"])
        recovered = _claim_execute()
        assert len(recovered) == 1 and recovered[0]["id"] == original["id"]
        assert recovered[0]["lease_token"] != original["lease_token"]
        _reject_late_result(original)
        asyncio.run(worker._execute(recovered[0]))
    elif phase == "apply_uncommitted":
        assert _claim_apply() == [], "active delivery lease must block replay before test expiry"
        _expire_owned_test_lease(connect, original["id"])
    delivery = _claim_apply()
    assert len(delivery) == 1 and delivery[0]["id"] == original["id"]
    assert worker._apply_result(delivery[0]) is True
    assert worker._apply_result(delivery[0]) is False, "same delivery token must not publish twice"
    assert worker._apply_result(original) is False, "terminated worker token must not overwrite recovery"
    return delivery[0]


def run():
    started = time.perf_counter()
    cases, admitted = [], []
    with isolated_process_kill_postgres() as fixture:
        connect, schema = fixture["connect"], fixture["schema"]
        for document_id, phase in enumerate(PHASES, start=1):
            admitted_job = _submit(connect, document_id); admitted.append(admitted_job["id"])
            with owned_paused_worker(schema, phase) as (process, message):
                before = _inspect(connect, admitted_job["id"], document_id)
                assert before["document"]["writes"] == 0 and before["publications"] == []
                assert before["result_count"] == int(phase in {"result_persisted", "apply_uncommitted"})
                pid = process.pid
                # Exiting the context force-kills this exact paused process.
            original = message["job"]
            assert message.get("force_kill_called") and message["joined"] and message["exit_code"] not in (None, 0)
            after_kill = _inspect(connect, admitted_job["id"], document_id)
            assert after_kill["document"]["writes"] == 0 and after_kill["publications"] == []
            _recover(connect, original, phase)
            final = _inspect(connect, admitted_job["id"], document_id)
            assert final["job"]["status"] == "succeeded" and final["document"]["writes"] == 1
            assert final["publications"] == [{"revision": 1, "job_id": admitted_job["id"]}]
            assert final["result_count"] == 1
            expected_attempts = 2 if phase in {"after_claim", "execute_wait"} else 1
            assert final["job"]["attempt_count"] == expected_attempts
            cases.append({"checkpoint": phase, "forced_child_pid": pid, "checkpoint_reached": True,
                "force_kill_called": message["force_kill_called"], "child_exit_code": message["exit_code"], "child_joined": message["joined"],
                "committed_writes_immediately_after_kill": 0, "status": "succeeded", "published_once": True,
                "execution_attempts": final["job"]["attempt_count"], "persisted_results": final["result_count"],
                "result_replayed_without_reexecution": phase in {"result_persisted", "apply_uncommitted"},
                "old_delivery_rejected": True})
            print(f"Process kill checkpoint passed: {phase}", flush=True)

        # Cancel a currently executing accepted task before terminating it.
        document_id = 5; cancelled = _submit(connect, document_id); admitted.append(cancelled["id"])
        with owned_paused_worker(schema, "execute_wait") as (_, message):
            with connect() as conn:
                conn.execute("SELECT id FROM probe_documents WHERE id=? FOR UPDATE", (document_id,)).fetchone()
                assert service.cancel_student_career_job(conn, cancelled["id"], student_id=document_id)["status"] == "cancelled"
                conn.commit()
        assert message.get("force_kill_called") and message["joined"] and message["exit_code"] not in (None, 0)
        assert _reject_late_result(message["job"])
        assert _claim_execute() == [] and _claim_apply() == []
        final = _inspect(connect, cancelled["id"], document_id)
        assert final["job"]["status"] == "cancelled" and final["document"]["writes"] == 0
        assert final["result_count"] == 0 and final["publications"] == []
        cancellation = {"cancelled_while_executing": True, "late_result_rejected": True, "accepted_job_retained": True, "writes": 0,
                        "force_kill_called": message["force_kill_called"], "child_exit_code": message["exit_code"], "child_joined": message["joined"]}

        # A crashed publication is followed by an edit/new job. Deliberately
        # omit eager supersede to verify the independent domain revision fence.
        document_id = 6; old = _submit(connect, document_id); admitted.append(old["id"])
        with owned_paused_worker(schema, "apply_uncommitted") as (_, message):
            pass
        assert message.get("force_kill_called") and message["joined"] and message["exit_code"] not in (None, 0)
        with connect() as conn:
            conn.execute("UPDATE probe_documents SET revision=2,value='edited-v2' WHERE id=?", (document_id,))
            conn.commit()
        new = _submit(connect, document_id, revision=2, create=False); admitted.append(new["id"])
        _expire_owned_test_lease(connect, old["id"])
        old_delivery = _claim_apply(); assert len(old_delivery) == 1 and old_delivery[0]["id"] == old["id"]
        assert worker._apply_result(old_delivery[0]) is False
        assert worker._apply_result(message["job"]) is False
        assert _reject_late_result(message["job"])
        before_new = _inspect(connect, old["id"], document_id)
        assert before_new["job"]["status"] == "superseded" and before_new["document"]["value"] == "edited-v2"
        assert before_new["document"]["writes"] == 0 and before_new["publications"] == []
        new_claim = _claim_execute(); assert len(new_claim) == 1 and new_claim[0]["id"] == new["id"]
        asyncio.run(worker._execute(new_claim[0]))
        new_delivery = _claim_apply(); assert len(new_delivery) == 1
        assert worker._apply_result(new_delivery[0]) is True
        assert worker._apply_result(new_delivery[0]) is False
        final = _inspect(connect, new["id"], document_id)
        assert final["document"]["revision"] == 2 and final["document"]["value"] == "generated-revision-2"
        assert final["publications"] == [{"revision": 2, "job_id": new["id"]}]
        revision = {"crashed_transaction_rolled_back": True, "missed_eager_invalidation_guarded_by_revision": True,
                    "old_result_retained_for_audit": before_new["result_count"] == 1, "old_job_status": "superseded",
                    "new_revision_published_once": True, "old_revision_publications": 0,
                    "force_kill_called": message["force_kill_called"], "child_exit_code": message["exit_code"], "child_joined": message["joined"]}
        with connect() as conn:
            stored_ids = [row[0] for row in conn.execute("SELECT id FROM ai_jobs ORDER BY id")]
            statuses = {row["status"]: row["n"] for row in conn.execute("SELECT status,COUNT(*) AS n FROM ai_jobs GROUP BY status")}
            duplicates = conn.execute("SELECT COUNT(*) FROM (SELECT document_id,revision FROM probe_publications GROUP BY document_id,revision HAVING COUNT(*)>1) AS duplicate_revisions").fetchone()[0]
        assert sorted(admitted) == stored_ids and statuses == {"succeeded": 5, "cancelled": 1, "superseded": 1}
        assert duplicates == 0
    return {"ok": True, "engine": "local PostgreSQL / real spawned worker processes", "synthetic_only": True,
        "forced_own_processes": 6, "checkpoints": cases, "cancellation": cancellation, "revision_fence": revision,
        "admitted_jobs": len(admitted), "retained_jobs": len(stored_ids), "final_statuses": statuses,
        "duplicate_publications_per_revision": duplicates, "schema_removed": fixture["schema_removed"],
        "owned_sessions_remaining": fixture["owned_sessions_remaining"],
        "timing_method": "After confirming the active lease prevents claim, expire only the terminated worker's isolated test-row lease; production constants unchanged.",
        "production_lease_seconds": worker.LEASE_SECONDS, "production_heartbeat_seconds": worker.HEARTBEAT_SECONDS,
        "not_measured": ["production 120-second wall-clock recovery", "real upstream AI cancellation", "host power loss or PostgreSQL server crash", "production mixed workload"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            "classroom_app/services/student_career_job_worker.py", "classroom_app/services/student_career_job_service.py",
            "classroom_app/services/ai_durable_job_service.py", "tools/career_postgres_process_kill_probe.py")}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    raise SystemExit(0 if report["ok"] else 1)
