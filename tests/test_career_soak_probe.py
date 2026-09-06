"""Bounded streaming measurements and safe long-run admission."""
import asyncio
import copy
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools import career_soak_probe as soak


class SoakMeasurementTests(unittest.TestCase):
    def test_histogram_keeps_all_counts_in_fixed_memory_including_late_slow_samples(self):
        values = soak.Histogram()
        original_bins = len(values.bins)
        for _ in range(250001):
            values.append(1)
        for _ in range(50000):
            values.append(5000)
        result = values.summary()
        self.assertEqual(len(values.bins), original_bins)
        self.assertEqual(result["count"], 300001)
        self.assertGreaterEqual(result["p95_upper_bound_ms"], 5000)
        self.assertEqual(result["max_ms"], 5000)
        self.assertEqual(result["sample_scope"], "all observations / fixed histogram")

    def test_bucket_flush_preserves_cumulative_counts_and_bounds_resources(self):
        stats = soak.StreamingMeasurements()
        for _ in range(4):
            stats.timing(stats.requests, "mixed/read", 2)
            stats.status["mixed/read"]["200"] += 1
        for index in range(400):
            stats.resources.append({"elapsed": index})
        first = stats.flush()
        self.assertEqual(first["requests"]["mixed/read"]["count"], 4)
        self.assertEqual(len(first["resources_last_300"]), 300)
        self.assertFalse(stats.resources)
        self.assertFalse(stats.requests)
        stats.timing(stats.requests, "mixed/read", 1000)
        stats.status["mixed/read"]["500"] += 1
        stats.flush()
        total = stats.totals()
        self.assertEqual(total["histograms"]["requests"]["mixed/read"]["count"], 5)
        self.assertEqual(total["statuses"]["mixed/read"], {"200": 4, "500": 1})
        self.assertEqual(total["histograms"]["requests"]["mixed/read"]["max_ms"], 1000)

    def test_long_run_requires_explicit_switch_and_natural_lease_recovery(self):
        args = soak.parser().parse_args(["--duration", "86400"])
        with self.assertRaises(ValueError):
            soak.validate_args(args)
        args.allow_long_run = True
        args.provider_faults = True
        soak.validate_args(args)
        for key, value in (("fast_fault_recovery", True), ("bucket_seconds", 5), ("fault_interval", 5),
                           ("reminder_interval", 5), ("save_interval", 1), ("job_rps", 1)):
            args = soak.parser().parse_args(["--duration", "86400", "--allow-long-run", "--provider-faults"])
            setattr(args, key, value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                soak.validate_args(args)

    def test_remote_or_unowned_schema_is_rejected_before_pool_creation(self):
        with patch.object(soak.mixed.config, "DATABASE_URL", "postgresql://production.example/test"), self.assertRaises(ValueError):
            soak._check_boundary("resume_probe_" + "a" * 32)
        with patch.object(soak.mixed.config, "DATABASE_URL", "postgresql://localhost/test"), self.assertRaises(ValueError):
            soak._check_boundary("public")

    def test_storage_budget_checks_both_volumes_and_retains_low_watermark(self):
        gib = 1024**3
        with tempfile.TemporaryDirectory(prefix="career-soak-disk-") as directory:
            budget = soak.StorageBudget({"evidence": directory, "postgresql_data": directory}, 2)
            with patch.object(soak.shutil, "disk_usage", side_effect=[
                SimpleNamespace(total=10*gib, free=4*gib), SimpleNamespace(total=10*gib, free=3*gib),
                SimpleNamespace(total=10*gib, free=4*gib), SimpleNamespace(total=10*gib, free=gib)]):
                self.assertEqual(len(budget.check()["latest"]), 2)
                with self.assertRaisesRegex(RuntimeError, "postgresql_data"):
                    budget.check()
            self.assertEqual(budget.report()["minimum_observed_free_bytes"]["postgresql_data"], gib)

    def test_failure_report_preserves_metrics_cleanup_and_original_error_when_stream_fails(self):
        stats = soak.StreamingMeasurements()
        stats.timing(stats.requests, "mixed/read", 7)
        stats.status["mixed/read"]["200"] += 1
        args = soak.parser().parse_args([])
        fixture = {"schema": "resume_probe_" + "a"*32, "soak_schema_removed": True, "soak_connections_remaining": 0}
        with tempfile.TemporaryDirectory(prefix="career-soak-failure-") as directory:
            output = Path(directory)/"failure.json"
            def broken_stream(kind, data):
                raise OSError("intentional stream failure")
            with patch.object(soak.sys, "stderr", new_callable=io.StringIO) as stderr:
                soak._persist_failure(args, stats, None, fixture, {"source": "hash"}, output.with_suffix(".jsonl"),
                    RuntimeError("original monitor failure"), broken_stream, output)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["message"], "original monitor failure")
            self.assertEqual(result["metrics"]["histograms"]["requests"]["mixed/read"]["count"], 1)
            self.assertEqual(result["metrics"]["statuses"]["mixed/read"], {"200": 1})
            self.assertTrue(result["schema_removed"])
            self.assertEqual(result["owned_connections_remaining"], 0)
            self.assertIn("SOAK FAILURE EVIDENCE WRITE FAILED", stderr.getvalue())
            self.assertFalse(output.with_suffix(".json.tmp").exists())

    @unittest.skipUnless(os.environ.get("RUN_LOCAL_PG_CAREER_SOAK_PROBE") == "1", "opt-in local PG cleanup regression")
    def test_failed_exercise_still_writes_verified_cleanup_event(self):
        with tempfile.TemporaryDirectory(prefix="career-soak-cleanup-") as directory:
            args = soak.parser().parse_args(["--duration", "1", "--users", "10", "--output", str(Path(directory)/"failure.json")])
            with patch.object(soak.mixed, "exercise", AsyncMock(side_effect=RuntimeError("intentional exercise failure"))), \
                    self.assertRaisesRegex(RuntimeError, "intentional exercise failure"):
                soak.run(args)
            lines = [json.loads(line) for line in Path(directory, "failure.buckets.jsonl").read_text(encoding="utf-8").splitlines()]
            cleanup = [line for line in lines if line["kind"] == "cleanup"]
            self.assertEqual(len(cleanup), 1)
            self.assertTrue(cleanup[0]["schema_removed"])
            self.assertEqual(cleanup[0]["owned_connections_remaining"], 0)
            failure = json.loads(Path(directory, "failure.json").read_text(encoding="utf-8"))
            self.assertFalse(failure["ok"])
            self.assertEqual(failure["error"]["message"], "intentional exercise failure")
            self.assertTrue(failure["schema_removed"])
            self.assertTrue(failure["worker_stopped"])
            self.assertEqual(failure["owned_connections_remaining"], 0)

    @unittest.skipUnless(os.environ.get("RUN_LOCAL_PG_CAREER_SOAK_PROBE") == "1", "opt-in owned worker/controller failure regression")
    def test_real_owned_worker_and_schema_cleaned_after_monitor_and_bucket_failures(self):
        # No route traffic or Office. Start the real isolated worker and reminder
        # only, then fail the supervisor and verify both stop before patches exit.
        for bucket_failure in (False, True):
            with self.subTest(bucket_failure=bucket_failure), tempfile.TemporaryDirectory(prefix="career-soak-supervision-") as directory:
                args = soak.parser().parse_args(["--duration", "1", "--users", "10", "--bucket-seconds", "1",
                                                "--output", str(Path(directory)/"failure.json")])
                admissions = []; observed = {}
                original_init = soak.WorkerController.__init__
                def initialize(controller, *positional, **keywords):
                    original_init(controller, *positional, **keywords)
                    observed["controller"] = controller
                    if bucket_failure:
                        original_write = controller.bucket_writer
                        def write(kind, data):
                            if kind == "bucket":
                                raise OSError("intentional real bucket write failure")
                            return original_write(kind, data)
                        controller.bucket_writer = write
                async def exercise(fixture, connect, pool, stats, config, reminder):
                    observed["reminder"] = reminder
                    soak.mixed.worker.start_student_career_job_workers()
                    reminder.start()
                    try:
                        while True:
                            admissions.append(time.monotonic())
                            await asyncio.sleep(.01)
                    finally:
                        await soak.mixed.worker.stop_student_career_job_workers()
                async def broken_monitor(controller):
                    await asyncio.sleep(.3)
                    raise RuntimeError("intentional real controller failure")
                from contextlib import ExitStack
                with ExitStack() as stack:
                    stack.enter_context(patch.object(soak.WorkerController, "__init__", initialize))
                    stack.enter_context(patch.object(soak.mixed, "exercise", exercise))
                    if not bucket_failure:
                        stack.enter_context(patch.object(soak.WorkerController, "monitor", broken_monitor))
                    with self.assertRaisesRegex(OSError if bucket_failure else RuntimeError, "intentional real"):
                        soak.run(args)
                report = json.loads(Path(directory, "failure.json").read_text(encoding="utf-8"))
                self.assertTrue(report["schema_removed"])
                self.assertEqual(report["owned_connections_remaining"], 0)
                self.assertTrue(report["worker_stopped"])
                self.assertIsNone(observed["controller"].process)
                self.assertFalse(observed["reminder"].thread.is_alive())
                self.assertLess(admissions[-1]-admissions[0], 3)
                count = len(admissions)
                time.sleep(.03)
                self.assertEqual(len(admissions), count)


class SoakSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def assert_stops_admission(self, *, bucket_failure=False):
        args = soak.parser().parse_args([])
        args.bucket_seconds = .01
        stats = soak.StreamingMeasurements()
        stats.timing(stats.requests, "mixed/read", 7)
        stats.status["mixed/read"]["200"] += 1
        stats.resources.append({"elapsed_s": 1, "app_and_generator_rss_mb": 100})
        reminder = type("Reminder", (), {"finish": AsyncMock(return_value={"ok": True})})()
        writes = []
        def write(kind, data):
            writes.append(kind)
            if bucket_failure:
                raise OSError("intentional bucket write failure")
        controller = soak.WorkerController("resume_probe_" + "a"*32, None, stats, args, write)
        admissions = []
        async def exercise(*unused):
            controller.start()
            try:
                while True:
                    admissions.append(time.monotonic())
                    await asyncio.sleep(.005)
            finally:
                # This raises on a failed monitor, exactly as the real worker
                # hook does. The wrapper must still finish the reminder.
                await controller.stop()
        async def broken_monitor():
            await asyncio.sleep(.02)
            raise RuntimeError("intentional controller failure")
        patches = [patch.object(controller, "_spawn"), patch.object(controller, "close_owned_worker"),
                   patch.object(controller, "database_snapshot", return_value={"jobs": [], "scope_connections": 0, "canaries": []}),
                   patch.object(soak.mixed, "exercise", exercise)]
        if not bucket_failure:
            patches.append(patch.object(controller, "monitor", broken_monitor))
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            started = time.monotonic()
            error = OSError if bucket_failure else RuntimeError
            with self.assertRaisesRegex(error, "intentional"):
                await asyncio.wait_for(soak._supervised_exercise({}, None, None, stats, args, reminder, controller), 2)
            self.assertLess(time.monotonic()-started, 1)
            self.assertIsInstance(controller.monitor_failure, error)
            self.assertTrue(controller.exercise_task.done())
            self.assertTrue(controller.task.done())
            count = len(admissions)
            await asyncio.sleep(.03)
            self.assertEqual(len(admissions), count)
            reminder.finish.assert_awaited_once()
            self.assertGreaterEqual(controller.close_owned_worker.call_count, 1)
            if bucket_failure:
                self.assertEqual(writes, ["bucket"])
                report = soak._failure_report(args, stats, controller, None, {}, Path("unused.jsonl"), controller.monitor_failure)
                self.assertEqual(report["unwritten_bucket"]["metrics"]["requests"]["mixed/read"]["count"], 1)
                self.assertEqual(report["unwritten_bucket"]["metrics"]["resources_last_300"][0]["app_and_generator_rss_mb"], 100)
                self.assertEqual(report["metrics"]["statuses"]["mixed/read"], {"200": 1})

    async def test_controller_failure_immediately_stops_admission_and_finishes_reminder(self):
        await self.assert_stops_admission()

    async def test_bucket_write_failure_immediately_stops_admission_and_finishes_reminder(self):
        await self.assert_stops_admission(bucket_failure=True)


