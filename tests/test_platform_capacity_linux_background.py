"""Offline Linux BG protocol/lifecycle checks; no container or PG connections."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from tools import platform_capacity_linux_background as bg


class LinuxBackgroundIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linux-bg-protocol-")
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.fixture = {"kind": "capacity-linux-server-fixture", "synthetic": True, "uiV3Synthetic": True,
                        "lab_nonce": "owned-nonce", "classOfferingId": 12, "classId": 13,
                        "teacher": {"id": 4, "email": "teacher@example.invalid"},
                        "users": [{"identifier": f"CAPACITY-{i}", "student_id": i + 100} for i in range(200)],
                        "password": "not-for-reports"}
        self.identity = {"lab_nonce": "owned-nonce", "compose_project": "owned-project",
                         "pg_system_identifier": "1234567", "artifact_root": self.runtime,
                         "fixture": self.fixture, "marker": {"database": {"password": "never-export"}}}
        self.write_fixture()
        self.write_manifest()

    def write_fixture(self):
        raw = json.dumps(self.fixture).encode()
        (self.runtime / "server-fixture.json").write_bytes(raw)
        self.identity["fixture_sha256"] = hashlib.sha256(raw).hexdigest()

    def write_manifest(self):
        _, _, students, binding = bg._identity_binding(self.identity)
        self.manifest = {"run_id": "owned-run", "lab_identity": binding, "student_ids": students,
                         "class_offering_id": 12, "fixture_runtime": str(self.runtime.resolve()),
                         "duration": 10, "dirty_every_seconds": 1,
                         "scheduled": [{"id": 1, "kind": "todo_due_reminder"},
                                       {"id": 2, "kind": "assignment_due_reminder"},
                                       {"id": 3, "kind": bg.background.ELASTIC_KIND}]}
        (self.runtime / bg.background.MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")
        self.state = {"run_id": self.manifest["run_id"], "lab_identity": binding, "status": "stopped"}
        self.state["dirty_producer"] = {"status": "stopped", "final_pending": 0,
            "pending_state": "none", "committed_batches": 1, "scope": "process_segment",
            "segment_started_at": "synthetic-segment-1", "deferred_cycles": 0}
        (self.runtime / bg.STATE).write_text(json.dumps(self.state), encoding="utf-8")
        pressure = self.state["dirty_producer"]
        self.events = [{"run_id": "owned-run", "event": event, "pressure": dict(pressure)} for event in (
            "projection_dirty_producer_started", "projection_dirty_batch_committed",
            "projection_dirty_producer_progress", "projection_dirty_producer_stopped")]
        (self.runtime / bg.background.EVENTS).write_text("".join(json.dumps(event) + "\n" for event in self.events), encoding="utf-8")
        self.report = {"run_id": "owned-run", "scheduled_task_facts": [
            {**item, "status": "done"} for item in self.manifest["scheduled"]]}

    def test_binding_and_report_never_export_secret_marker_or_fixture(self):
        with patch.object(bg.background, "_report_validated_background", return_value=self.report) as report:
            result = bg.report_lab_background(self.identity)
        report.assert_called_once_with(self.runtime)
        text = json.dumps(result)
        self.assertIn("linux-compose-lab-v1", text)
        self.assertNotIn("never-export", text)
        self.assertNotIn("not-for-reports", text)
        self.assertEqual("scheduled_task_dispatcher.main", result["dispatcher_entrypoint"])
        self.assertTrue(result["finite_task_verification"]["all_present_and_done"])
        self.assertTrue(result["worker_identity_matches_run"])

    def test_missing_elastic_job_cannot_pass_via_remaining_aggregate_done_counts(self):
        self.report["scheduled_task_facts"].pop()
        result = bg._background_integrity(self.manifest, self.report, self.state)
        self.assertEqual([3], result["finite_task_verification"]["missing_task_ids"])
        self.assertFalse(result["finite_task_verification"]["all_present_and_done"])

    def test_retry_or_replaced_job_and_foreign_worker_run_fail_integrity(self):
        self.report["scheduled_task_facts"][0]["status"] = "pending"
        self.report["scheduled_task_facts"][1]["kind"] = bg.background.ELASTIC_KIND
        self.state["run_id"] = "earlier-run"
        result = bg._background_integrity(self.manifest, self.report, self.state)
        self.assertEqual([1], [item["id"] for item in result["finite_task_verification"]["unfinished_tasks"]])
        self.assertEqual([2], result["finite_task_verification"]["mismatched_task_kind_ids"])
        self.assertFalse(result["worker_identity_matches_run"])
        self.state["run_id"] = "owned-run"
        self.state["lab_identity"] = {**self.state["lab_identity"], "lab_nonce": "another-lab"}
        self.assertFalse(bg._background_integrity(self.manifest, self.report, self.state)["worker_identity_matches_run"])

    def test_duplicate_manifest_ids_cannot_hide_missing_expected_work(self):
        self.manifest["scheduled"].append(self.manifest["scheduled"][0])
        with self.assertRaises(ValueError):
            bg._background_integrity(self.manifest, self.report, self.state)

    def test_changed_fixture_bytes_or_nonce_fail_before_report_db_work(self):
        with patch.object(bg.background, "_report_validated_background") as report:
            (self.runtime / "server-fixture.json").write_text(json.dumps({**self.fixture, "lab_nonce": "other"}))
            with self.assertRaises(RuntimeError):
                bg.report_lab_background(self.identity)
        report.assert_not_called()

    def test_duplicate_or_non_200_students_cannot_seed_background(self):
        for users in (self.fixture["users"][:-1], self.fixture["users"][:-1] + [self.fixture["users"][0]]):
            with self.subTest(count=len(users)):
                self.fixture["users"] = users
                self.write_fixture()
                with self.assertRaises(RuntimeError):
                    bg.seed_lab_background(self.identity)

    def test_manifest_replay_from_other_instance_or_offering_or_students_is_rejected(self):
        for values in ({"lab_identity": {**self.manifest["lab_identity"], "pg_system_identifier": "different"}},
                       {"class_offering_id": 99}, {"student_ids": list(reversed(self.manifest["student_ids"]))}):
            with self.subTest(field=next(iter(values))):
                (self.runtime / bg.background.MANIFEST).write_text(json.dumps({**self.manifest, **values}))
                with patch.object(bg.background, "_report_validated_background") as report:
                    with self.assertRaises(RuntimeError):
                        bg.report_lab_background(self.identity)
                    report.assert_not_called()

    def test_cli_calls_strict_boundary_before_worker_and_has_no_dsn_option(self):
        calls = []
        helper = types.ModuleType("tools.platform_capacity_linux_lab")
        def boundary(path, *, require_seeded):
            calls.append(("boundary", path, require_seeded))
            return self.identity
        helper.validate_container_boundary = boundary
        with patch.dict(sys.modules, {helper.__name__: helper}), patch.object(sys, "argv", ["bg", "worker"]), \
                patch.object(bg, "run_lab_worker", side_effect=lambda identity: calls.append(("worker", identity))):
            bg.main()
        self.assertEqual(("boundary", Path("/lab/lab-marker.json"), True), calls[0])
        self.assertIs(self.identity, calls[1][1])
        with patch.object(sys, "argv", ["bg", "worker", "--lab-marker", "/other/marker.json"]), \
                patch.object(bg, "run_lab_worker") as worker:
            with self.assertRaises(SystemExit):
                bg.main()
            worker.assert_not_called()

    def test_real_dispatcher_main_owns_signal_and_producer_drain_then_pool_close(self):
        import scheduled_task_dispatcher as dispatcher
        from classroom_app.db import postgres
        order = []
        async def original(args, *, stop):
            order.append("dispatcher")
            asyncio.get_running_loop().call_soon(signal.getsignal(signal.SIGTERM), signal.SIGTERM, None)
            try:
                await asyncio.Future()
            finally:
                order.append("workers_drained")
        async def producer(manifest, runtime, shutdown, stop, dirty_batch, *, record=None):
            await shutdown.wait()
            order.append("producer_drained")
        with patch.object(dispatcher, "_run", original), patch.object(bg, "_dirty_producer", producer), \
                patch.object(postgres, "close_connection_pool", side_effect=lambda: order.append("pool_closed")):
            bg.run_lab_worker(self.identity)
        self.assertEqual("dispatcher", order[0])
        self.assertEqual("pool_closed", order[-1])
        self.assertIn("workers_drained", order)
        self.assertIn("producer_drained", order)
        state = json.loads((self.runtime / bg.STATE).read_text())
        self.assertEqual("stopped", state["status"])
        self.assertEqual([], state["projection_measurement_errors"])


class LinuxBackgroundProducerTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_waits_for_real_inflight_producer_thread(self):
        entered, release = threading.Event(), threading.Event()
        effects = []
        def transaction(index):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test release missing")
            effects.append(index)
        async def original(args, *, stop):
            try:
                await asyncio.Future()
            finally:
                effects.append("real_dispatcher_cleanup")
        with tempfile.TemporaryDirectory(prefix="linux-bg-drain-") as folder:
            manifest = {"duration": 10, "dirty_every_seconds": 1}
            async def producer(shutdown, stop):
                await bg._dirty_producer(manifest, Path(folder), shutdown, stop, transaction)
            task = asyncio.create_task(bg._run_with_producer(original, argparse.Namespace(), {}, producer))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(.03)
                self.assertFalse(task.done(), "shutdown lost the in-flight transaction")
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIn(0, effects)
        self.assertIn("real_dispatcher_cleanup", effects)

    async def test_intake_marker_or_requested_stop_prevents_new_dirty_transaction(self):
        for requested in (False, True):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory(prefix="linux-bg-stop-") as folder:
                runtime = Path(folder)
                if not requested:
                    (runtime / bg.INTAKE_STOP).touch()
                shutdown = asyncio.Event()
                transaction = Mock()
                task = asyncio.create_task(bg._dirty_producer(
                    {"duration": 10, "dirty_every_seconds": 1}, runtime, shutdown, {"requested": requested}, transaction))
                await asyncio.sleep(.01)
                shutdown.set()
                await task
                transaction.assert_not_called()

    async def test_producer_failure_stops_and_drains_actual_dispatcher(self):
        cleaned = asyncio.Event()
        async def original(args, *, stop):
            try:
                await asyncio.Future()
            finally:
                cleaned.set()
        async def producer(shutdown, stop):
            await asyncio.sleep(0)
            raise RuntimeError("controlled measurement failure")
        with self.assertRaisesRegex(RuntimeError, "controlled measurement failure"):
            await bg._run_with_producer(original, argparse.Namespace(), {}, producer)
        self.assertTrue(cleaned.is_set())


if __name__ == "__main__":
    unittest.main()
