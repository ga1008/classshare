"""Synthetic transaction fault controls; no database or network connections."""
import asyncio
from collections import Counter
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools import platform_capacity_linux_background as bg
from tests import test_platform_capacity_linux_background as support


class Conflict(Exception):
    def __init__(self, code):
        self.sqlstate = code
        super().__init__("synthetic server rejection")


class Store:
    def __init__(self, plans=()):
        self.plans = list(plans)
        self.connections = []
        self.versions = Counter()
        self.events = []
        self.commits = 0
        self.checkout_budgets = []

    def connect(self, *, checkout_timeout):
        self.checkout_budgets.append(checkout_timeout)
        plan = self.plans[len(self.connections)] if len(self.connections) < len(self.plans) else {}
        owner = self

        class Connection:
            def __init__(self):
                self.selected = []
                self.rolled_back = False
                self.closed = False

            def execute(self, statement, params=()):
                if statement.startswith("SELECT student_id"):
                    rows = [{"student_id": student, "dirty_version": owner.versions[student] + 1}
                            for student in self.selected]
                    return type("Rows", (), {"fetchall": lambda _self: rows})()
                return None  # Bounded local timeout SQL has no mutation.

            def commit(self):
                if plan.get("commit_conflict"):
                    raise Conflict(plan["commit_conflict"])
                owner.versions.update(self.selected)
                owner.commits += 1
                if plan.get("unknown_commit"):
                    raise ConnectionError("synthetic committed response lost")

            def rollback(self):
                if plan.get("rollback_failure"):
                    raise ConnectionError("synthetic rollback not confirmed")
                self.selected.clear()
                self.rolled_back = True

            def close(self):
                self.closed = True

        conn = Connection()
        conn.plan = plan
        self.connections.append(conn)
        return conn

    def mark(self, conn, offering, student, **kwargs):
        conn.selected.append(student)
        if len(conn.selected) == 5 and conn.plan.get("body_conflict"):
            callback = conn.plan.get("on_conflict")
            if callback:
                callback()
            raise Conflict(conn.plan["body_conflict"])

    def record(self, name, **values):
        self.events.append((name, values))


