"""Synthetic scheduler pressure for the native capacity fixture.

Run seed, then worker beside platform_capacity_probe.py. The elastic handler is
a controlled provider-wait stand-in. It does not grade work or award anything.
All real reminders and progress publication use the application services.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ELASTIC_KIND = "capacity_controlled_provider_wait"
MANIFEST = "background-fixture.json"
EVENTS = "background-events.jsonl"


@contextmanager
def _committed_projection_recorder(connection_type, record):
    """Observe only confirmed owner commits inside this synthetic worker process."""
    pending = {}
    pending_lock = threading.Lock()
    measurement_errors = []
    original_commit, original_rollback, original_close = connection_type.commit, connection_type.rollback, connection_type.close

    def take(conn):
        with pending_lock:
            return pending.pop(conn, [])

    def staged(conn, **values):
        with pending_lock:
            pending.setdefault(conn, []).append(values)

    staged.measurement_errors = measurement_errors

    def measurement_error(phase, exc):
        error = {"phase": phase, "error_type": type(exc).__name__, "at": datetime.now().isoformat()}
        with pending_lock:
            measurement_errors.append(error)
        try:
            record("projection_measurement_error", **error)
        except Exception:
            pass  # Persisted with final worker state even if the event file failed.
        print(f"[CAPACITY] projection measurement failed ({phase}): {type(exc).__name__}")

    def committed(conn):
        values = take(conn)
        # A PostgreSQL commit called in INERROR can return after ROLLBACK. It
        # must not turn a staged publication into a confirmed measurement.
        raw = getattr(conn, "raw_connection", None)
        before = getattr(getattr(raw, "info", None), "transaction_status", None)
        result = original_commit(conn)  # exception/unknown outcome emits nothing
        after = getattr(getattr(raw, "info", None), "transaction_status", None)
        if before == 2 and after == 0 and values:  # psycopg INTRANS -> IDLE
            # A caller can roll a published savepoint back and still commit
            # its outer transaction. Confirm durable generation visibility;
            # record only the later observation time, never the staged time.
            confirmed = []
            try:
                for item in values:
                    row = conn.execute(
                        "SELECT calculated_version FROM learning_progress_snapshots "
                        "WHERE class_offering_id=? AND student_id=?",
                        (item["class_offering_id"], item["student_id"]),
                    ).fetchone()
                    if row and int(row["calculated_version"]) >= int(item["version"]):
                        confirmed.append(item)
            except Exception as exc:
                measurement_error("durable_generation_read", exc)
                return result
            for item in confirmed:
                try:
                    record("projection_published", **item)
                except Exception as exc:
                    # Measurement I/O after a real commit must not make the
                    # business handler retry an already committed generation.
                    measurement_error("event_write", exc)
        return result

    def rolled_back(conn):
        take(conn)
        return original_rollback(conn)

    def closed(conn):
        take(conn)
        return original_close(conn)

    with patch.object(connection_type, "commit", committed), patch.object(connection_type, "rollback", rolled_back), patch.object(connection_type, "close", closed):
        yield staged


def validate_fixture(args, runtime, fixture):
    """Called only after the shared cluster/socket/dotenv boundary is installed."""
    expected = f"postgresql://rehearsal_admin@127.0.0.1:{args.pg_port}/lanshare_assessment_rehearsal_capacity"
    if (fixture.get("uiV3Synthetic") is not True or os.environ.get("DATABASE_URL") != expected
            or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"
            or not runtime.resolve().is_relative_to((ROOT / ".codex-temp").resolve())):
        raise RuntimeError("Background pressure requires the explicit synthetic boundary")
    from classroom_app.database import get_db_connection
    with get_db_connection() as conn:
        offering = conn.execute("SELECT * FROM class_offerings WHERE id=?", (fixture["classOfferingId"],)).fetchone()
        teacher = conn.execute("SELECT email FROM teachers WHERE id=?", (fixture["teacher"]["id"],)).fetchone()
        if (not offering or int(offering["teacher_id"]) != int(fixture["teacher"]["id"])
                or int(offering["class_id"]) != int(fixture["classId"])
                or not teacher or teacher["email"] != fixture["teacher"]["email"]
                or not teacher["email"].endswith("@example.invalid")):
            raise RuntimeError("Background fixture ownership mismatch")
        students = conn.execute("SELECT id,student_id_number,class_id,description FROM students "
                                "WHERE student_id_number LIKE 'CAPACITY-SYNTHETIC-%' ORDER BY student_id_number").fetchall()
    selected = [int(row["id"]) for row in students
                if int(row["class_id"]) == int(fixture["classId"])
                and row["description"] == "owned synthetic capacity fixture"]
    if len(selected) < args.users:
        raise RuntimeError("Seed the required capacity identities before background work")
    return dict(offering), selected[:args.users]


def seed_background(args, runtime: Path, fixture: dict) -> dict:
    offering, students = validate_fixture(args, runtime, fixture)
    return _seed_validated_background(args, runtime, offering, students)


def _seed_validated_background(args, runtime: Path, offering: dict, students: list[int]) -> dict:
    """Shared workload core; callers must establish their own strict boundary."""
    from classroom_app.database import get_db_connection
    from classroom_app.db.connection import execute_insert_returning_id
    from classroom_app.services import scheduled_task_service as scheduler
    from classroom_app.services.learning_progress_service import ensure_cultivation_snapshot_refresh_task
    deadline_every = int(getattr(args, "deadline_every", 30))
    elastic_every = int(getattr(args, "elastic_every", 3))
    dirty_every = int(getattr(args, "dirty_every", 5))
    if not 10 <= args.duration <= 10800 or min(deadline_every, elastic_every, dirty_every) < 1:
        raise ValueError("Bounded background duration and intervals required")
    if (runtime / MANIFEST).exists() and not getattr(args, "replace", False):
        raise ValueError("Background manifest exists. Pass --replace after stopping its worker")
    if (runtime / "background-worker-state.json").exists() and not (runtime / "stop-native-background").exists():
        state = json.loads((runtime / "background-worker-state.json").read_text(encoding="utf-8"))
        if state.get("status") == "running":
            raise ValueError("Stop the previous background worker before replacing its work")
    run_id = uuid.uuid4().hex
    prefix = f"capacity-background:{run_id}:"
    now = datetime.now().replace(microsecond=0)
    records = []
    with get_db_connection() as conn:
        if (runtime / MANIFEST).exists():
            previous = json.loads((runtime / MANIFEST).read_text(encoding="utf-8"))
            old_prefix = f"capacity-background:{previous['run_id']}:"
            conn.execute("UPDATE scheduled_tasks SET status='cancelled' WHERE dedupe_key LIKE ? AND status='pending'", (old_prefix + "%",))
        progress_id = ensure_cultivation_snapshot_refresh_task(conn)
        for index, offset in enumerate(range(5, args.duration, deadline_every)):
            student = students[index % len(students)]
            due = (now + timedelta(seconds=offset + 600)).isoformat()
            todo = execute_insert_returning_id(conn, """INSERT INTO classroom_todos
                (class_offering_id,owner_role,owner_user_pk,title,notes,due_at,metadata_json,created_at,updated_at)
                VALUES(?,'student',?,'合成容量到期提醒','owned synthetic background fixture',?,? ,?,?)""",
                (offering["id"], student, due, json.dumps({"reminder": {"enabled": True, "email_enabled": False}}), now.isoformat(), now.isoformat()))
            task_id = scheduler.schedule_task(conn, task_kind="todo_due_reminder", run_at=now+timedelta(seconds=offset),
                payload={"todo_id": todo, "class_offering_id": offering["id"], "owner_role": "student", "owner_user_pk": student},
                dedupe_key=prefix+f"todo:{index}", priority=50, max_attempts=3)
            records.append({"id": task_id, "kind": "todo_due_reminder", "offset": offset,
                            "notification_ref": f"todo-due:{todo}:{due}", "expected_recipient_ids": [student]})
        for index, offset in enumerate(sorted({5, max(5, args.duration//3), max(5, args.duration*2//3)})):
            due = (now + timedelta(seconds=args.duration+7200)).isoformat()
            assignment = execute_insert_returning_id(conn, """INSERT INTO assignments
                (course_id,class_offering_id,title,status,availability_mode,auto_close,due_at,created_at)
                VALUES(?,?,'合成容量作业提醒','published','deadline',1,?,?)""",
                (offering["course_id"], offering["id"], due, now.isoformat()))
            task_id = scheduler.schedule_task(conn, task_kind="assignment_due_reminder", run_at=now+timedelta(seconds=offset),
                payload={"assignment_id": assignment, "window": "2h", "due_at": due},
                dedupe_key=prefix+f"assignment:{index}", priority=40, max_attempts=3)
            records.append({"id": task_id, "kind": "assignment_due_reminder", "offset": offset,
                            "notification_ref": f"{assignment}:2h", "expected_recipient_ids": students})
        for index, offset in enumerate([0, 0, 0, *range(0, args.duration, elastic_every)]):
            task_id = scheduler.schedule_task(conn, task_kind=ELASTIC_KIND, run_at=now+timedelta(seconds=offset),
                payload={"seconds": 1 + index % 3, "synthetic_provider_wait": True},
                dedupe_key=prefix+f"elastic:{index}", priority=120, max_attempts=1)
            records.append({"id": task_id, "kind": ELASTIC_KIND, "offset": offset})
        # Use the real handler and record whether the fixture's autonomous
        # runtime is enabled. A skipped tick is never counted as AI activity.
        wanderer_id = scheduler.schedule_task(conn, task_kind="wanderer_tick", run_at=now+timedelta(seconds=15),
            recurrence_seconds=60, payload={}, dedupe_key=prefix+"wanderer", priority=120, max_attempts=2)
        records.append({"id": wanderer_id, "kind": "wanderer_tick", "offset": 15})
    manifest = {"run_id": run_id, "fixture_runtime": str(runtime.resolve()), "class_offering_id": int(offering["id"]),
        "student_ids": students, "started_at": now.isoformat(), "duration": args.duration,
        "progress_task_id": progress_id, "dirty_every_seconds": dirty_every,
        "scheduled": records, "elastic_semantics": "controlled 1-3 second supplier wait, no AI grading or certificate effects"}
    (runtime / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (runtime / EVENTS).write_text("", encoding="utf-8")
    return {"run_id": run_id, "scheduled_tasks": dict(Counter(item["kind"] for item in records)), "manifest": str(runtime / MANIFEST)}


async def run_background_worker(args, runtime: Path, fixture: dict):
    validate_fixture(args, runtime, fixture)
    from classroom_app.database import get_db_connection
    from classroom_app.services import scheduled_task_handlers  # noqa: F401 - real registrations
    from classroom_app.services import scheduled_task_service as scheduler
    from classroom_app.services import learning_progress_service as progress
    manifest = json.loads((runtime / MANIFEST).read_text(encoding="utf-8"))
    if manifest["fixture_runtime"] != str(runtime.resolve()):
        raise RuntimeError("Background manifest belongs to another fixture")
    stop = runtime / "stop-native-background"
    if stop.exists():
        raise ValueError("Remove the previous owned stop marker explicitly before launching")
    lock = threading.Lock()
    def record(event, **values):
        item = {"event": event, "at": datetime.now().isoformat(), "run_id": manifest["run_id"], **values}
        with lock, (runtime / EVENTS).open("a", encoding="utf-8") as output:
            output.write(json.dumps(item, ensure_ascii=False)+"\n")
    dispatch = scheduler._dispatch_task
    publish = progress._publish_learning_metrics
    async def measured_dispatch(task):
        record("task_started", task_id=task["id"], kind=task["task_kind"], run_at=task["run_at"])
        try:
            result = await dispatch(task)
        except BaseException as exc:
            record("task_failed", task_id=task["id"], kind=task["task_kind"], error=type(exc).__name__)
            raise
        record("task_handler_finished", task_id=task["id"], kind=task["task_kind"], result=str(result)[:240])
        return result
    def measured_publish(conn, offering, student, metrics, **kwargs):
        result = publish(conn, offering, student, metrics, **kwargs)
        if result[1] and int(offering) == manifest["class_offering_id"]:
            staged_publication(conn, student_id=student, class_offering_id=offering, version=kwargs["expected_version"])
        return result
    async def controlled_wait(task):
        await asyncio.sleep(max(1, min(3, float(task["payload"]["seconds"]))))
        return "controlled supplier wait completed; no grading effects"
    scheduler.register_task_handler(ELASTIC_KIND, controlled_wait)

    def dirty_batch(index):
        # Capture admission start before the transaction. Publication may run
        # immediately after commit, before this producer appends its event;
        # the JSONL append time must not make an applied generation look pending.
        admitted_at = datetime.now().isoformat()
        students = manifest["student_ids"]
        selected = sorted({students[(index*10+i) % len(students)] for i in range(min(10, len(students)))})
        with get_db_connection() as conn:
            for student in selected:
                progress.mark_student_learning_progress_dirty(conn, manifest["class_offering_id"], student, source_ref="capacity:synthetic-background")
            placeholders = ",".join("?" for _ in selected)
            versions = conn.execute(f"SELECT student_id,dirty_version FROM learning_progress_snapshots WHERE class_offering_id=? AND student_id IN ({placeholders})",
                                    (manifest["class_offering_id"], *selected)).fetchall()
        for row in versions:
            record("projection_dirty", student_id=row["student_id"], version=row["dirty_version"], at=admitted_at)
    async def dirty_loop():
        index = 0
        until = time.monotonic() + manifest["duration"]
        while not stop.exists() and not (runtime / "stop-native-background-intake").exists() and time.monotonic() < until:
            await asyncio.to_thread(dirty_batch, index)
            index += 1
            await asyncio.sleep(manifest["dirty_every_seconds"])
        # Keep the lifecycle task alive while the scheduler drains prior marks.
        while not stop.exists():
            await asyncio.sleep(0.5)
    state = runtime / "background-worker-state.json"
    state.write_text(json.dumps({"status": "running", "pid": os.getpid(), "run_id": manifest["run_id"],
                                "started_at": datetime.now().isoformat(), "pool_max": 4}), encoding="utf-8")
    from classroom_app.db.postgres import LanSharePostgresConnection
    with ExitStack() as patches:
        staged_publication = patches.enter_context(_committed_projection_recorder(LanSharePostgresConnection, record))
        patches.enter_context(patch.object(scheduler, "_dispatch_task", measured_dispatch))
        patches.enter_context(patch.object(progress, "_publish_learning_metrics", measured_publish))
        # In-app reminders remain real, no email worker or outbound delivery.
        patches.enter_context(patch("classroom_app.services.message_center_service.queue_notification_email_if_applicable", return_value=False))
        tasks = [asyncio.create_task(scheduler.run_scheduler_worker_forever(worker_id="capacity-synthetic-background", poll_seconds=5)),
                 asyncio.create_task(dirty_loop())]
        try:
            while not stop.exists():
                if any(task.done() for task in tasks):
                    for task in tasks:
                        if task.done():
                            task.result()
                    raise RuntimeError("Background worker stopped unexpectedly")
                await asyncio.sleep(0.5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            state.write_text(json.dumps({"status": "stopped", "pid": os.getpid(), "run_id": manifest["run_id"],
                                        "stopped_at": datetime.now().isoformat(),
                "projection_measurement_errors": staged_publication.measurement_errors}), encoding="utf-8")


def _projection_publication_delays(events):
    """Match committed generations regardless of cross-thread JSONL order."""
    published = {}
    for item in events:
        if item["event"] == "projection_published":
            published.setdefault(item["student_id"], []).append(item)
    projection_latencies = []
    pending_projection_marks = 0
    for item in events:
        if item["event"] != "projection_dirty":
            continue
        candidates = [p for p in published.get(item["student_id"], [])
                      if p["version"] >= item["version"] and p["at"] >= item["at"]]
        completion = min(candidates, key=lambda value: value["at"], default=None)
        if completion:
            projection_latencies.append((datetime.fromisoformat(completion["at"])-datetime.fromisoformat(item["at"])).total_seconds())
        else:
            pending_projection_marks += 1
    return projection_latencies, pending_projection_marks


def background_report(args, runtime: Path, fixture: dict) -> dict:
    validate_fixture(args, runtime, fixture)
    return _report_validated_background(runtime)


def _report_validated_background(runtime: Path) -> dict:
    """Shared durable-facts report after the caller verifies its fixture scope."""
    from classroom_app.database import get_db_connection
    manifest = json.loads((runtime / MANIFEST).read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (runtime / EVENTS).read_text(encoding="utf-8").splitlines() if line]
    events = [item for item in events if item["run_id"] == manifest["run_id"]]
    ids = [item["id"] for item in manifest["scheduled"]]
    with get_db_connection() as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT id,task_kind,status,run_at,last_result FROM scheduled_tasks WHERE id IN ({placeholders})", tuple(ids)).fetchall()
        # Event timestamps are commit-confirmed observations, not a substitute
        # for the durable generation facts at the final drained boundary.
        students = manifest["student_ids"]
        student_placeholders = ",".join("?" for _ in students)
        projection_rows = conn.execute(
            f"SELECT student_id,dirty,dirty_version,calculated_version FROM learning_progress_snapshots "
            f"WHERE class_offering_id=? AND student_id IN ({student_placeholders})",
            (manifest["class_offering_id"], *students),
        ).fetchall()
        projection_facts = {int(row["student_id"]): dict(row) for row in projection_rows}
        dirty_marks = [item for item in events if item["event"] == "projection_dirty"]
        unconfirmed_students = sorted({int(item["student_id"]) for item in dirty_marks
            if int(projection_facts.get(int(item["student_id"]), {}).get("calculated_version", -1)) < int(item["version"])})
        notification_checks = []
        for item in manifest["scheduled"]:
            if "notification_ref" not in item:
                continue
            category = "todo" if item["kind"] == "todo_due_reminder" else "assignment_due"
            recipients = conn.execute("SELECT recipient_user_pk FROM message_center_notifications WHERE ref_type=? AND ref_id=?",
                                      (category, item["notification_ref"])).fetchall()
            found = {int(row["recipient_user_pk"]) for row in recipients}
            completed = next(row["status"] for row in rows if row["id"] == item["id"]) == "done"
            expected = set(item["expected_recipient_ids"])
            notification_checks.append({"task_id": item["id"], "kind": item["kind"], "notifications": len(recipients),
                "expected_fixture_recipients": len(expected), "missing_fixture_recipients": len(expected-found), "task_completed": completed})
    def distribution(values):
        values = sorted(values)
        return {"count": len(values), "p95_seconds": round(values[min(len(values)-1, int(len(values)*.95))], 3),
                "max_seconds": round(values[-1], 3)} if values else {"count": 0}
    deadline_latencies = [max(0, (datetime.fromisoformat(item["at"])-datetime.fromisoformat(item["run_at"])).total_seconds())
        for item in events if item["event"] == "task_started" and item["kind"] in {"todo_due_reminder", "assignment_due_reminder"}]
    projection_latencies, pending_projection_marks = _projection_publication_delays(events)
    worker_state_path = runtime / "background-worker-state.json"
    worker_state = json.loads(worker_state_path.read_text(encoding="utf-8")) if worker_state_path.exists() else {}
    measurement_errors = worker_state.get("projection_measurement_errors",
        [item for item in events if item["event"] == "projection_measurement_error"])
    report = {"run_id": manifest["run_id"], "task_states": dict(Counter(f"{row['task_kind']}:{row['status']}" for row in rows)),
        "scheduled_task_facts": [{"id": int(row["id"]), "kind": row["task_kind"], "status": row["status"]} for row in rows],
        "deadline_start_delay": distribution(deadline_latencies), "projection_publication_delay": distribution(projection_latencies),
        "pending_projection_marks": pending_projection_marks,
        "projection_durable_facts": {"expected_students": len(students), "rows_found": len(projection_rows),
            "dirty_students": sorted(int(row["student_id"]) for row in projection_rows if row["dirty"]),
            "unconfirmed_mark_students": unconfirmed_students,
            "all_rows_clean_and_current": len(projection_rows) == len(students) and all(
                not row["dirty"] and row["dirty_version"] == row["calculated_version"] for row in projection_rows),
            "generation_facts": sorted(projection_facts.values(), key=lambda row: row["student_id"])},
        "projection_measurement": "confirmed PostgreSQL owner commit; ambiguous commits excluded from event latency; final generations checked independently",
        "projection_measurement_errors": measurement_errors,
        "postgres_application_name": os.environ.get("POSTGRES_APPLICATION_NAME"),
        "notification_checks": notification_checks,
        "wanderer_handler_results": [item["result"] for item in events if item["event"] == "task_handler_finished" and item["kind"] == "wanderer_tick"],
        "observed_event_counts": dict(Counter(item["event"] for item in events)),
        "handler_failures": [{key: item[key] for key in ("task_id", "kind", "error")} for item in events if item["event"] == "task_failed"],
        "scope": "real scheduler lanes, real in-app reminders and progress; elastic sleep is a controlled supplier-wait stand-in; excludes actual AI grading/provider acceptance"}
    (runtime / "background-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def diagnose_background(args, runtime, fixture):
    validate_fixture(args, runtime, fixture)
    from classroom_app.services import scheduled_task_handlers  # noqa: F401
    from classroom_app.services import scheduled_task_service as scheduler
    from classroom_app.db.postgres import LanSharePostgresConnection
    failures, outcomes = [], []
    execute = LanSharePostgresConnection.execute
    def traced(conn, statement, params=None):
        try:
            return execute(conn, statement, params)
        except Exception as exc:
            failures.append({"error": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None),
                             "statement": " ".join(statement.split())[:1800]})
            raise
    with patch.object(LanSharePostgresConnection, "execute", traced):
        for kind in ("cultivation_alert_scan", "mp_deadline_reminder_scan"):
            try:
                result = scheduler._HANDLERS[kind]({"payload": {}})
                outcomes.append({"kind": kind, "result": result})
            except Exception as exc:
                outcomes.append({"kind": kind, "error": type(exc).__name__})
    report = {"outcomes": outcomes, "failed_statements": failures}
    (runtime / "background-diagnosis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "worker", "report", "diagnose"))
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--cluster-dir", type=Path, required=True)
    parser.add_argument("--pg-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, default=18767)
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--deadline-every", type=int, default=30)
    parser.add_argument("--elastic-every", type=int, default=3)
    parser.add_argument("--dirty-every", type=int, default=5)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.duration <= 10800 or min(args.deadline_every, args.elastic_every, args.dirty_every) < 1:
        raise ValueError("Bounded duration and positive pressure intervals required")
    from tools.platform_capacity_probe import establish_boundary
    runtime, fixture = establish_boundary(args)
    os.environ.update(POSTGRES_POOL_MAX="4", POSTGRES_APPLICATION_NAME="lanshare-scheduler",
                      MAIN_APP_INTERNAL_URL=f"http://127.0.0.1:{args.http_port}")
    try:
        if args.action == "seed":
            print(json.dumps(seed_background(args, runtime, fixture)))
        elif args.action == "worker":
            asyncio.run(run_background_worker(args, runtime, fixture))
        elif args.action == "report":
            print(json.dumps(background_report(args, runtime, fixture), ensure_ascii=False))
        else:
            print(json.dumps(diagnose_background(args, runtime, fixture), ensure_ascii=False))
    finally:
        if "classroom_app.db.postgres" in sys.modules:
            sys.modules["classroom_app.db.postgres"].close_connection_pool()


if __name__ == "__main__":
    main()
