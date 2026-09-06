"""One-process/app/schema soak with bounded metrics and an owned worker subprocess.

Default is a short smoke. Runs longer than ten minutes require --allow-long-run.
All data and AI are synthetic. No production settings or source files change.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import hashlib
import json
import math
import multiprocessing
import os
import re
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import career_mixed_load_probe as mixed
from classroom_app.db import schema_ai_jobs, schema_resume
from classroom_app.services import ai_durable_job_service as durable

CANARY = "career_soak_canary"
PHASES = ("after_claim", "execute_wait", "result_persisted", "apply_uncommitted")
PROVIDER = "career_soak_provider"
PROVIDER_CASES = ("slow_success", "timeout_recovery", "rate_limit_recovery", "disconnect_recovery",
                  "unavailable_recovery", "invalid_recovery", "invalid_exhausted")
ACTIVE_STATUSES = ("queued", "running", "retry_wait", "result_ready")
BOUNDS = tuple(0.01 * 2 ** index for index in range(25))


class Histogram:
    """Fixed memory; percentiles report bucket upper bounds, not exact samples."""
    def __init__(self):
        self.bins = [0] * (len(BOUNDS) + 1)
        self.count = 0; self.total = 0.0; self.maximum = 0.0

    def append(self, value):
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Latency must be finite and nonnegative")
        self.bins[bisect.bisect_left(BOUNDS, value)] += 1
        self.count += 1; self.total += value; self.maximum = max(self.maximum, value)

    def __len__(self):
        return self.count

    def summary(self):
        result = {"count": self.count, "sample_scope": "all observations / fixed histogram", "max_ms": round(self.maximum, 3)}
        if not self.count:
            return result
        result["mean_ms"] = round(self.total / self.count, 3)
        for percentile in (50, 95, 99):
            threshold = math.ceil(self.count * percentile / 100); cumulative = 0
            for index, number in enumerate(self.bins):
                cumulative += number
                if cumulative >= threshold:
                    result[f"p{percentile}_upper_bound_ms"] = round(BOUNDS[index] if index < len(BOUNDS) else self.maximum, 3)
                    break
        return result


class StorageBudget:
    """Keep a reserve on both local volumes before admitting more test writes."""
    def __init__(self, paths, minimum_free_gb):
        self.paths = {name: Path(path).resolve(strict=True) for name, path in paths.items()}
        self.minimum_free_bytes = int(minimum_free_gb * 1024**3)
        self.latest = {}; self.minimum_observed_free_bytes = {}

    def check(self):
        low = []
        for name, path in self.paths.items():
            usage = shutil.disk_usage(path)
            self.latest[name] = {"path": str(path), "free_bytes": usage.free, "total_bytes": usage.total}
            self.minimum_observed_free_bytes[name] = min(usage.free, self.minimum_observed_free_bytes.get(name, usage.free))
            if usage.free < self.minimum_free_bytes:
                low.append(name)
        if low:
            raise RuntimeError("Soak storage reserve reached: " + ", ".join(low))
        return self.report()

    def report(self):
        return {"reserve_bytes": self.minimum_free_bytes, "latest": self.latest,
                "minimum_observed_free_bytes": self.minimum_observed_free_bytes}


def _storage_budget(connect, args, output):
    directory = args.database_disk_path
    if not directory:
        with connect() as conn:
            directory = conn.execute("SHOW data_directory").fetchone()[0]
    path = Path(directory)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("PostgreSQL data directory is not visible on this host; pass --database-disk-path for its actual local storage volume")
    budget = StorageBudget({"evidence": output.parent, "postgresql_data": path}, args.min_free_gb)
    budget.check()
    return budget


class StreamingMeasurements(mixed.Measurements):
    def __init__(self):
        super().__init__()
        self.requests = defaultdict(Histogram); self.pool_wait = defaultdict(Histogram); self.sql_time = defaultdict(Histogram)
        self.total_histograms = defaultdict(Histogram)
        self.total_status = defaultdict(Counter)
        self.total_bytes = Counter()
        self.resources = deque(maxlen=300)
        self.arrival_dispatch_lag = Histogram()

    def timing(self, destination, label, elapsed):
        with self.lock:
            destination[label].append(elapsed)
            kind = "requests" if destination is self.requests else "pool_wait" if destination is self.pool_wait else "sql_time"
            self.total_histograms[(kind, label)].append(elapsed)

    def summarize(self, destination, label):
        return destination[label].summary()

    def flush(self):
        with self.lock:
            result = {name: {key: histogram.summary() for key, histogram in destination.items()}
                      for name, destination in (("requests", self.requests), ("pool_wait", self.pool_wait), ("sql_time", self.sql_time))}
            result["statuses"] = {key: dict(value) for key, value in self.status.items()}
            result["bytes"] = dict(self.bytes)
            for key, value in self.status.items():
                self.total_status[key].update(value)
            self.total_bytes.update(self.bytes)
            result["resources_last_300"] = list(self.resources)
            result["resources_retention"] = "at most the last 300 one-second samples in this bucket"
            result["unexpected_first_100"] = list(self.unexpected)
            result["arrival_skipped_cumulative"] = self.skipped
            result["expected_conflicts_cumulative"] = self.expected_conflicts
            for destination in (self.requests, self.pool_wait, self.sql_time, self.status, self.bytes):
                destination.clear()
            self.resources.clear()
            return result

    def totals(self):
        with self.lock:
            return {"histograms": {kind: {label: value.summary() for (stored_kind, label), value in self.total_histograms.items() if stored_kind == kind}
                                   for kind in ("requests", "pool_wait", "sql_time")},
                    "statuses": {key: dict(value) for key, value in self.total_status.items()},
                    "bytes": dict(self.total_bytes), "arrival_dispatch_lag": self.arrival_dispatch_lag.summary()}


def _check_boundary(schema):
    if urlsplit(mixed.config.DATABASE_URL).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Soak requires localhost PostgreSQL")
    if not re.fullmatch(r"resume_probe_[0-9a-f]{32}", schema):
        raise ValueError("Soak requires its newly created fixture schema")


def _pool(schema, role, maximum):
    _check_boundary(schema)
    pool = mixed.ConnectionPool(mixed.config.DATABASE_URL, min_size=1, max_size=maximum, timeout=10,
        kwargs={"row_factory": mixed.sqlite_compatible_dict_row,
                "options": f"-c search_path={schema} -c application_name=career-soak-{role}-{schema} -c statement_timeout=30000 -c lock_timeout=5000"}, open=True)
    pool.wait(timeout=15)
    return pool


def _patch_connections(stack, connect):
    # This isolated probe tests admitted execution. Rollout/authorization has
    # separate tests; an inherited deployment allowlist must not deny canaries.
    stack.enter_context(patch.object(mixed.config, "CAREER_AI_ROLLOUT_MODE", "all"))
    for name, module in list(sys.modules.items()):
        if name.startswith("classroom_app") and hasattr(module, "get_db_connection"):
            stack.enter_context(patch.object(module, "get_db_connection", connect))
        if name.startswith("classroom_app") and hasattr(module, "get_configured_db_engine"):
            stack.enter_context(patch.object(module, "get_configured_db_engine", return_value="postgres"))


def _apply_canary(conn, job, payload, result):
    return conn.execute("UPDATE probe_soak_canaries SET writes=writes+1 WHERE id=? AND revision=? AND job_id=?",
                        (payload["canary_id"], payload["revision"], job["id"])).rowcount == 1


async def _execute_canary(job, payload):
    return {"synthetic_only": True}


async def _execute_provider(job, payload):
    """Deterministic transport/response contract; the real worker owns retries.

    No network is opened. The timeout is caused by the worker's real wait_for,
    and malformed results reach its actual result-type validator.
    """
    case = payload["case"]
    if case not in PROVIDER_CASES:
        raise ValueError("Unknown synthetic provider case")
    attempt = int(job["attempt_count"])
    if case == "slow_success":
        await asyncio.sleep(payload["slow_seconds"])
    elif case == "invalid_exhausted" or attempt == 1:
        if case == "timeout_recovery":
            await asyncio.sleep(payload["timeout_seconds"] + 30)
            raise AssertionError("The worker did not enforce its provider deadline")
        if case in ("rate_limit_recovery", "unavailable_recovery"):
            status = 429 if case == "rate_limit_recovery" else 503
            request = httpx.Request("POST", "https://synthetic-provider.invalid/generate")
            response = httpx.Response(status, request=request,
                headers={"Retry-After": str(payload["retry_after_seconds"])} if status == 429 else {})
            raise httpx.HTTPStatusError(f"Synthetic provider HTTP {status}", request=request, response=response)
        if case == "disconnect_recovery":
            raise httpx.ConnectError("Synthetic provider connection interrupted")
        if case in ("invalid_recovery", "invalid_exhausted"):
            return ["synthetic invalid response: object required"]
    return {"synthetic_only": True, "case": case}


def _apply_provider(conn, job, payload, result):
    if result != {"synthetic_only": True, "case": payload["case"]}:
        raise ValueError("Synthetic provider result contract violated")
    return conn.execute("UPDATE probe_soak_providers SET writes=writes+1 WHERE id=? AND revision=? AND job_id=?",
                        (payload["probe_id"], payload["revision"], job["id"])).rowcount == 1


def _fail_provider(conn, job, payload, code, message):
    conn.execute("UPDATE probe_soak_providers SET failure_writes=failure_writes+1,failure_code=? "
                 "WHERE id=? AND revision=? AND job_id=?",
                 (code, payload["probe_id"], payload["revision"], job["id"]))


def _provider_audit(rows, attempts, *, required):
    """Compare persisted outcomes and real retry timing with a bounded script."""
    issues = []; cases = Counter(); grouped = defaultdict(list); results = []
    for attempt in attempts:
        if attempt["stage"] == "execute":
            grouped[attempt["job_id"]].append(attempt)
    def seconds(later, earlier):
        try:
            return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()
        except (TypeError, ValueError):
            return None
    for row in rows:
        case = row["case_name"]; cases[case] += 1
        history = sorted(grouped[row["job_id"]], key=lambda item: item["attempt_no"])
        errors = []
        exhausted = case == "invalid_exhausted"
        expected_count = 3 if exhausted else (1 if case == "slow_success" else 2)
        expected_status = "dead_letter" if exhausted else "succeeded"
        if row["status"] != expected_status or row["attempt_count"] != expected_count:
            errors.append("unexpected_terminal_or_attempt_count")
        if [item["attempt_no"] for item in history] != list(range(1, expected_count + 1)):
            errors.append("missing_or_extra_persisted_attempts")
        if row["writes"] != (0 if exhausted else 1) or row["failure_writes"] != (1 if exhausted else 0):
            errors.append("business_result_not_exactly_once")
        expected_code = {"timeout_recovery": "TimeoutError", "rate_limit_recovery": "HTTPStatusError",
            "unavailable_recovery": "HTTPStatusError", "disconnect_recovery": "ConnectError",
            "invalid_recovery": "ValueError", "invalid_exhausted": "ValueError"}.get(case)
        failed = history if exhausted else history[:-1]
        if any(item["status"] != "error" or item["error_code"] != expected_code or not item["error_message"] for item in failed):
            errors.append("unexpected_or_unexplained_attempt_error")
        if not exhausted and history and history[-1]["status"] != "success":
            errors.append("successful_execute_not_recorded")
        if exhausted and (row["last_error_code"] != "ValueError" or row["failure_code"] != "ValueError" or not row["last_error"]):
            errors.append("terminal_failure_not_explained")
        delays = []
        for previous, current in zip(history, history[1:]):
            minimum = (15, 60, 180)[min(previous["attempt_no"] - 1, 2)] + row["job_id"] % 7
            if case == "timeout_recovery":
                minimum = max(minimum, mixed.registry.UPSTREAM_COOLDOWN_SECONDS)
            if case == "rate_limit_recovery":
                minimum = max(minimum, row["retry_after_seconds"])
            elapsed = seconds(current["started_at"], previous["finished_at"])
            # Ledger timestamps have second precision; do not accelerate them.
            delays.append({"after_attempt": previous["attempt_no"], "minimum_seconds": minimum,
                           "observed_seconds": elapsed})
            if elapsed is None or elapsed < minimum - 1:
                errors.append("retry_started_before_real_backoff")
        if history and case in ("slow_success", "timeout_recovery"):
            minimum = row["slow_seconds"] if case == "slow_success" else row["timeout_seconds"]
            elapsed = seconds(history[0]["finished_at"], history[0]["started_at"])
            if elapsed is None or elapsed < minimum - 1:
                errors.append("slow_or_timeout_wait_not_exercised")
            if case == "timeout_recovery":
                hold = seconds(row["capacity_reserved_until"], history[0]["finished_at"])
                if hold is None or hold < mixed.registry.UPSTREAM_COOLDOWN_SECONDS - 1:
                    errors.append("timeout_capacity_cooldown_missing")
        if errors:
            issues.append({"probe_id": row["id"], "case": case, "errors": errors})
        results.append({**row, "attempts": history, "retry_delays": delays, "ok": not errors})
    complete = set(PROVIDER_CASES).issubset(cases)
    if required and not complete:
        issues.append({"errors": ["provider_fault_cases_not_all_exercised"]})
    return {"ok": not issues, "required": required, "complete_case_coverage": complete,
            "case_counts": dict(cases), "issues": issues, "jobs": results,
            "scope": "Synthetic provider transport exceptions and invalid-result contract via real durable worker; no external network or provider SDK exercised; no backoff/lease clock acceleration."}


def _worker_main(schema, channel, stub_seconds, provider_timeout_seconds):
    """All database work stays in this schema and the worker's four-slot pool."""
    _check_boundary(schema)
    mixed.worker._load_domains()
    with ExitStack() as stack:
        pool = _pool(schema, "worker", 4); stack.callback(pool.close)
        def connect():
            return mixed.LanSharePostgresConnection(pool.getconn(timeout=10), pool=pool)
        _patch_connections(stack, connect)
        stack.enter_context(patch.object(mixed.worker, "CAREER_JOBS_ENABLED", True))
        stack.enter_context(patch.object(mixed.worker, "AI_CONCURRENCY", 2))
        stack.enter_context(patch.object(mixed.worker, "POLL_SECONDS", 1))
        schema_ai_jobs.reset_ai_job_schema_guard_for_tests()
        with connect() as conn:
            schema_ai_jobs.ensure_ai_job_schema(conn, engine="postgres"); conn.commit()
        originals = mixed.registry.registered_student_career_handlers()
        mixed.registry._HANDLERS.clear()
        active = maximum = executions = 0

        def checkpoint(job, phase):
            channel.send({"kind": "checkpoint", "phase": phase, "pid": os.getpid(), "job_id": job["id"]})

        async def pause(job, phase):
            checkpoint(job, phase)
            await asyncio.sleep(45)
            raise TimeoutError("Soak controller did not kill its checkpoint worker")

        async def stub(job, payload):
            nonlocal active, maximum, executions
            active += 1; maximum = max(maximum, active); executions += 1
            try:
                if job["task_type"] == CANARY:
                    if payload["checkpoint"] == "execute_wait" and job["attempt_count"] == 1:
                        await pause(job, "execute_wait")
                    return {"synthetic_only": True}
                if job["task_type"] == PROVIDER:
                    return await _execute_provider(job, payload)
                await asyncio.sleep(stub_seconds)
                if job["task_type"] == "career_major_network_generate":
                    return {"network": mixed.baseline_network(payload["major_name"]), "sources": {"synthetic_only": True}}
                return {"ok": True, "content": "合成介绍：参与课程讨论与资料整理。"}
            finally:
                active -= 1

        def apply_canary(conn, job, payload, result):
            changed = _apply_canary(conn, job, payload, result)
            if changed and payload["checkpoint"] == "apply_uncommitted" and job["delivery_attempt_count"] == 1:
                checkpoint(job, "apply_uncommitted")
                if not channel.poll(45):
                    raise TimeoutError("Soak controller did not kill its uncommitted worker")
                raise RuntimeError("A paused transaction must not resume")
            return changed

        for kind in ("career_major_network_generate", "resume_suggestion"):
            handler = originals[kind]
            mixed.registry.register_student_career_handler(kind, execute=stub, apply=handler.apply, fail=handler.fail, timeout_seconds=90)
        mixed.registry.register_student_career_handler(CANARY, execute=stub, apply=apply_canary, timeout_seconds=90)
        mixed.registry.register_student_career_handler(PROVIDER, execute=stub, apply=_apply_provider,
            fail=_fail_provider, timeout_seconds=provider_timeout_seconds)
        original_execute = mixed.worker._execute
        async def instrument_execute(job):
            payload = durable.load_ai_job_payload(job)
            phase = payload.get("checkpoint") if job["task_type"] == CANARY and job["attempt_count"] == 1 else None
            if phase == "after_claim":
                await pause(job, phase)
            await original_execute(job)
            if phase == "result_persisted":
                await pause(job, phase)
        stack.enter_context(patch.object(mixed.worker, "_execute", instrument_execute))

        async def run_worker():
            mixed.worker.start_student_career_job_workers()
            process = mixed.psutil.Process(); process.cpu_percent()
            channel.send({"kind": "ready", "pid": os.getpid()})
            last_report = 0.0
            try:
                while True:
                    if channel.poll() and channel.recv().get("command") == "stop":
                        break
                    if time.monotonic() - last_report >= 2:
                        channel.send({"kind": "worker", "pid": os.getpid(), "rss_mb": round(process.memory_info().rss / 1048576, 3),
                            "cpu_percent_one_core_100": process.cpu_percent(), "pool": pool.get_stats(),
                            "stub_active": active, "stub_max": maximum, "stub_executions": executions,
                            "worker_error": mixed.worker.student_career_worker_snapshot()["last_error"]})
                        last_report = time.monotonic()
                    await asyncio.sleep(0.2)
            finally:
                await mixed.worker.stop_student_career_job_workers()
        try:
            asyncio.run(run_worker())
        finally:
            channel.close()