class DirtyBatchRetryTests(unittest.TestCase):
    manifest = {"student_ids": list(range(1, 201)), "class_offering_id": 12}

    def run_batch(self, store, record=None):
        with patch.object(bg.time, "sleep"):
            bg._apply_dirty_batch(7, self.manifest, store.connect, store.mark, record or store.record)

    def test_known_aborts_replay_same_selection_once_after_explicit_rollback(self):
        for plan in ({"body_conflict": code} for code in ("55P03", "40P01", "40001")):
            with self.subTest(plan=plan):
                store = Store([plan, {}])
                self.run_batch(store)
                self.assertEqual(Counter({student: 1 for student in range(71, 81)}), store.versions)
                self.assertEqual(1, store.commits)
                self.assertTrue(store.connections[0].rolled_back)
                self.assertTrue(all(conn.closed for conn in store.connections))
                self.assertTrue(all(0 < value <= 10 for value in store.checkout_budgets))
                retries = [values for event, values in store.events if event == "projection_dirty_conflict"]
                self.assertEqual((71, 72, 73, 74, 75, 76, 77, 78, 79, 80), retries[0]["student_ids"])
                self.assertEqual(7, retries[0]["batch_index"])
                self.assertEqual(10, sum(name == "projection_dirty" for name, _ in store.events))

    def test_server_serialization_rejection_at_commit_can_retry(self):
        store = Store([{"commit_conflict": "40001"}, {}])
        self.run_batch(store)
        self.assertEqual(1, store.commits)
        self.assertEqual(10, sum(store.versions.values()))

    def test_retry_exhaustion_preserves_failure_without_committing_partial_students(self):
        store = Store([{"body_conflict": "55P03"}] * 5)
        with self.assertRaises(bg._RolledBackDirtyConflict):
            self.run_batch(store)
        self.assertEqual(5, len(store.connections))
        self.assertEqual(0, store.commits)
        self.assertEqual({}, store.versions)
        self.assertFalse(store.events[-1][1]["will_retry"])

    def test_elapsed_budget_stops_retry_before_attempt_limit(self):
        now = [0.0]
        store = Store([{"body_conflict": "55P03", "on_conflict": lambda: now.__setitem__(0, 11.0)}])
        with patch.object(bg.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(bg._RolledBackDirtyConflict):
                self.run_batch(store)
        self.assertEqual(1, len(store.connections))
        self.assertFalse(store.events[-1][1]["will_retry"])

    def test_unknown_commit_is_not_replayed_even_after_successful_cleanup_rollback(self):
        store = Store([{"unknown_commit": True}])
        with self.assertRaises(ConnectionError):
            self.run_batch(store)
        self.assertEqual(1, len(store.connections))
        self.assertEqual(1, store.commits)
        self.assertEqual(10, sum(store.versions.values()))
        self.assertTrue(store.events[-1][1]["commit_outcome_unknown"])

    def test_permanent_error_and_failed_rollback_are_not_retried(self):
        for plan, error in (({"body_conflict": "23505"}, Conflict),
                            ({"body_conflict": "55P03", "rollback_failure": True}, ConnectionError)):
            with self.subTest(plan=plan):
                store = Store([plan])
                with self.assertRaises(error):
                    self.run_batch(store)
                self.assertEqual(1, len(store.connections))
                self.assertEqual({}, store.versions)
                self.assertTrue(store.connections[0].closed)

    def test_committed_audit_failure_never_duplicates_dirty_generations(self):
        store = Store()
        def failed_audit(name, **values):
            if name == "projection_dirty_batch_committed":
                raise OSError("synthetic full audit filesystem")
        with self.assertRaises(OSError):
            self.run_batch(store, failed_audit)
        self.assertEqual(1, len(store.connections))
        self.assertEqual(1, store.commits)
        self.assertEqual(10, sum(store.versions.values()))


class DirtyProducerReadyAndStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_waits_for_signal_and_obeys_stop_and_timeout(self):
        with tempfile.TemporaryDirectory(prefix="dirty-ready-") as folder:
            ready, shutdown = threading.Event(), asyncio.Event()
            waiter = asyncio.create_task(bg._wait_dispatcher_ready(ready, shutdown, {}, Path(folder)))
            await asyncio.sleep(.01)
            self.assertFalse(waiter.done())
            ready.set()
            await asyncio.wait_for(waiter, 1)
            ready.clear()
            shutdown.set()
            await asyncio.wait_for(bg._wait_dispatcher_ready(ready, shutdown, {}, Path(folder)), 1)
            self.assertFalse(ready.is_set())
            shutdown.clear()
            with patch.object(bg, "DISPATCHER_READY_SECONDS", .001):
                with self.assertRaises(TimeoutError):
                    await bg._wait_dispatcher_ready(ready, shutdown, {}, Path(folder))

    async def test_stop_drains_accepted_retry_batch_without_starting_next_index(self):
        retry_entered, release = threading.Event(), threading.Event()
        store = Store([{"body_conflict": "55P03"}, {}])
        indices = []
        def record(name, **values):
            store.record(name, **values)
            if name == "projection_dirty_conflict":
                retry_entered.set()
                if not release.wait(3):
                    raise AssertionError("synthetic stop test did not release")
        manifest = {**DirtyBatchRetryTests.manifest, "duration": 10, "dirty_every_seconds": .01}
        def batch(index):
            indices.append(index)
            bg._apply_dirty_batch(index, manifest, store.connect, store.mark, record)
        async def dispatcher(args, *, stop):
            await asyncio.Future()
        with tempfile.TemporaryDirectory(prefix="dirty-stop-") as folder:
            async def producer(shutdown, stop):
                await bg._dirty_producer(manifest, Path(folder), shutdown, stop, batch)
            task = asyncio.create_task(bg._run_with_producer(dispatcher, None, {}, producer))
            try:
                self.assertTrue(await asyncio.to_thread(retry_entered.wait, 2))
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual([0], indices)
        self.assertEqual(1, store.commits)
        self.assertEqual(Counter({student: 1 for student in range(1, 11)}), store.versions)


class DispatcherReadinessIntegrationTests(unittest.TestCase):
    setUp = support.LinuxBackgroundIdentityTests.setUp
    write_fixture = support.LinuxBackgroundIdentityTests.write_fixture
    write_manifest = support.LinuxBackgroundIdentityTests.write_manifest

    def test_pressure_starts_only_after_real_heartbeat_callback_returns(self):
        import json
        import scheduled_task_dispatcher as dispatcher
        from classroom_app.db import postgres
        from classroom_app.services import scheduled_task_service as scheduler
        order = []
        def heartbeat(worker_id, *, status, last_error=""):
            order.append("heartbeat_committed")
        async def original(args, *, stop):
            await asyncio.to_thread(scheduler.update_scheduler_heartbeat, "capacity-linux-background", status="running")
            await asyncio.sleep(.07)
        async def producer(manifest, runtime, shutdown, stop, dirty_batch):
            state = json.loads((runtime / bg.STATE).read_text())
            self.assertTrue(state["dispatcher_ready"])
            order.append("producer")
            await shutdown.wait()
        with patch.object(dispatcher, "_run", original), patch.object(scheduler, "update_scheduler_heartbeat", heartbeat), \
                patch.object(bg, "_dirty_producer", producer), patch.object(postgres, "close_connection_pool"):
            bg.run_lab_worker(self.identity)
        self.assertEqual(["heartbeat_committed", "producer"], order)

    def test_failed_heartbeat_never_marks_ready_or_injects_dirty_work(self):
        import json
        import scheduled_task_dispatcher as dispatcher
        from classroom_app.db import postgres
        from classroom_app.services import scheduled_task_service as scheduler
        from classroom_app.services import learning_progress_service as progress
        async def original(args, *, stop):
            await asyncio.to_thread(scheduler.update_scheduler_heartbeat, "capacity-linux-background", status="running")
        with patch.object(dispatcher, "_run", original), patch.object(scheduler, "update_scheduler_heartbeat",
                side_effect=ConnectionError("synthetic heartbeat commit failed")), \
                patch.object(progress, "mark_student_learning_progress_dirty") as mark, \
                patch.object(postgres, "close_connection_pool"):
            with self.assertRaises(ConnectionError):
                bg.run_lab_worker(self.identity)
        mark.assert_not_called()
        state = json.loads((self.runtime / bg.STATE).read_text())
        self.assertFalse(state["dispatcher_ready"])
        self.assertEqual("error", state["status"])


if __name__ == "__main__":
    unittest.main()
