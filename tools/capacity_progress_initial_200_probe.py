"""Real initial-200 projection closure in a random, ownership-marked PG clone.

Source reads use the existing synthetic native boundary and an exported dump
snapshot. Only the restored clone is seeded/scheduled/deleted. No metrics,
publication, transaction or wall-clock mock is used, and no HTTP/AI is called.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(value, message):
    if not value:
        raise AssertionError(message)


async def measure_projection(runtime, fixture, report):
    from classroom_app.database import get_db_connection
    from classroom_app.db.postgres import LanSharePostgresConnection, get_pool_stats
    from classroom_app.services import learning_progress_service as progress
    from classroom_app.services import scheduled_task_handlers as handlers
    from classroom_app.services import scheduled_task_service as scheduler
    from tools.platform_capacity_background import _committed_projection_recorder

    offering = int(fixture["classOfferingId"])
    # Preserve every historical snapshot/event. Bring the cloned population
    # current before adding new identities, outside the measured initial phase.
    prepared_at = time.perf_counter()
    baseline_batches = []
    while True:
        with get_db_connection() as conn:
            missing = progress._pending_learning_snapshot_keys(conn, limit=1)
            dirty = conn.execute("SELECT COUNT(*) AS n FROM learning_progress_snapshots WHERE dirty=1").fetchone()["n"]
        if not missing and not dirty:
            break
        require(time.perf_counter()-prepared_at < 180, "Existing cloned population did not become clean; initial-200 scope is ambiguous")
        baseline_batches.append(await asyncio.to_thread(progress.refresh_pending_learning_snapshots,
                                                        limit=100, time_budget_seconds=3))
    report["baseline_preparation"] = {"seconds": round(time.perf_counter()-prepared_at, 3), "batches": baseline_batches}
    prefix = "INITIAL200-" + uuid.uuid4().hex[:12]
    with get_db_connection() as conn:
        base = dict(conn.execute("SELECT * FROM students WHERE id=?", (fixture["student"]["id"],)).fetchone())
        owned_class = conn.execute("INSERT INTO classes(name,created_by_teacher_id) VALUES(?,?) RETURNING id",
                                   (prefix, fixture["teacher"]["id"])).fetchone()["id"]
        conn.execute("""INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id,is_primary,source)
            VALUES(?,?,?,0,'manual')""", (offering, owned_class, fixture["teacher"]["id"]))
        students = []
        for index in range(200):
            row = conn.execute("""INSERT INTO students(student_id_number,name,class_id,gender,email,hashed_password,
                password_reset_required,password_updated_at,description) VALUES(?,?,?,'unknown',?,?,0,?,?) RETURNING id""",
                (f"{prefix}-{index:03d}", f"Initial projection synthetic {index:03d}", owned_class,
                 f"initial-{prefix.lower()}-{index}@example.invalid", base["hashed_password"],
                 base.get("password_updated_at"), "owned initial-200 projection fixture")).fetchone()
            students.append(int(row["id"]))
        # Copied autonomous/provider jobs never execute in this probe. Reset
        # only the clone's canonical projection timer for the real three lanes.
        conn.execute("UPDATE scheduled_tasks SET status='cancelled' WHERE status IN ('pending','running')")
        timer = progress.ensure_cultivation_snapshot_refresh_task(conn, advance_pending=False)
        conn.execute("""UPDATE scheduled_tasks SET status='pending',run_at=?,attempt_count=0,
            locked_at=NULL,locked_by='',started_at=NULL,finished_at=NULL WHERE id=? AND task_kind=?
            AND dedupe_key='cultivation:snapshot-refresh'""",
            ((datetime.now()+timedelta(seconds=5)).isoformat(timespec="seconds"), timer,
             progress.CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND))
    ids = set(students)
    marks = ",".join("?" for _ in students)

    def fact_rows():
        with get_db_connection() as conn:
            return [dict(row) for row in conn.execute(f"""SELECT student_id,dirty,dirty_version,calculated_version,score
                FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id IN ({marks})""",
                (offering, *students)).fetchall()]

    before = {}
    with get_db_connection() as conn:
        missing = progress._pending_learning_snapshot_keys(conn, limit=500)
        require(len(missing) == 200 and {(int(row["class_offering_id"]), int(row["student_id"])) for row in missing}
                == {(offering, student) for student in students}, "Discovery has keys beyond the exact owned initial-200 population")
        for table in ("learning_progress_snapshots", "learning_stage_status", "cultivation_score_events"):
            before[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        statements = []
        class ReadTrace:
            def execute(self, statement, values=()):
                statements.append(statement)
                return conn.execute(statement, values)
            def __getattr__(self, name):
                return getattr(conn, name)
        for student in students:
            state = progress.get_student_learning_state(ReadTrace(), offering, student)
            require(state["snapshot_status"] == "building" and state["score"] is None
                    and state["progress_percent"] is None, "Initial read fabricated a ready/zero value")
        require(not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"))
                        for statement in statements), "Initial reads wrote state")
        for table, count in before.items():
            require(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == count,
                    "Initial read changed snapshot/stage/score history")
    require(not fact_rows(), "New students already have projection rows")
    pool = get_pool_stats()
    require(pool and pool["max"] == 4, "Initial probe did not use the intended four-connection background pool")
    report.update(student_ids=students, owned_linked_class_id=int(owned_class), initial_building_reads=200, initial_read_sql=len(statements),
                  initial_read_writes=0, historical_rows_preserved=True,
                  actual_pool=pool, projection_scope="200 new accounts, one linked class, exactly 200 projection keys in one existing offering",
                  policy={"refresh_limit": handlers.CULTIVATION_SNAPSHOT_REFRESH_LIMIT,
                          "soft_budget_seconds": handlers.CULTIVATION_SNAPSHOT_REFRESH_BUDGET_SECONDS,
                          "discovery_limit": handlers.CULTIVATION_SNAPSHOT_DISCOVERY_LIMIT,
                          "discovery_budget_seconds": handlers.CULTIVATION_SNAPSHOT_DISCOVERY_BUDGET_SECONDS,
                          "recurrence_seconds": progress.CULTIVATION_SNAPSHOT_REFRESH_INTERVAL_SECONDS,
                          "scheduler_poll_seconds": 5})
    require(report["policy"] == {"refresh_limit": 25, "soft_budget_seconds": 0.75, "discovery_limit": 25,
                                 "discovery_budget_seconds": 0.1, "recurrence_seconds": 3, "scheduler_poll_seconds": 5},
            "Actual imported policy does not match candidate B")
    report["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "classroom_app/services/learning_progress_service.py", "classroom_app/services/scheduled_task_handlers.py",
        "classroom_app/services/scheduled_task_service.py", "classroom_app/db/postgres.py")}
    started, events, batches = time.perf_counter(), [], []
    event_lock = threading.Lock()

    def record(kind, **values):
        event = {"event": kind, "elapsed_seconds": time.perf_counter()-started,
                 "at": datetime.now().isoformat(), **values}
        with event_lock:
            events.append(event)

    original_publish, original_refresh, original_dispatch = progress._publish_learning_metrics, handlers.refresh_pending_learning_snapshots, scheduler._dispatch_task
    def observed_publish(conn, class_id, student, metrics, **values):
        result = original_publish(conn, class_id, student, metrics, **values)
        if result[1] and int(class_id) == offering and int(student) in ids:
            staged(conn, class_offering_id=class_id, student_id=student, version=values["expected_version"])
        return result
    def observed_refresh(**values):
        began = time.perf_counter()
        result = original_refresh(**values)
        with event_lock:
            batches.append({"started_seconds": began-started, "seconds": time.perf_counter()-began,
                            "parameters": values, **result})
        return result
    async def observed_dispatch(task):
        require(task["task_kind"] == progress.CULTIVATION_SNAPSHOT_REFRESH_TASK_KIND,
                "Unexpected copied task attempted to execute")
        record("projection_task_start", due_delay_seconds=max(0, (datetime.now()-datetime.fromisoformat(task["run_at"])).total_seconds()))
        return await original_dispatch(task)

    with ExitStack() as stack:
        staged = stack.enter_context(_committed_projection_recorder(LanSharePostgresConnection, record))
        stack.enter_context(patch.object(progress, "_publish_learning_metrics", observed_publish))
        stack.enter_context(patch.object(handlers, "refresh_pending_learning_snapshots", observed_refresh))
        stack.enter_context(patch.object(scheduler, "_dispatch_task", observed_dispatch))
        worker = asyncio.create_task(scheduler.run_scheduler_worker_forever("initial-200-owned-clone", poll_seconds=5))
        ready_at = None
        try:
            while time.perf_counter()-started <= 120:
                if worker.done():
                    worker.result()
                    raise AssertionError("Real scheduler exited early")
                rows = await asyncio.to_thread(fact_rows)
                if len(rows) == 200 and all(not row["dirty"] and row["dirty_version"] == row["calculated_version"] for row in rows):
                    ready_at = time.perf_counter()-started
                    break
                await asyncio.sleep(.5)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            report.update(events=events, batches=batches, observed_ready_seconds=ready_at,
                          final_generations=await asyncio.to_thread(fact_rows))
    require(ready_at is not None and ready_at <= 120, "Real wall-clock initial-200 closure exceeded 120 seconds")
    publications = {int(event["student_id"]): event["elapsed_seconds"] for event in events if event["event"] == "projection_published"}
    require(set(publications) == ids, "Some students lack a confirmed commit observation")
    require(max(publications.values()) <= 120, "A confirmed initial publication exceeded 120 seconds")
    with get_db_connection() as conn:
        for student in students:
            state = progress.get_student_learning_state(conn, offering, student)
            require(state["snapshot_status"] == "ready" and state["score"] is not None, "Final real learning state is not ready")
    report.update(committed_students=200, commit_seconds_by_student=publications,
                  last_committed_seconds=max(publications.values()), all_ready=True)


def run(args):
    from tools.platform_capacity_probe import establish_boundary
    from tools.platform_capacity_migration_rehearsal import SOURCE_DB, assert_clone_identity, native_environment, snapshot
    runtime, fixture = establish_boundary(args)
    require(args.cluster_dir.resolve() == (ROOT / ".codex-temp/platform-capacity-20261105-pg").resolve(), "Only the explicitly owned native cluster is allowed")
    output = args.output.resolve()
    require(output != ROOT / ".codex-temp" and output.is_relative_to((ROOT / ".codex-temp").resolve()), "Output must be an owned .codex-temp child")
    output.mkdir(parents=True, exist_ok=True)
    import psycopg
    from psycopg import sql
    source_dsn = f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/{SOURCE_DB}"
    clone, token = "lanshare_capacity_restore_" + uuid.uuid4().hex[:16], uuid.uuid4().hex
    clone_dsn = f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/{clone}"
    dump = output / "synthetic-source.dump"
    (output / "clone-owner.json").write_text(json.dumps({"database": clone, "token": token}), encoding="utf-8")
    report = {"status": "running", "clone_database": clone, "source_read_only": True,
              "production_data_accessed": False, "scope": "real initial-200 native PostgreSQL projections; no HTTP/provider capacity certification"}
    created = False
    try:
        with psycopg.connect(source_dsn, autocommit=True, connect_timeout=5) as source:
            source.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            teacher = source.execute("SELECT email FROM teachers WHERE id=%s", (fixture["teacher"]["id"],)).fetchone()
            offering = source.execute("SELECT class_id,teacher_id FROM class_offerings WHERE id=%s", (fixture["classOfferingId"],)).fetchone()
            require(teacher and teacher[0] == fixture["teacher"]["email"] and teacher[0].endswith("@example.invalid")
                    and tuple(offering or ()) == (fixture["classId"], fixture["teacher"]["id"]), "Synthetic source ownership mismatch")
            synthetic = source.execute("""SELECT COUNT(*) FROM students WHERE student_id_number LIKE 'CAPACITY-SYNTHETIC-%%'
                AND class_id=%s AND description='owned synthetic capacity fixture'""", (fixture["classId"],)).fetchone()[0]
            require(synthetic >= 200, "The source is not the seeded owned 200-user capacity fixture")
            exported = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            source_facts = snapshot(source)
            binary = shutil.which("pg_dump")
            require(binary, "Native pg_dump is unavailable")
            dumped = subprocess.run([binary, "--dbname", source_dsn, "--format=custom", "--no-owner", "--no-acl", "--no-password",
                                     "--snapshot", exported, "--file", str(dump)], env=native_environment(),
                                    capture_output=True, text=True, encoding="utf-8", timeout=120)
            (output / "dump.log").write_text(dumped.stdout+dumped.stderr, encoding="utf-8")
            dumped.check_returncode()
            source.execute("ROLLBACK")
        report["dump_sha256"] = hashlib.sha256(dump.read_bytes()).hexdigest()
        with psycopg.connect(source_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(clone)))
            created = True
            admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(clone), sql.Literal("synthetic-capacity-migration:" + token)))
        binary = shutil.which("pg_restore")
        require(binary, "Native pg_restore is unavailable")
        process = subprocess.run([binary, "--dbname", clone_dsn, "--no-owner", "--no-acl", "--no-password", "--exit-on-error", str(dump)],
                                 env=native_environment(), capture_output=True, text=True, encoding="utf-8", timeout=120)
        (output / "restore.log").write_text(process.stdout+process.stderr, encoding="utf-8")
        process.check_returncode()
        with psycopg.connect(clone_dsn, connect_timeout=5) as restored:
            assert_clone_identity(restored, clone, token)
            require(snapshot(restored) == source_facts, "Restored clone does not match source snapshot")
        os.environ.update(DATABASE_URL=clone_dsn, POSTGRES_POOL_MAX="4", POSTGRES_APPLICATION_NAME="lanshare-scheduler")
        asyncio.run(measure_projection(runtime, fixture, report))
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=type(exc).__name__, detail=str(exc)[:300])
    finally:
        if "classroom_app.db.postgres" in sys.modules:
            sys.modules["classroom_app.db.postgres"].close_connection_pool()
        if created:
            with psycopg.connect(source_dsn, autocommit=True, connect_timeout=5) as admin:
                assert_clone_identity(admin, clone, token)
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(clone)))
            report["owned_clone_removed"] = True
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key not in {"events", "batches", "final_generations", "commit_seconds_by_student"}}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--cluster-dir", type=Path, required=True)
    parser.add_argument("--pg-port", type=int, default=18766)
    parser.add_argument("--http-port", type=int, default=18767)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
