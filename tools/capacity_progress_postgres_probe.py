"""Exercise progress publication races in an explicitly owned local PG cluster.

The generated schema contains synthetic rows only. Both the database name and
the server's data_directory must match this task's disposable cluster.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from datetime import datetime, timedelta
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def probe_projection_timer_recovery(connect, source_column_types):
    from classroom_app.services import learning_progress_service as progress
    from classroom_app.services import scheduled_task_service as scheduler

    now = datetime.now().replace(microsecond=0)
    old = (now-timedelta(minutes=5)).isoformat()
    with connect() as conn:
        actual = {row["column_name"]: row["data_type"] for row in conn.execute(
            "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND table_name='scheduled_tasks' AND column_name IN ('finished_at','updated_at','locked_at','run_at')"
        ).fetchall()}
        assert actual == source_column_types, "Owned fixture must exercise the real source timestamp column types"
        task_id = progress.ensure_cultivation_snapshot_refresh_task(conn)
        unrelated = scheduler.schedule_task(conn, task_kind="probe_unrelated", run_at=old, dedupe_key="probe:unrelated-failed")
        conn.execute("UPDATE scheduled_tasks SET status='failed',attempt_count=3,last_error='synthetic outage',finished_at=?,updated_at=? WHERE id IN (?,?)",
                     (old, old, task_id, unrelated))
        progress.ensure_cultivation_snapshot_refresh_task(conn)
        task = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
        other = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (unrelated,)).fetchone())
        assert (task["status"], task["attempt_count"], task["last_error"]) == ("pending", 0, "synthetic outage")
        assert (other["status"], other["attempt_count"]) == ("failed", 3)
        for status in ("failed", "cancelled", "running"):
            timestamp = now.isoformat()
            conn.execute("UPDATE scheduled_tasks SET status=?,attempt_count=3,finished_at=?,updated_at=?,locked_at=?,locked_by='unchanged-owner' WHERE id=?",
                         (status, timestamp, timestamp, timestamp, task_id))
            before = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
            progress.ensure_cultivation_snapshot_refresh_task(conn)
            after = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())
            assert all(before[field] == after[field] for field in
                       ("status", "attempt_count", "finished_at", "updated_at", "locked_at", "locked_by", "started_at"))
        conn.execute("UPDATE scheduled_tasks SET status='failed',finished_at=NULL,updated_at=?,locked_at=NULL,locked_by='' WHERE id=?", (old, task_id))
        progress.ensure_cultivation_snapshot_refresh_task(conn)
        assert conn.execute("SELECT status FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "pending"

    # Another session changes the state after the initial dedupe read but before
    # the rearm UPDATE. The native WHERE/CAS must preserve that new owner/cancel.
    for replacement in ("cancelled", "running"):
        with connect() as conn:
            conn.execute("UPDATE scheduled_tasks SET status='failed',finished_at=?,updated_at=?,locked_at=NULL,locked_by='' WHERE id=?", (old, old, task_id))
        class CompetingChange:
            def __init__(self, conn):
                self.conn, self.triggered = conn, False
            def execute(self, statement, params=()):
                if not self.triggered and " ".join(statement.split()).startswith("UPDATE scheduled_tasks SET status='pending'"):
                    self.triggered = True
                    with connect() as competing:
                        competing.execute("UPDATE scheduled_tasks SET status=?,locked_at=?,locked_by='new-owner',updated_at=? WHERE id=?",
                                          (replacement, now.isoformat(), now.isoformat(), task_id))
                return self.conn.execute(statement, params)
        with connect() as conn:
            racing = CompetingChange(conn)
            progress.ensure_cultivation_snapshot_refresh_task(racing)
            assert racing.triggered
            row = conn.execute("SELECT status,locked_at,locked_by FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
            assert (row["status"], row["locked_at"], row["locked_by"]) == (replacement, now.isoformat(), "new-owner")
    return {"timestamp_column_types_match_source": source_column_types, "historical_failed_rearmed": True,
            "recent_failed_cooldown_preserved": True, "null_finished_at_falls_back_to_updated_at": True,
            "cancelled_and_running_claim_unchanged": True, "other_failed_not_replayed": True,
            "concurrent_cancel_and_claim_fenced": True}


def probe_scheduler(connect, stack, source_column_types):
    from classroom_app.services import scheduled_task_service as scheduler
    from classroom_app.db import schema_scheduler
    stack.enter_context(patch.object(scheduler, "get_db_connection", connect))
    stack.enter_context(patch.object(schema_scheduler, "_SCHEMA_READY", False))
    started, release = threading.Event(), threading.Event()
    def slow(_task):
        started.set()
        if not release.wait(5):
            raise RuntimeError("probe did not release handler")
        return "completed"
    stack.enter_context(patch.dict(scheduler._HANDLERS, {
        "probe_elastic": slow, "todo_due_reminder": lambda task: "deadline complete",
    }))
    with connect() as conn:
        scheduler.ensure_scheduler_schema(conn)
        slow_id = scheduler.schedule_task(conn, task_kind="probe_elastic", priority=120, run_at=datetime.now()-timedelta(seconds=10))
        scheduler.schedule_task(conn, task_kind="probe_elastic", priority=120, run_at=datetime.now()-timedelta(seconds=10))
        scheduler.schedule_task(conn, task_kind="todo_due_reminder", priority=100, run_at=datetime.now()-timedelta(seconds=10))
    async def check():
        operation = asyncio.create_task(scheduler.process_due_scheduled_tasks_once(limit=1, lane="elastic"))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            with connect() as conn:
                claim = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (slow_id,)).fetchone())
            deadline = await scheduler.process_due_scheduled_tasks_once(limit=1, lane="deadline")
            assert deadline["done"] == 1 and not operation.done()
            with patch.object(scheduler, "_now", return_value=datetime.now()+timedelta(minutes=2)):
                await asyncio.sleep(0.1)
                competing = await asyncio.to_thread(scheduler._claim_due_tasks, 1, lane="elastic")
                # The other worker receives the still-pending task, never the
                # actively heartbeating task whose original claim is old.
                assert len(competing) == 1 and competing[0]["id"] != slow_id
                with connect() as conn:
                    renewed = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (slow_id,)).fetchone())
                assert renewed["locked_at"] == claim["locked_at"] and renewed["updated_at"] > claim["updated_at"]
                operation.cancel()
                await asyncio.sleep(0.06)
                assert not operation.done()
                release.set()
                try:
                    await operation
                except asyncio.CancelledError:
                    pass
        finally:
            release.set()
    with patch.object(scheduler, "SCHEDULER_STALE_MINUTES", 1), patch.object(scheduler, "SCHEDULER_LEASE_HEARTBEAT_SECONDS", 0.02):
        asyncio.run(check())
    with connect() as conn:
        row = conn.execute("SELECT status,last_result FROM scheduled_tasks WHERE id=?", (slow_id,)).fetchone()
        assert row["status"] == "done" and row["last_result"] == "completed"
    recovery = probe_projection_timer_recovery(connect, source_column_types)
    return {"deadline_during_slow_sync_handler": True, "long_handler_renewed_not_reclaimed": True,
            "cancellation_drains_live_thread": True, "immutable_ownership_token": True,
            "projection_timer_recovery": recovery}


def run(args):
    from tools.isolated_environment import guard_dotenv_loading, isolate_sqlite_environment
    import psycopg
    from psycopg import sql

    cluster = Path(args.cluster_root).resolve()
    if cluster != (ROOT / ".codex-temp/platform-capacity-20261105-pg").resolve():
        raise ValueError("This probe requires its task-owned disposable PG cluster")
    dsn = f"postgresql://rehearsal_admin@127.0.0.1:{args.port}/lanshare_assessment_rehearsal_capacity"
    schema = "capacity_progress_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        actual, listener, port = admin.execute(
            "SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port')"
        ).fetchone()
        if Path(actual).resolve() != cluster or listener != "127.0.0.1" or int(port) != args.port or args.port == 5432:
            raise ValueError("Connected PostgreSQL does not own the expected disposable directory")
        source_column_types = dict(admin.execute(
            "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='scheduled_tasks' AND column_name IN ('finished_at','updated_at','locked_at','run_at')"
        ).fetchall())
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with tempfile.TemporaryDirectory(prefix="capacity-progress-") as temporary, ExitStack() as stack:
            isolate_sqlite_environment(Path(temporary))
            guard_dotenv_loading(stack)
            os.environ.update(DB_ENGINE="postgres", DATABASE_URL=dsn, POSTGRES_BACKEND_READY="true", POSTGRES_POOL_ENABLED="false")
            from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
            from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
            from classroom_app.services import learning_progress_service as service
            from tests.test_learning_progress_snapshots import _metrics

            def connect():
                raw = psycopg.connect(dsn, connect_timeout=5, row_factory=sqlite_compatible_dict_row,
                    options=f"-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=3000",
                    application_name=schema)
                return LanSharePostgresConnection(raw)

            stack.enter_context(patch.object(service, "get_db_connection", connect))
            with connect() as conn:
                ensure_cultivation_progress_schema(conn, engine="postgres")
                conn.execute("CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT)")
                conn.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT)")
                conn.execute("CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT,sect_name TEXT,description TEXT,credits INTEGER)")
                conn.execute("CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER,teacher_id INTEGER,course_id INTEGER)")
                conn.execute("CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER,name TEXT,student_id_number TEXT,enrollment_status TEXT DEFAULT 'active')")
                conn.execute("CREATE TABLE class_offering_class_links(id INTEGER PRIMARY KEY,offering_id INTEGER,class_id INTEGER,is_primary INTEGER)")
                conn.execute("CREATE TABLE cultivation_display_settings(student_id INTEGER PRIMARY KEY,hide_cultivation INTEGER,updated_at TEXT)")
                conn.execute("CREATE TABLE learning_certificates(id INTEGER PRIMARY KEY,class_offering_id INTEGER,student_id INTEGER,stage_key TEXT,level_key TEXT,level_name TEXT,tier INTEGER,title TEXT,certificate_code TEXT,issued_at TEXT,revealed_at TEXT,metadata_json TEXT)")
                conn.execute("CREATE TABLE learning_stage_exam_attempts(id INTEGER PRIMARY KEY,class_offering_id INTEGER,student_id INTEGER,stage_key TEXT,status TEXT,generated_at TEXT)")
                conn.execute("CREATE TABLE learning_stage_status(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,class_offering_id INTEGER,student_id INTEGER,stage_key TEXT,status TEXT,progress_score DOUBLE PRECISION,readiness_score DOUBLE PRECISION,unlocked_at TEXT,passed_at TEXT,last_exam_assignment_id INTEGER,certificate_id INTEGER,last_calculated_at TEXT,metadata_json TEXT,UNIQUE(class_offering_id,student_id,stage_key))")
                conn.execute("INSERT INTO classes VALUES(1,'Synthetic class')")
                conn.execute("INSERT INTO classes VALUES(2,'Synthetic linked class')")
                conn.execute("INSERT INTO teachers VALUES(1,'Synthetic teacher')")
                conn.execute("INSERT INTO courses VALUES(1,'Synthetic course','Synthetic sect','',1)")
                conn.execute("INSERT INTO class_offerings VALUES(1,1,1,1)")
                for student in range(1, 4):
                    conn.execute("INSERT INTO students(id,class_id,name,student_id_number) VALUES(?,1,?,?)", (student, f"Student {student}", f"S{student}"))
                conn.execute("INSERT INTO students(id,class_id,name,student_id_number) VALUES(4,2,'Linked student','S4')")
                conn.execute("INSERT INTO students VALUES(999,1,'Inactive student','S999','inactive')")
                conn.execute("INSERT INTO class_offering_class_links VALUES(1,1,1,1)")
                conn.execute("INSERT INTO class_offering_class_links VALUES(2,1,2,0)")
                conn.execute("INSERT INTO cultivation_display_settings VALUES(2,1,'2026-11-05')")
            # First visit is read-only, and a periodic discovery cycle closes
            # the loop even when no behavior event has yet marked this student.
            with connect() as conn:
                missing = service.get_student_learning_state(conn, 1, 1)
                assert missing["score"] is None and missing["snapshot_status"] == "building"
            with patch.object(service, "_build_learning_metrics", return_value=_metrics(42, task=42)):
                seeded = service.refresh_pending_learning_snapshots(limit=20, time_budget_seconds=10)
            assert seeded["seeded"] == 4 and seeded["refreshed"] == 4, seeded
            with connect() as conn:
                assert service.get_student_learning_state(conn, 1, 4)["score"] == 42
                assert conn.execute("SELECT COUNT(*) AS n FROM learning_progress_snapshots WHERE student_id=999").fetchone()["n"] == 0

            def counts(conn):
                return tuple(conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
                    for name in ("learning_progress_snapshots", "learning_stage_status", "cultivation_score_events"))

            with connect() as conn:
                before = counts(conn)
                service.mark_student_learning_progress_dirty(conn, 1, 1)
                service.mark_student_learning_progress_dirty(conn, 1, 2)
            # Force both sessions to hold their own stage row, the acquisition
            # pattern that previously deadlocked when rank reads wrote peers.
            barrier = threading.Barrier(2)
            def read_with_stage_lock(student):
                with connect() as conn:
                    conn.execute("UPDATE learning_stage_status SET last_calculated_at=last_calculated_at WHERE class_offering_id=1 AND student_id=?", (student,))
                    barrier.wait(timeout=5)
                    profile = service.build_student_global_cultivation_profile(conn, student, include_rank=True)
                    return profile["score"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(read_with_stage_lock, (1, 2))) == [42, 42]
            with connect() as conn:
                assert counts(conn) == before
            with connect() as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                assert service.build_student_global_cultivation_profile(conn, 1, include_rank=True)["score"] == 42

            # A new generation commits after stage/event writes, before CAS.
            # Every old projection side effect must roll back, not the marker.
            with connect() as conn:
                version = conn.execute("SELECT dirty_version FROM learning_progress_snapshots WHERE student_id=1").fetchone()[0]
                prior_events = counts(conn)[2]
            recorded = service._record_cultivation_score_events
            arrived = threading.Event()
            def add_late_event(*a, **kw):
                recorded(*a, **kw)
                with connect() as other:
                    service.mark_student_learning_progress_dirty(other, 1, 1, source_ref="late:committed")
                arrived.set()
            with patch.object(service, "_record_cultivation_score_events", side_effect=add_late_event), connect() as conn:
                _, published = service._publish_learning_metrics(conn, 1, 1, _metrics(70, task=70), expected_version=version, event_source_ref="old")
                assert not published and arrived.is_set()
            with connect() as conn:
                row = conn.execute("SELECT score,dirty,dirty_version FROM learning_progress_snapshots WHERE student_id=1").fetchone()
                assert row["score"] == 42 and row["dirty"] == 1 and row["dirty_version"] == version + 1
                assert counts(conn)[2] == prior_events
                assert conn.execute("SELECT MAX(progress_score) AS score FROM learning_stage_status WHERE student_id=1").fetchone()["score"] == 42

            # A correction decreases the score and appends precisely one delta.
            with patch.object(service, "_build_learning_metrics", return_value=_metrics(25, task=25)):
                service.refresh_pending_learning_snapshots(limit=10, time_budget_seconds=10)
            with connect() as conn:
                corrected = service.get_student_learning_state(conn, 1, 1)
                assert corrected["score"] == 25 and corrected["snapshot_status"] == "ready"
                rank = service.build_student_class_position(conn, 1, 1, current_score=25)
                assert rank and rank["hidden_count"] == 1 and len(rank["mountain"]["points"]) == 3
                assert all(item["name"] != "Student 2" for item in rank["ranking"])

            # Publication admission is nonblocking across real sessions.
            key = int.from_bytes(__import__("hashlib").sha256(b"cultivation:1:1").digest()[:8], "big", signed=True)
            with connect() as owner:
                owner.execute("SELECT pg_advisory_xact_lock(?)", (key,))
                with connect() as contender:
                    _, published = service._publish_learning_metrics(contender, 1, 1, _metrics(99, task=99), expected_version=version + 1, event_source_ref="contender")
                    assert not published

            # A grading transaction already owns a later stage row while a
            # concurrent refresh owns publication admission. It must be able
            # to defer its projection and commit, releasing the stage lock.
            domain_locked, publisher_started = threading.Event(), threading.Event()
            original_state = service._build_learning_state_from_metrics
            def state_with_barrier(*a, **kw):
                if kw.get("persist_stage_status"):
                    publisher_started.set()
                return original_state(*a, **kw)
            def domain_first():
                with connect() as conn:
                    conn.execute("UPDATE learning_stage_status SET last_calculated_at=last_calculated_at WHERE student_id=1 AND stage_key=?", (service.LEARNING_LEVELS[-1]["key"],))
                    domain_locked.set()
                    assert publisher_started.wait(5)
                    return service.refresh_student_learning_state(conn, 1, 1)["score"]
            def publication_first():
                assert domain_locked.wait(5)
                with connect() as conn:
                    return service.refresh_student_learning_state(conn, 1, 1)["score"]
            with patch.object(service, "_build_learning_metrics", return_value=_metrics(33, task=33)), patch.object(service, "_build_learning_state_from_metrics", side_effect=state_with_barrier):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    domain_result = pool.submit(domain_first)
                    publication_result = pool.submit(publication_first)
                    assert domain_result.result(timeout=10) == 33 and publication_result.result(timeout=10) == 33
            # A large first-use population cannot consume the dirty-drain share.
            with connect() as conn:
                for student in range(5, 105):
                    conn.execute("INSERT INTO students(id,class_id,name,student_id_number) VALUES(?,1,?,?)", (student, f"Student {student}", f"S{student}"))
                service.mark_student_learning_progress_dirty(conn, 1, 1)
            with patch.object(service, "_build_learning_metrics", return_value=_metrics(30, task=30)):
                mixed = service.refresh_pending_learning_snapshots(limit=8, time_budget_seconds=3)
            assert mixed["seeded"] <= 2 and mixed["refreshed"] > 0, mixed
            with connect() as conn:
                assert service.get_student_learning_state(conn, 1, 1)["score"] == 30
            scheduler_report = probe_scheduler(connect, stack, source_column_types)
            return {"schema": schema, "initial_discovery": seeded, "concurrent_reads_without_deadlock": 2,
                "read_side_projection_writes": 0, "late_generation_preserved": True,
                "stale_projection_rolled_back": True, "score_correction": 25,
                "hidden_peer_excluded": True, "publication_admission_nonblocking": True,
                "stage_first_domain_and_projection_without_deadlock": True,
                "bounded_first_use_discovery": mixed, "scheduler": scheduler_report}
    finally:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-root", required=True)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args)
    report["schema_removed"] = True
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