def provider_evidence():
    rows = []; attempts = []
    for job_id, case in enumerate(soak.PROVIDER_CASES, 1):
        count = 3 if case == "invalid_exhausted" else (1 if case == "slow_success" else 2)
        row = {"id": job_id, "job_id": job_id, "round_no": 1, "case_name": case,
            "status": "dead_letter" if case == "invalid_exhausted" else "succeeded", "attempt_count": count,
            "writes": 0 if case == "invalid_exhausted" else 1, "failure_writes": 1 if case == "invalid_exhausted" else 0,
            "failure_code": "ValueError" if case == "invalid_exhausted" else "", "last_error_code": "ValueError" if case == "invalid_exhausted" else "",
            "last_error": "Synthetic invalid response" if case == "invalid_exhausted" else "",
            "slow_seconds": 35, "timeout_seconds": 45, "retry_after_seconds": 30, "capacity_reserved_until": None}
        start = datetime(2026, 9, 6, 10)
        code = {"timeout_recovery": "TimeoutError", "rate_limit_recovery": "HTTPStatusError",
            "unavailable_recovery": "HTTPStatusError", "disconnect_recovery": "ConnectError",
            "invalid_recovery": "ValueError", "invalid_exhausted": "ValueError"}.get(case, "")
        for number in range(1, count + 1):
            failed = number < count or case == "invalid_exhausted"
            finish = start + timedelta(seconds=35 if case == "slow_success" else 45 if case == "timeout_recovery" and number == 1 else 1)
            attempts.append({"job_id": job_id, "attempt_no": number, "stage": "execute", "status": "error" if failed else "success",
                "error_code": code if failed else "", "error_message": "Synthetic provider failure" if failed else "",
                "started_at": start.isoformat(), "finished_at": finish.isoformat()})
            wait = (15, 60, 180)[number - 1] + job_id % 7
            if case == "timeout_recovery":
                wait = max(wait, soak.mixed.registry.UPSTREAM_COOLDOWN_SECONDS)
                if number == 1:
                    row["capacity_reserved_until"] = (finish + timedelta(seconds=wait)).isoformat()
            if case == "rate_limit_recovery":
                wait = max(wait, row["retry_after_seconds"])
            start = finish + timedelta(seconds=wait)
        rows.append(row)
    return rows, attempts


