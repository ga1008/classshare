"""Synthetic Linux lab pressure through the real dispatcher entry point.

Only the lab controller's verified container boundary permits application imports.
The controlled wait is not AI grading or provider acceptance. No arbitrary DSN,
native-cluster impersonation, alternate scheduler loop or outbound email is used.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import platform_capacity_background as background

MARKER = Path("/lab/lab-marker.json")
INTAKE_STOP = "stop-background-intake"
STATE = "background-worker-state.json"
DIRTY_RETRY_SQLSTATES = frozenset({"55P03", "40P01", "40001"})
DIRTY_MAX_ATTEMPTS = 5
# Admission deadline for retries, pool checkout and SQL. Already-started driver
# calls and rollback/close still finish normally; this is not a thread kill.
DIRTY_BUDGET_SECONDS = 10.0
DISPATCHER_READY_SECONDS = 60.0


class _RolledBackDirtyConflict(RuntimeError):
    def __init__(self, sqlstate):
        super().__init__("Synthetic dirty transaction rolled back: " + sqlstate)
        self.sqlstate = sqlstate


def _dirty_timeout(conn, deadline):
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms < 1:
        raise TimeoutError("Synthetic dirty batch exhausted its time budget")
    # Local only; retain an already stricter session setting. The single-row
    # production mark has one SQL statement, so check the budget before each.
    conn.execute("""SELECT set_config(name,
        LEAST(COALESCE(NULLIF(setting::integer, 0), ?),
              CASE WHEN name='lock_timeout' THEN 250 ELSE 1000 END, ?)::text || 'ms', true)
        FROM pg_settings WHERE name IN ('lock_timeout','statement_timeout')""",
        (remaining_ms, remaining_ms))


def _dirty_transaction(selected, offering, get_connection, mark_dirty, deadline, outcome):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Synthetic dirty batch exhausted its time budget")
    conn = get_connection(checkout_timeout=remaining)
    try:
        outcome["phase"] = "transaction"
        for student in selected:
            _dirty_timeout(conn, deadline)
            mark_dirty(conn, offering, student, source_ref="capacity:synthetic-background")
        _dirty_timeout(conn, deadline)
        placeholders = ",".join("?" for _ in selected)
        versions = conn.execute("SELECT student_id,dirty_version FROM learning_progress_snapshots "
            f"WHERE class_offering_id=? AND student_id IN ({placeholders})", (offering, *selected)).fetchall()
        _dirty_timeout(conn, deadline)
        outcome["phase"] = "commit"
        conn.commit()  # Own this boundary explicitly; no implicit second commit.
        outcome["phase"] = "committed"
        return versions
    except Exception as exc:
        # A transport error during commit can describe accepted work. Even a
        # successful cleanup rollback cannot make that outcome safe to retry.
        conn.rollback()  # If this fails, never wrap/retry the original conflict.
        outcome["rollback_confirmed"] = True
        code = str(getattr(exc, "sqlstate", "") or "")
        if code in DIRTY_RETRY_SQLSTATES:
            raise _RolledBackDirtyConflict(code) from exc
        raise
    finally:
        conn.close()


def _apply_dirty_batch(index, manifest, get_connection, mark_dirty, record):
    """Retry only a server-rejected, explicitly rolled-back whole transaction."""
    students = manifest["student_ids"]
    selected = tuple(sorted({students[(index * 10 + i) % len(students)] for i in range(10)}))
    admitted_at = datetime.now().isoformat()
    began = time.monotonic()
    deadline = began + DIRTY_BUDGET_SECONDS
    for attempt in range(1, DIRTY_MAX_ATTEMPTS + 1):
        outcome = {"phase": "acquire", "rollback_confirmed": False}
        try:
            versions = _dirty_transaction(selected, manifest["class_offering_id"], get_connection,
                                          mark_dirty, deadline, outcome)
        except _RolledBackDirtyConflict as exc:
            delay = min(.4, .05 * (2 ** (attempt - 1)))
            retry = attempt < DIRTY_MAX_ATTEMPTS and time.monotonic() + delay < deadline
            record("projection_dirty_conflict", batch_index=index, attempt=attempt, student_ids=selected,
                   sqlstate=exc.sqlstate, rollback_confirmed=True, will_retry=retry,
                   elapsed_seconds=time.monotonic() - began)
            if not retry:
                raise
            time.sleep(delay)
        except Exception as exc:
            record("projection_dirty_failed", batch_index=index, attempt=attempt, student_ids=selected,
                   error_type=type(exc).__name__, sqlstate=str(getattr(exc, "sqlstate", "") or ""),
                   commit_outcome_unknown=outcome["phase"] == "commit",
                   **outcome, elapsed_seconds=time.monotonic() - began)
            raise
        else:
            # Audit failure after confirmed commit is fatal, never a reason to
            # increment the same selected students' dirty generations again.
            record("projection_dirty_batch_committed", batch_index=index, attempt=attempt,
                   student_ids=selected, elapsed_seconds=time.monotonic() - began)
            for row in versions:
                record("projection_dirty", student_id=row["student_id"], version=row["dirty_version"],
                       at=admitted_at, batch_index=index)
            return


async def _wait_dispatcher_ready(ready, shutdown, stop, runtime):
    deadline = time.monotonic() + DISPATCHER_READY_SECONDS
    while not ready.is_set():
        if shutdown.is_set() or stop.get("requested") or (runtime / INTAKE_STOP).exists():
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Real dispatcher did not commit a running heartbeat")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=.05)
        except TimeoutError:
            pass


def _identity_binding(identity):
    """Bind artifacts to non-secret facts returned by the strict lab validator."""
    runtime = Path(identity["artifact_root"])
    fixture_bytes = (runtime / "server-fixture.json").read_bytes()
    fixture = json.loads(fixture_bytes)
    digest = hashlib.sha256(fixture_bytes).hexdigest()
    users = fixture.get("users", [])
    students = [int(user["student_id"]) for user in users]
    if (fixture != identity["fixture"] or fixture.get("kind") != "capacity-linux-server-fixture"
            or fixture.get("synthetic") is not True or fixture.get("uiV3Synthetic") is not True
            or fixture.get("lab_nonce") != identity["lab_nonce"]
            or digest != identity["fixture_sha256"] or len(students) != 200 or len(set(students)) != 200
            or not identity["compose_project"] or not identity["pg_system_identifier"]):
        raise RuntimeError("Linux background fixture identity mismatch")
    binding = {key: str(identity[key]) for key in (
        "lab_nonce", "compose_project", "pg_system_identifier", "fixture_sha256")}
    return runtime, fixture, students, {"boundary": "linux-compose-lab-v1", **binding}


def _read_bound_manifest(identity):
    runtime, fixture, students, binding = _identity_binding(identity)
    manifest = json.loads((runtime / background.MANIFEST).read_text(encoding="utf-8"))
    if (manifest.get("lab_identity") != binding
            or manifest.get("fixture_runtime") != str(runtime.resolve())
            or manifest.get("class_offering_id") != int(fixture["classOfferingId"])
            or manifest.get("student_ids") != students or not manifest.get("run_id")):
        raise RuntimeError("Linux background manifest belongs to another lab run")
    return runtime, manifest


def seed_lab_background(identity, *, duration=3600, deadline_every=30, elastic_every=3, dirty_every=5):
    """Controller calls this only after validate_container_boundary succeeds."""
    runtime, fixture, students, binding = _identity_binding(identity)
    from classroom_app.database import get_db_connection
    with get_db_connection() as conn:
        offering = conn.execute("SELECT * FROM class_offerings WHERE id=?", (fixture["classOfferingId"],)).fetchone()
        if (not offering or int(offering["teacher_id"]) != int(fixture["teacher"]["id"])
                or int(offering["class_id"]) != int(fixture["classId"])):
            raise RuntimeError("Linux background offering ownership mismatch")
    args = SimpleNamespace(duration=duration, deadline_every=deadline_every, elastic_every=elastic_every,
                           dirty_every=dirty_every, replace=False)
    summary = background._seed_validated_background(args, runtime, dict(offering), students)
    manifest_path = runtime / background.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(lab_identity=binding, intake_stop_marker=INTAKE_STOP,
                    dispatcher_entrypoint="scheduled_task_dispatcher.main")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_id": summary["run_id"], "scheduled_tasks": summary["scheduled_tasks"],
            "manifest_name": background.MANIFEST, "lab_identity": binding}


def report_lab_background(identity):
    """Controller-side DB verification, never a claim by the HTTP load client."""
    runtime, manifest = _read_bound_manifest(identity)
    report = background._report_validated_background(runtime)
    report.update(_background_integrity(manifest, report, json.loads((runtime / STATE).read_text(encoding="utf-8"))))
    report.update(lab_identity=manifest["lab_identity"],
                  dispatcher_entrypoint="scheduled_task_dispatcher.main",
                  verification_origin="verified Linux lab controller")
    (runtime / "background-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _background_integrity(manifest, report, state):
    """An absent controlled job must not disappear from an aggregate count."""
    finite = {"todo_due_reminder", "assignment_due_reminder", background.ELASTIC_KIND}
    expected = {int(item["id"]): item["kind"] for item in manifest["scheduled"] if item["kind"] in finite}
    facts = {int(item["id"]): item for item in report["scheduled_task_facts"]}
    if not expected or len(expected) != sum(item["kind"] in finite for item in manifest["scheduled"]):
        raise ValueError("Finite background manifest IDs must be nonempty and unique")
    missing = sorted(set(expected) - set(facts))
    unfinished = [facts[task_id] for task_id in sorted(expected.keys() & facts.keys()) if facts[task_id]["status"] != "done"]
    mismatched = sorted(task_id for task_id in expected.keys() & facts.keys() if facts[task_id]["kind"] != expected[task_id])
    return {"finite_task_verification": {"expected_count": len(expected), "missing_task_ids": missing,
                "unfinished_tasks": unfinished, "mismatched_task_kind_ids": mismatched,
                "all_present_and_done": not missing and not unfinished and not mismatched},
            "worker_identity_matches_run": state.get("run_id") == manifest["run_id"] == report.get("run_id")
                and state.get("lab_identity") == manifest["lab_identity"]}


async def _dirty_producer(manifest, runtime, shutdown, stop, dirty_batch):
    index = 0
    until = time.monotonic() + manifest["duration"]
    while (not shutdown.is_set() and not stop.get("requested")
           and not (runtime / INTAKE_STOP).exists() and time.monotonic() < until):
        # The owner wrapper never cancels this task. On shutdown an accepted
        # short transaction finishes and remains visible to final DB checks.
        await asyncio.to_thread(dirty_batch, index)
        index += 1
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=manifest["dirty_every_seconds"])
        except TimeoutError:
            pass
    await shutdown.wait()


async def _run_with_producer(original_run, args, stop, producer):
    """Add pressure while the real dispatcher owns both production workers."""
    shutdown = asyncio.Event()
    dirty = asyncio.create_task(producer(shutdown, stop))
    runner = asyncio.create_task(original_run(args, stop=stop))
    try:
        done, _ = await asyncio.wait((runner, dirty), return_when=asyncio.FIRST_COMPLETED)
        if dirty in done:
            dirty.result()
            raise RuntimeError("Synthetic dirty producer exited before dispatcher shutdown")
        return runner.result()
    finally:
        shutdown.set()
        if not runner.done() and not runner.cancelling():
            runner.cancel()
        results = await asyncio.gather(runner, dirty, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                raise result


def run_lab_worker(identity):
    runtime, manifest = _read_bound_manifest(identity)
    if (runtime / INTAKE_STOP).exists():
        raise ValueError("Remove only the owned lab intake stop marker before launching")
    os.environ["POSTGRES_APPLICATION_NAME"] = "lanshare-scheduler"
    from classroom_app.services import scheduled_task_handlers  # noqa: F401 - real registrations
    from classroom_app.services import scheduled_task_service as scheduler
    from classroom_app.services import learning_progress_service as progress
    from classroom_app.db.postgres import LanSharePostgresConnection, connect_postgres
    import scheduled_task_dispatcher as dispatcher

    lock = threading.Lock()
    def record(event, **values):
        item = {"event": event, "at": datetime.now().isoformat(), "run_id": manifest["run_id"], **values}
        with lock, (runtime / background.EVENTS).open("a", encoding="utf-8") as output:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")

    dispatch, publish, original_run = scheduler._dispatch_task, progress._publish_learning_metrics, dispatcher._run
    heartbeat = scheduler.update_scheduler_heartbeat
    ready = threading.Event()

    def observed_heartbeat(worker_id, *, status, last_error=""):
        result = heartbeat(worker_id, status=status, last_error=last_error)
        if status == "running" and worker_id == "capacity-linux-background" and not ready.is_set():
            # The real heartbeat returns only after its own commit succeeded.
            record("dispatcher_ready", worker_id=worker_id)
            state.update(status="running", dispatcher_ready=True, ready_at=datetime.now().isoformat())
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            ready.set()
        return result
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

    def dirty_batch(index):
        _apply_dirty_batch(index, manifest, connect_postgres, progress.mark_student_learning_progress_dirty, record)

    async def with_pressure(args, *, stop):
        async def producer(shutdown, current_stop):
            await _wait_dispatcher_ready(ready, shutdown, current_stop, runtime)
            await _dirty_producer(manifest, runtime, shutdown, current_stop, dirty_batch)
        await _run_with_producer(original_run, args, stop, producer)

    state = {"status": "starting", "dispatcher_ready": False, "pid": os.getpid(), "run_id": manifest["run_id"],
             "lab_identity": manifest["lab_identity"], "started_at": datetime.now().isoformat(),
             "dispatcher_entrypoint": "scheduled_task_dispatcher.main"}
    state_path = runtime / STATE
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    with ExitStack() as patches:
        staged_publication = patches.enter_context(background._committed_projection_recorder(LanSharePostgresConnection, record))
        patches.enter_context(patch.object(scheduler, "_dispatch_task", measured_dispatch))
        patches.enter_context(patch.object(progress, "_publish_learning_metrics", measured_publish))
        patches.enter_context(patch.object(scheduler, "update_scheduler_heartbeat", observed_heartbeat))
        patches.enter_context(patch.dict(scheduler._HANDLERS, {background.ELASTIC_KIND: controlled_wait}))
        # Actual in-app notifications run; external email delivery is excluded.
        patches.enter_context(patch("classroom_app.services.message_center_service.queue_notification_email_if_applicable", return_value=False))
        patches.enter_context(patch.object(dispatcher, "_run", with_pressure))
        patches.enter_context(patch.object(sys, "argv", ["scheduled_task_dispatcher.py", "--worker-id", "capacity-linux-background"]))
        try:
            dispatcher.main()  # Real SIGTERM ownership, both workers, executor drain and pool close.
        except BaseException as exc:
            state.update(status="error", error_type=type(exc).__name__)
            raise
        else:
            state["status"] = "stopped"
        finally:
            state.update(stopped_at=datetime.now().isoformat(),
                         projection_measurement_errors=staged_publication.measurement_errors)
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("worker",))
    parser.add_argument("--lab-marker", type=Path, default=MARKER, choices=(MARKER,))
    args = parser.parse_args()
    from tools.platform_capacity_linux_lab import validate_container_boundary
    identity = validate_container_boundary(args.lab_marker, require_seeded=True)
    run_lab_worker(identity)


if __name__ == "__main__":
    main()