class WorkerController:
    def __init__(self, schema, connect, stats, args, bucket_writer):
        self.schema, self.connect, self.stats, self.args, self.bucket_writer = schema, connect, stats, args, bucket_writer
        self.process = self.channel = self.task = None
        self.draining = False; self.finished = False; self.generation = 0
        self.faults = []; self.errors = []; self.latest = {}; self.max_scope_connections = 0
        self.source_hashes = _hashes(); self.source_changes = set()
        self.next_fault = 0; self.started = 0; self.last_bucket = 0; self.pending_fault = None
        self.exercise_task = None; self.monitor_failure = None
        self.storage_budget = None
        self.pending_bucket = None
        self.provider_rounds = []; self.provider_active_round = None; self.provider_next = 0
        self.provider_last_sample = 0; self.provider_final_audit = None
        self.drain_curve_samples = 0

    def _spawn(self):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker_main,
            args=(self.schema, child, self.args.stub_seconds, self.args.provider_timeout_seconds), name="career-owned-soak-worker")
        process.start(); child.close()
        self.process, self.channel = process, parent
        self.generation += 1; self.latest = {"generation": self.generation, "pid": process.pid}

    def start(self):
        self.started = self.last_bucket = time.monotonic()
        self.next_fault = self.started + self.args.fault_interval
        self.provider_next = self.started + self.args.provider_first_offset
        self._spawn()
        self.task = asyncio.create_task(self.monitor())
        self.task.add_done_callback(self._monitor_finished)
        return 1

    def _monitor_finished(self, task):
        # This task must supervise admission, not merely be awaited after 24h.
        error = asyncio.CancelledError("Soak monitor was cancelled") if task.cancelled() else task.exception()
        if error is not None and not self.finished:
            self.monitor_failure = error
            if self.exercise_task is not None and not self.exercise_task.done():
                self.exercise_task.cancel()

    def enqueue_fault(self, phase):
        with self.connect() as conn:
            number = len(self.faults) + 1
            conn.execute("INSERT INTO probe_soak_canaries(id,revision,writes) VALUES(?,1,0)", (number,))
            job = mixed.registry.enqueue_student_career_job(conn, task_type=CANARY, dedupe_key=f"soak-canary:{number}",
                payload={"canary_id": number, "revision": 1, "checkpoint": phase}, scope_type="soak-canary", scope_id=str(number))
            conn.execute("UPDATE probe_soak_canaries SET job_id=? WHERE id=?", (job["id"], number)); conn.commit()
        return {"phase": phase, "job_id": job["id"], "admitted_elapsed_seconds": round(time.monotonic() - self.started, 3)}

    def enqueue_provider_round(self):
        number = len(self.provider_rounds) + 1; ids = []
        with self.connect() as conn:
            for index, case in enumerate(PROVIDER_CASES):
                probe_id = (number - 1) * len(PROVIDER_CASES) + index + 1
                conn.execute("INSERT INTO probe_soak_providers(id,round_no,case_name,revision,writes,failure_writes,"
                    "slow_seconds,timeout_seconds,retry_after_seconds) VALUES(?,?,?,1,0,0,?,?,?)",
                    (probe_id, number, case, self.args.provider_slow_seconds, self.args.provider_timeout_seconds,
                     self.args.provider_retry_after_seconds))
                payload = {"probe_id": probe_id, "revision": 1, "case": case,
                    "slow_seconds": self.args.provider_slow_seconds, "timeout_seconds": self.args.provider_timeout_seconds,
                    "retry_after_seconds": self.args.provider_retry_after_seconds}
                job = mixed.registry.enqueue_student_career_job(conn, task_type=PROVIDER,
                    dedupe_key=f"soak-provider:{probe_id}", payload=payload, scope_type="soak-provider", scope_id=str(probe_id))
                conn.execute("UPDATE probe_soak_providers SET job_id=? WHERE id=?", (job["id"], probe_id)); ids.append(job["id"])
            conn.commit()
        return {"round_no": number, "job_ids": ids, "admitted_elapsed_seconds": round(time.monotonic()-self.started, 3),
                "samples": 0, "peak_waiting": 0, "peak_active": 0, "completed": False}

    def provider_attempts(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT a.job_id,a.attempt_no,a.stage,a.status,a.error_code,a.error_message,a.started_at,a.finished_at "
                "FROM ai_job_attempts a JOIN probe_soak_providers p ON p.job_id=a.job_id ORDER BY a.job_id,a.attempt_no,a.stage")]

    def record_queue_sample(self, database, *, phase):
        active = sum(row["count"] for row in database["jobs"] if row["status"] in ACTIVE_STATUSES)
        waiting = sum(row["count"] for row in database["jobs"] if row["status"] in ("queued", "retry_wait"))
        sample = {"phase": phase, "elapsed_seconds": round(time.monotonic()-self.started, 3), "active": active,
                  "waiting": waiting, "jobs": database["jobs"], "provider_jobs": database.get("provider_jobs", [])}
        if self.provider_active_round:
            current = self.provider_active_round; sample["round_no"] = current["round_no"]
            current["samples"] += 1; current["peak_waiting"] = max(current["peak_waiting"], waiting)
            current["peak_active"] = max(current["peak_active"], active)
        if phase == "draining":
            self.drain_curve_samples += 1
        self.bucket_writer("queue_recovery_sample", sample)

    def kill_checkpoint(self, message):
        assert self.pending_fault and message["pid"] == self.process.pid and message["job_id"] == self.pending_fault["job_id"]
        assert message["phase"] == self.pending_fault["phase"] and self.process.is_alive()
        self.process.kill(); self.process.join(10)
        assert not self.process.is_alive() and self.process.exitcode not in (None, 0)
        event = {**self.pending_fault, "pid": self.process.pid, "generation": self.generation,
                 "exit_code": self.process.exitcode, "joined": True, "elapsed_seconds": round(time.monotonic() - self.started, 3)}
        self.channel.close(); self.process.close(); self.process = self.channel = None
        if self.args.fast_fault_recovery:
            with self.connect() as conn:
                event["test_leases_advanced"] = conn.execute("UPDATE ai_jobs SET lease_expires_at=? WHERE status IN ('running','result_ready') AND lease_token<>''",
                    (durable._iso(durable._now() - timedelta(seconds=1)),)).rowcount
                conn.commit()
        self.faults.append(event); self.pending_fault = None
        self.bucket_writer("fault", event)
        self._spawn()

    def database_snapshot(self):
        with self.connect() as conn:
            jobs = [dict(row) for row in conn.execute("SELECT task_type,status,COUNT(*) AS count FROM ai_jobs GROUP BY task_type,status")]
            connections = conn.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE application_name IN (?,?)",
                (f"career-soak-app-{self.schema}", f"career-soak-worker-{self.schema}")).fetchone()[0]
            activity = [dict(row) for row in conn.execute(
                "SELECT application_name,state,wait_event_type,wait_event,COUNT(*) AS count FROM pg_stat_activity "
                "WHERE application_name IN (?,?) GROUP BY application_name,state,wait_event_type,wait_event",
                (f"career-soak-app-{self.schema}", f"career-soak-worker-{self.schema}"))]
            canaries = [dict(row) for row in conn.execute("SELECT id,writes FROM probe_soak_canaries ORDER BY id")]
            providers = [dict(row) for row in conn.execute(
                "SELECT p.*,j.status,j.attempt_count,j.max_attempts,j.last_error_code,j.last_error,j.started_at,j.finished_at,"
                "j.available_at,j.capacity_reserved_until FROM probe_soak_providers p JOIN ai_jobs j ON j.id=p.job_id ORDER BY p.id")]
        self.max_scope_connections = max(self.max_scope_connections, connections)
        return {"jobs": jobs, "scope_connections": connections, "database_activity": activity, "canaries": canaries,
                "provider_jobs": providers}

    async def monitor(self):
        try:
            while not self.finished:
                while self.channel and self.channel.poll():
                    message = self.channel.recv()
                    if message["kind"] == "checkpoint":
                        await asyncio.to_thread(self.kill_checkpoint, message)
                        break
                    if message["kind"] == "worker":
                        self.latest = {**message, "generation": self.generation}
                        self.stats.max_stub = max(self.stats.max_stub, message["stub_max"])
                        self.stats.current_stub = message["stub_active"]
                        if message["worker_error"] and len(self.errors) < 50:
                            self.errors.append({"generation": self.generation, "worker_error": message["worker_error"][:500]})
                if self.process and not self.process.is_alive():
                    raise RuntimeError("Owned soak worker exited outside an injected checkpoint")
                now = time.monotonic()
                # Do not burn provider attempt budgets with a simultaneous
                # injected process kill; these are separate, attributable cases.
                if not self.draining and not self.pending_fault and not self.provider_active_round and now >= self.next_fault:
                    self.pending_fault = await asyncio.to_thread(self.enqueue_fault, PHASES[len(self.faults) % 4])
                    self.next_fault = now + self.args.fault_interval
                if (self.args.provider_faults and not self.draining and not self.pending_fault and not self.provider_active_round
                    and now >= self.provider_next and len(self.provider_rounds) < self.args.provider_max_rounds
                    and (self.args.duration <= 600 or now < self.started + self.args.duration - 300)):
                    current = await asyncio.to_thread(self.enqueue_provider_round)
                    self.provider_rounds.append(current); self.provider_active_round = current
                    self.provider_next = now + self.args.provider_fault_interval
                    self.provider_last_sample = 0
                    self.bucket_writer("provider_round_admitted", current)
                if self.provider_active_round and now - self.provider_last_sample >= self.args.provider_sample_seconds:
                    database = await asyncio.to_thread(self.database_snapshot)
                    self.record_queue_sample(database, phase="draining" if self.draining else "provider_fault_recovery")
                    current = self.provider_active_round
                    rows = [row for row in database["provider_jobs"] if row["round_no"] == current["round_no"]]
                    if len(rows) == len(PROVIDER_CASES) and all(row["status"] not in ACTIVE_STATUSES for row in rows):
                        history = await asyncio.to_thread(self.provider_attempts)
                        audit = _provider_audit(rows, history, required=True)
                        current.update(completed=True, completed_elapsed_seconds=round(now-self.started, 3),
                                       audit_ok=audit["ok"], backlog_observed=current["peak_waiting"] > 0)
                        self.bucket_writer("provider_round_completed", {"round": current, "audit": audit})
                        if not audit["ok"]:
                            raise RuntimeError("Synthetic provider attempt/terminal audit failed")
                        self.provider_active_round = None
                    self.provider_last_sample = now
                if now - self.last_bucket >= self.args.bucket_seconds:
                    storage = await asyncio.to_thread(self.storage_budget.check) if self.storage_budget else None
                    database = await asyncio.to_thread(self.database_snapshot)
                    current_hashes = await asyncio.to_thread(_hashes)
                    self.source_changes.update(name for name in set(self.source_hashes) | set(current_hashes)
                                               if self.source_hashes.get(name) != current_hashes.get(name))
                    self.pending_bucket = {"elapsed_seconds": round(now-self.started, 3), "generation": self.generation,
                        "worker_last_sample": self.latest, "database": database, "metrics": self.stats.flush(),
                        "storage": storage,
                        "source_changes_observed": sorted(self.source_changes)}
                    self.bucket_writer("bucket", self.pending_bucket)
                    self.pending_bucket = None
                    self.last_bucket = now
                await asyncio.sleep(0.2)
        except Exception as exc:
            self.errors.append({"controller_error": type(exc).__name__})
            raise

    async def stop(self):
        self.draining = True
        deadline = time.monotonic() + self.args.drain_seconds
        drained = False; last_sample = 0
        try:
            while time.monotonic() < deadline:
                if self.task and self.task.done():
                    await self.task
                database = await asyncio.to_thread(self.database_snapshot)
                active = sum(row["count"] for row in database["jobs"] if row["status"] in ACTIVE_STATUSES)
                if time.monotonic() - last_sample >= self.args.provider_sample_seconds or active == 0:
                    self.record_queue_sample(database, phase="draining"); last_sample = time.monotonic()
                if active == 0 and self.pending_fault is None:
                    drained = True; break
                await asyncio.sleep(0.5)
            self.final_database = await asyncio.to_thread(self.database_snapshot)
            if self.args.provider_faults:
                history = await asyncio.to_thread(self.provider_attempts)
                self.provider_final_audit = _provider_audit(self.final_database["provider_jobs"], history, required=True)
                if self.provider_active_round and drained:
                    self.provider_active_round.update(completed=True, completed_elapsed_seconds=round(time.monotonic()-self.started, 3),
                        audit_ok=self.provider_final_audit["ok"], backlog_observed=self.provider_active_round["peak_waiting"] > 0)
                    self.provider_active_round = None
            self.drained = drained
            if not drained:
                self.errors.append({"drain_error": "accepted_jobs_not_drained"})
        finally:
            self.finished = True
            if self.task:
                await asyncio.gather(self.task, return_exceptions=True)
            await asyncio.to_thread(self.close_owned_worker)

    def close_owned_worker(self):
        if self.process:
            if self.process.is_alive():
                try:
                    self.channel.send({"command": "stop"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
                self.process.join(10)
            if self.process.is_alive():
                self.process.kill(); self.process.join(10)
                self.errors.append({"shutdown_error": "forced_worker_shutdown"})
            assert not self.process.is_alive()
            self.channel.close(); self.process.close(); self.process = self.channel = None


def _hashes():
    result = mixed.source_hashes()
    result["tools/career_soak_probe.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


async def _supervised_exercise(fixture, connect, pool, stats, args, reminder, controller):
    controller.exercise_task = asyncio.create_task(mixed.exercise(fixture, connect, pool, stats, args, reminder))
    try:
        result = await controller.exercise_task
        if controller.monitor_failure is not None:
            raise controller.monitor_failure
        return result
    except asyncio.CancelledError:
        if controller.monitor_failure is not None:
            raise controller.monitor_failure
        raise
    finally:
        # mixed.exercise stops the worker before the reminder. Its worker-stop
        # hook can itself raise after a controller fault, so never rely on that
        # hook reaching reminder.finish before restoring the database patches.
        try:
            await reminder.finish()
        finally:
            await asyncio.to_thread(controller.close_owned_worker)


def _write_report(output, report):
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


def _failure_report(args, stats, controller, fixture, before, stream_path, error):
    tail = stats.flush()
    return {"ok": False, "phase": "failed", "synthetic_only": True, "configuration": vars(args),
        "error": {"type": type(error).__name__, "message": str(error)[:1000]},
        "metrics": stats.totals(), "unexpected_first_100": list(stats.unexpected),
        "last_bucket": tail, "unwritten_bucket": controller.pending_bucket if controller else None,
        "source_hashes_before": before, "buckets_jsonl": str(stream_path),
        "schema": (fixture or {}).get("schema"),
        "schema_removed": (fixture or {}).get("soak_schema_removed", False),
        "owned_connections_remaining": (fixture or {}).get("soak_connections_remaining"),
        "worker_errors": list(controller.errors) if controller else [],
        "storage": controller.storage_budget.report() if controller and controller.storage_budget else None,
        "worker_stopped": not controller or controller.process is None,
        "faults": list(controller.faults) if controller else [],
        "provider_rounds": list(controller.provider_rounds) if controller else [],
        "provider_audit": controller.provider_final_audit if controller else None}


def _persist_failure(args, stats, controller, fixture, before, stream_path, error, write, output):
    failure = _failure_report(args, stats, controller, fixture, before, stream_path, error)
    # A full disk can prevent both artifacts. Never hide that behind
    # a normal exit: retain the original exception and emit stderr.
    try:
        write("failure", failure)
    except Exception as evidence_error:
        print(f"SOAK FAILURE EVIDENCE WRITE FAILED: {type(evidence_error).__name__}; "
              f"schema={failure['schema']} original={failure['error']}", file=sys.stderr, flush=True)
    try:
        _write_report(output, failure)
    except Exception as evidence_error:
        print(f"SOAK FAILURE REPORT WRITE FAILED: {type(evidence_error).__name__}; "
              f"schema={failure['schema']}", file=sys.stderr, flush=True)
    return failure


@contextmanager
def _verified_fixture(students, write):
    fixture = None
    try:
        with mixed.isolated_career_postgres(students=students) as fixture:
            yield fixture
    finally:
        if fixture:
            import psycopg
            schema = fixture["schema"]; _check_boundary(schema)
            with psycopg.connect(mixed.config.DATABASE_URL, connect_timeout=5, autocommit=True) as admin:
                removed = admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s", (schema,)).fetchone()[0] == 0
                connections = admin.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE application_name IN (%s,%s)",
                    (f"career-soak-app-{schema}", f"career-soak-worker-{schema}")).fetchone()[0]
            fixture["soak_schema_removed"] = removed; fixture["soak_connections_remaining"] = connections
            write("cleanup", {"schema_removed": removed, "owned_connections_remaining": connections})
            assert removed and connections == 0


def run(args):
    validate_args(args)
    stats = StreamingMeasurements(); before = _hashes()
    output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    stream_path = output.with_suffix(".buckets.jsonl")
    if output.exists() or stream_path.exists():
        raise ValueError("Use a new output name; soak evidence must not overwrite an earlier run")
    started = time.monotonic(); controller = None; fixture = None
    with stream_path.open("x", encoding="utf-8", buffering=1) as stream:
        def write(kind, data):
            stream.write(json.dumps({"kind": kind, "elapsed_wall_seconds": round(time.monotonic()-started, 3), **data}, ensure_ascii=False) + "\n")
            stream.flush()
        @contextmanager
        def record_failures():
            try:
                yield
            except BaseException as exc:
                _persist_failure(args, stats, controller, fixture, before, stream_path, exc, write, output)
                raise
        with record_failures():
            write("start", {"configuration": vars(args), "source_hashes": before})
            with _verified_fixture(args.users, write) as fixture, ExitStack() as stack:
                schema = fixture["schema"]; pool = _pool(schema, "app", 8); stack.callback(pool.close)
                write("fixture", {"schema": schema, "application_pid": os.getpid(), "application_pool_max": 8, "worker_pool_max": 4})
                connect = mixed.pooled_connection_factory(pool, stats)
                _patch_connections(stack, connect)
                storage_budget = _storage_budget(connect, args, output)
                write("storage_preflight", storage_budget.report())
                reminder = mixed.ReminderMeasurements(connect, mixed.LABEL, poll_seconds=args.scheduler_poll_seconds)
                reminder.install(stack)
                originals = dict(mixed.registry._HANDLERS); policies = dict(durable.TASK_POLICIES)
                stack.callback(lambda: (mixed.registry._HANDLERS.clear(), mixed.registry._HANDLERS.update(originals), durable.TASK_POLICIES.clear(), durable.TASK_POLICIES.update(policies)))
                mixed.registry.register_student_career_handler(CANARY, execute=_execute_canary, apply=_apply_canary)
                mixed.registry.register_student_career_handler(PROVIDER, execute=_execute_provider, apply=_apply_provider,
                    fail=_fail_provider, timeout_seconds=args.provider_timeout_seconds)
                with connect() as conn:
                    conn.execute("CREATE TABLE probe_soak_canaries(id BIGINT PRIMARY KEY,revision INTEGER,job_id BIGINT,writes INTEGER NOT NULL)")
                    conn.execute("CREATE TABLE probe_soak_providers(id BIGINT PRIMARY KEY,round_no INTEGER NOT NULL,case_name TEXT NOT NULL,"
                        "revision INTEGER NOT NULL,job_id BIGINT UNIQUE,writes INTEGER NOT NULL,failure_writes INTEGER NOT NULL,"
                        "failure_code TEXT NOT NULL DEFAULT '',slow_seconds DOUBLE PRECISION NOT NULL,"
                        "timeout_seconds DOUBLE PRECISION NOT NULL,retry_after_seconds INTEGER NOT NULL)")
                    conn.commit()
                controller = WorkerController(schema, connect, stats, args, write)
                controller.storage_budget = storage_budget
                stack.enter_context(patch.object(mixed.worker, "start_student_career_job_workers", controller.start))
                stack.enter_context(patch.object(mixed.worker, "stop_student_career_job_workers", controller.stop))
                try:
                    details = asyncio.run(_supervised_exercise(fixture, connect, pool, stats, args, reminder, controller))
                    details["assignment_reminders"] = reminder.verify_duplicate_replay()
                finally:
                    controller.close_owned_worker()
                write("final_bucket", {"metrics": stats.flush(), "database": controller.final_database})
            removed, connections_left = fixture["soak_schema_removed"], fixture["soak_connections_remaining"]
            after = _hashes(); changed = sorted(controller.source_changes | {name for name in set(before) | set(after) if before.get(name) != after.get(name)})
            final_jobs = controller.final_database["jobs"]
            provider_audit = controller.provider_final_audit or _provider_audit([], [], required=args.provider_faults)
            expected_dead_letters = sum(row["case_name"] == "invalid_exhausted" for row in controller.final_database["provider_jobs"])
            bad_jobs = [row for row in final_jobs if row["status"] not in ("succeeded", "cancelled", "superseded")
                        and not (provider_audit["ok"] and row["task_type"] == PROVIDER and row["status"] == "dead_letter"
                                 and row["count"] == expected_dead_letters)]
            canaries_once = all(row["writes"] == 1 for row in controller.final_database["canaries"])
            total_metrics = stats.totals()
            admitted = details["major_networks"] + len(controller.final_database["canaries"]) + len(controller.final_database["provider_jobs"]) + sum(
                statuses.get("202", 0) for label, statuses in total_metrics["statuses"].items() if label.endswith("/suggestion_enqueue"))
            retained = sum(row["count"] for row in final_jobs)
            report = {"ok": not stats.unexpected and stats.skipped == 0 and not controller.errors and controller.drained and
                             stats.max_stub <= 2 and controller.max_scope_connections <= 12 and not bad_jobs and canaries_once and
                             details["assignment_reminders"]["ok"] and removed and connections_left == 0 and not changed and admitted == retained
                             and provider_audit["ok"] and (not args.provider_faults or
                                 all(item.get("completed") and item.get("backlog_observed") for item in controller.provider_rounds)),
                "synthetic_only": True, "configuration": vars(args), "elapsed_with_setup_seconds": round(time.monotonic()-started, 3),
                "same_application_process_and_schema": True, "application_pid": os.getpid(), "schema": schema,
                "worker_generations": controller.generation,
                "storage": storage_budget.report(),
                "worker_pool_max": 4, "application_and_reminder_pool_max": 8, "scope_connections_observed_max": controller.max_scope_connections,
                "measurement_scope": "One persistent ASGI application/generator/reminder process. Worker RSS is per generation and resets at injected kills. Fixed-memory latency histograms; 300 latest resource samples per bucket. No TCP/TLS, external AI, SMTP, or Office export. Desktop data is not a production SLA.",
                "fixture_rollout_policy": "all in this test's application and worker processes; deployment policy unchanged",
                "timing_method": "natural production 120-second leases" if not args.fast_fault_recovery else "smoke only: advance isolated terminated worker task leases; not production wall-clock recovery",
                "buckets_jsonl": str(stream_path), "metrics": total_metrics, "faults": controller.faults,
                "fault_checkpoints_exercised": sorted({event["phase"] for event in controller.faults}),
                "complete_fault_rotation_exercised": set(PHASES).issubset({event["phase"] for event in controller.faults}),
                "provider_faults": provider_audit, "provider_rounds": controller.provider_rounds,
                "expected_provider_dead_letters": expected_dead_letters,
                "queue_recovery_curve": {"location": str(stream_path), "event_kind": "queue_recovery_sample",
                    "sample_seconds": args.provider_sample_seconds, "drain_samples": controller.drain_curve_samples,
                    "scope": "Every provider fault round and final stop-admission drain; samples streamed, not retained in memory"},
                "admitted_jobs": admitted, "retained_jobs": retained,
                "worker_errors": controller.errors, "accepted_jobs_drained": controller.drained, "final_jobs": final_jobs,
                "canaries_all_published_once": canaries_once, "canaries": controller.final_database["canaries"],
                "max_model_stub_concurrency": stats.max_stub, "unexpected_first_100": stats.unexpected,
                "arrival_skipped": stats.skipped, "schema_removed": removed, "owned_connections_remaining": connections_left,
                "fixed_code_during_run": not changed, "sources_changed_during_run": changed,
                "assignment_reminders": details["assignment_reminders"], "teaching_dataset": details["teaching_dataset"],
                "source_hashes_before": before}
            write("finished", {"ok": report["ok"], "schema_removed": removed, "owned_connections_remaining": connections_left})
            _write_report(output, report)
    return report


def validate_args(args):
    if not (1 <= args.duration <= 86400 and 10 <= args.users <= 1000 and 0 < args.rps <= 10 and 1 <= args.writers <= args.users
            and args.save_interval >= 1 and 0 < args.job_rps <= 1 and args.bucket_seconds >= 1 and args.fault_interval >= 5
            and args.reminder_interval >= 5 and args.reminder_offset >= 1 and args.scheduler_poll_seconds >= 5
            and 0 < args.drain_seconds <= 900 and 0 <= args.conflict_rate <= 1 and 1 <= args.max_inflight <= 100
            and 0 <= args.baseline_duration <= 60 and 0 < args.stub_seconds <= 10 and .25 <= args.min_free_gb <= 1000
            and 1 <= args.provider_first_offset <= 3600 and 30 <= args.provider_fault_interval <= 86400
            and 1 <= args.provider_slow_seconds < args.provider_timeout_seconds <= 90
            and 22 <= args.provider_retry_after_seconds <= 120 and 1 <= args.provider_sample_seconds <= 30
            and 1 <= args.provider_max_rounds <= 24):
        raise ValueError("Invalid bounded soak configuration")
    if args.duration > 600 and (not args.allow_long_run or args.fast_fault_recovery or args.bucket_seconds < 300 or args.fault_interval < 3600):
        raise ValueError("Long runs require explicit allow-long-run, natural leases, >=300s buckets and >=3600s faults")
    if args.duration > 600 and (args.reminder_interval < 300 or args.writers > 100 or args.save_interval < 30 or args.job_rps > .1):
        raise ValueError("Low-intensity long runs require reminders >=300s, writers <=100, saves >=30s and job RPS <=0.1")
    if args.provider_faults and (args.fast_fault_recovery or args.provider_first_offset >= args.duration):
        raise ValueError("Provider fault runs require natural lease/backoff timing and an injection inside the admission window")
    if args.duration > 600 and (not args.provider_faults or args.provider_fault_interval < 3600
                              or args.provider_sample_seconds < 5 or args.provider_slow_seconds < 30):
        raise ValueError("Long soak requires explicit --provider-faults, >=3600s injection interval, >=5s queue samples and >=30s slow provider")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name, value, kind in (("duration", 60, float), ("rps", 2, float), ("users", 100, int), ("writers", 10, int),
        ("save-interval", 60, float), ("baseline-duration", 2, float), ("job-rps", .05, float), ("stub-seconds", .2, float),
        ("conflict-rate", .05, float), ("max-inflight", 30, int), ("seed", 42, int), ("reminder-interval", 600, float),
        ("reminder-offset", 10, float), ("scheduler-poll-seconds", 20, float), ("bucket-seconds", 300, float),
        ("fault-interval", 3600, float), ("drain-seconds", 300, float)):
        result.add_argument("--" + name, type=kind, default=value)
    result.add_argument("--fast-fault-recovery", action="store_true")
    result.add_argument("--allow-long-run", action="store_true")
    result.add_argument("--output", default=".codex-temp/career-soak-smoke.json")
    result.add_argument("--min-free-gb", type=float, default=2,
                        help="Abort before more writes when either measured local volume has less free space (default: 2 GiB)")
    result.add_argument("--database-disk-path", default="",
                        help="Actual host directory for the local PostgreSQL data volume, if SHOW data_directory is not host-visible")
    result.add_argument("--provider-faults", action="store_true",
                        help="Inject bounded provider fault rounds through the real worker; required for long soak")
    for name, value, kind in (("provider-first-offset", 60, float), ("provider-fault-interval", 3600, float),
        ("provider-slow-seconds", 35, float), ("provider-timeout-seconds", 45, float),
        ("provider-retry-after-seconds", 30, int), ("provider-sample-seconds", 5, float), ("provider-max-rounds", 24, int)):
        result.add_argument("--" + name, type=kind, default=value)
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    report = run(args)
    print(json.dumps({key: report[key] for key in ("ok", "elapsed_with_setup_seconds", "accepted_jobs_drained", "faults", "worker_errors",
        "scope_connections_observed_max", "schema_removed", "owned_connections_remaining", "sources_changed_during_run")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