class SoakProviderAuditTests(unittest.TestCase):
    def test_all_seven_cases_require_real_attempt_errors_delays_and_exact_terminal_effect(self):
        rows, attempts = provider_evidence()
        result = soak._provider_audit(rows, attempts, required=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["complete_case_coverage"])
        self.assertEqual(set(result["case_counts"]), set(soak.PROVIDER_CASES))
        self.assertEqual(len(result["jobs"]), 7)

    def test_early_retry_missing_timeout_reservation_and_unexpected_failure_cannot_pass(self):
        original_rows, original_attempts = provider_evidence()
        for mutation in ("early_429", "missing_cooldown", "duplicate_publish", "unexpected_dead_letter", "missing_error_reason"):
            rows, attempts = copy.deepcopy(original_rows), copy.deepcopy(original_attempts)
            if mutation == "early_429":
                job_id = next(row["job_id"] for row in rows if row["case_name"] == "rate_limit_recovery")
                history = [item for item in attempts if item["job_id"] == job_id]
                history[1]["started_at"] = history[0]["finished_at"]
            elif mutation == "missing_cooldown":
                next(row for row in rows if row["case_name"] == "timeout_recovery")["capacity_reserved_until"] = None
            elif mutation == "duplicate_publish":
                rows[0]["writes"] = 2
            elif mutation == "unexpected_dead_letter":
                rows[0]["status"] = "dead_letter"
            else:
                next(item for item in attempts if item["status"] == "error")["error_message"] = ""
            with self.subTest(mutation=mutation):
                self.assertFalse(soak._provider_audit(rows, attempts, required=True)["ok"])

    def test_success_stubs_alone_do_not_claim_provider_coverage(self):
        result = soak._provider_audit([], [], required=True)
        self.assertFalse(result["ok"])
        self.assertFalse(result["complete_case_coverage"])

    def test_provider_long_runs_require_slow_natural_timing_and_bounded_rounds(self):
        args = soak.parser().parse_args(["--duration", "86400", "--allow-long-run", "--provider-faults"])
        soak.validate_args(args)
        for key, value in (("provider_faults", False), ("fast_fault_recovery", True),
                           ("provider_slow_seconds", 2), ("provider_max_rounds", 25), ("provider_fault_interval", 60)):
            changed = copy.deepcopy(args); setattr(changed, key, value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                soak.validate_args(changed)


class SoakProviderExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_faults_use_actual_httpx_exception_contract_and_next_attempt_recovers(self):
        for case, expected, status in (("rate_limit_recovery", soak.httpx.HTTPStatusError, 429),
            ("unavailable_recovery", soak.httpx.HTTPStatusError, 503), ("disconnect_recovery", soak.httpx.ConnectError, None)):
            payload = {"case": case, "retry_after_seconds": 30}
            with self.subTest(case=case), self.assertRaises(expected) as caught:
                await soak._execute_provider({"attempt_count": 1}, payload)
            if status:
                self.assertEqual(caught.exception.response.status_code, status)
            if status == 429:
                self.assertEqual(caught.exception.response.headers["Retry-After"], "30")
            self.assertFalse(soak.mixed.worker._safe_failure(caught.exception)[2])
            self.assertEqual(await soak._execute_provider({"attempt_count": 2}, payload), {"synthetic_only": True, "case": case})

    async def test_invalid_payload_reaches_worker_type_validator_and_persistent_case_never_self_recovers(self):
        for case in ("invalid_recovery", "invalid_exhausted"):
            self.assertIsInstance(await soak._execute_provider({"attempt_count": 1}, {"case": case}), list)
        self.assertIsInstance(await soak._execute_provider({"attempt_count": 3}, {"case": "invalid_exhausted"}), list)
        self.assertIsInstance(await soak._execute_provider({"attempt_count": 2}, {"case": "invalid_recovery"}), dict)

    async def test_slow_wait_is_explicit_and_timeout_allows_asyncio_cancellation(self):
        with patch.object(soak.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await soak._execute_provider({"attempt_count": 1}, {"case": "slow_success", "slow_seconds": 35})
            sleep.assert_awaited_once_with(35)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(soak._execute_provider({"attempt_count": 1},
                {"case": "timeout_recovery", "timeout_seconds": 45}), timeout=.02)


if __name__ == "__main__":
    unittest.main()
