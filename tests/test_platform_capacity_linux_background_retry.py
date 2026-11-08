"""Synthetic transaction fault controls; no database or network connections."""
import asyncio
from collections import Counter
import json
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
                if plan.get("close_failure"):
                    raise ConnectionError("synthetic close failed")

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
        indices, observations = [], []
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
                await bg._dirty_producer(manifest, Path(folder), shutdown, stop, batch,
                    record=lambda event, **values: observations.append((event, values)))
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
        self.assertEqual("projection_dirty_producer_stopped", observations[-1][0])
        self.assertEqual(0, observations[-1][1]["pressure"]["final_pending"])
        self.assertEqual(1, observations[-1][1]["pressure"]["committed_batches"])


class DirtyProducerDeferredTests(unittest.IsolatedAsyncioTestCase):
    manifest = {**DirtyBatchRetryTests.manifest, "duration": 10, "dirty_every_seconds": .01}

    async def test_exhaustion_defers_same_batch_and_dispatcher_stays_alive_until_one_commit(self):
        store = Store([{"body_conflict": "55P03"}] * 5 + [{}])
        stop, indices, pauses, observations = {}, [], [], []
        stopped, dispatcher_closed = asyncio.Event(), asyncio.Event()
        active, max_active, thread_ids, producer_ids, live_task_sets = 0, 0, set(), set(), []
        def batch(index):
            nonlocal active, max_active
            active += 1
            max_active = max(active, max_active)
            thread_ids.add(threading.get_ident())
            indices.append(index)
            try:
                bg._apply_dirty_batch(index, self.manifest, store.connect, store.mark, store.record)
            finally:
                active -= 1
        def record(event, **values):
            observations.append((event, values))
            producer_ids.add(id(asyncio.current_task()))
            if event in {"projection_dirty_deferred", "projection_dirty_producer_progress"}:
                self.assertFalse(dispatcher_closed.is_set())
                live_task_sets.append({id(task) for task in asyncio.all_tasks()})
            if event == "projection_dirty_producer_progress":
                stop["requested"] = True
            if event == "projection_dirty_producer_stopped":
                stopped.set()
        async def pause(seconds, *_args):
            pauses.append(seconds)
            await asyncio.sleep(0)
        async def dispatcher(args, *, stop):
            try:
                await stopped.wait()
            finally:
                dispatcher_closed.set()
        with tempfile.TemporaryDirectory(prefix="dirty-deferred-") as folder:
            async def producer(shutdown, current_stop):
                await bg._dirty_producer(self.manifest, Path(folder), shutdown, current_stop, batch, record=record)
            with patch.object(bg.time, "sleep"), patch.object(bg, "_producer_pause", pause):
                await asyncio.wait_for(bg._run_with_producer(dispatcher, None, stop, producer), 2)
        self.assertEqual([0, 0], indices)
        self.assertEqual([1, .01], pauses)
        self.assertEqual(1, max_active)
        self.assertEqual(1, len(thread_ids))
        self.assertEqual(1, len(producer_ids))
        self.assertEqual(live_task_sets[0], live_task_sets[1])
        self.assertEqual(6, len(store.connections))
        self.assertEqual(1, store.commits)
        self.assertEqual(Counter({student: 1 for student in range(1, 11)}), store.versions)
        deferred = next(values for name, values in observations if name == "projection_dirty_deferred")
        self.assertEqual(tuple(range(1, 11)), deferred["student_ids"])
        self.assertFalse(deferred["accepted"])
        self.assertTrue(deferred["rollback_confirmed"])
        final = observations[-1][1]["pressure"]
        self.assertEqual((1, 1, 0, "none", "process_segment"),
            (final["committed_batches"], final["deferred_cycles"], final["final_pending"],
             final["pending_state"], final["scope"]))
        self.assertTrue(dispatcher_closed.is_set())

    async def test_repeated_deferrals_have_capped_cooldown_and_single_frozen_index(self):
        shutdown, stop, indices, pauses, states = asyncio.Event(), {}, [], [], []
        def batch(index):
            indices.append(index)
            raise bg._RolledBackDirtyConflict("55P03")
        def record(event, **values):
            states.append((event, values))
            if event == "projection_dirty_producer_stopped":
                shutdown.set()
        async def pause(seconds, *_args):
            pauses.append(seconds)
            if len(pauses) == 5:
                stop["requested"] = True
            await asyncio.sleep(0)
        with tempfile.TemporaryDirectory(prefix="dirty-cooldown-") as folder, \
                patch.object(bg, "_producer_pause", pause):
            await bg._dirty_producer(self.manifest, Path(folder), shutdown, stop, batch, record=record)
        self.assertEqual([0] * 5, indices)
        self.assertEqual([1, 2, 4, 5, 5], pauses)
        final = states[-1][1]
        self.assertEqual("stop_requested", final["reason"])
        self.assertEqual((0, 1, "rolled_back"), (final["pressure"]["committed_batches"],
            final["pressure"]["final_pending"], final["pressure"]["pending_state"]))

    async def test_stop_marker_and_deadline_end_deferred_pressure_without_hiding_pending(self):
        for reason in ("intake_stop_marker", "duration_elapsed"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(prefix="dirty-defer-stop-") as folder:
                runtime, shutdown, observations, indices = Path(folder), asyncio.Event(), [], []
                def batch(index):
                    indices.append(index)
                    raise bg._RolledBackDirtyConflict("55P03")
                def record(event, **values):
                    observations.append((event, values))
                    if event == "projection_dirty_deferred" and reason == "intake_stop_marker":
                        (runtime / bg.INTAKE_STOP).touch()
                    if event == "projection_dirty_producer_stopped":
                        shutdown.set()
                manifest = {**self.manifest, "duration": .05 if reason == "duration_elapsed" else 10}
                await asyncio.wait_for(bg._dirty_producer(manifest, runtime, shutdown, {}, batch, record=record), 1)
                final = observations[-1][1]
                self.assertEqual([0], indices)
                self.assertEqual(reason, final["reason"])
                self.assertEqual(1, final["pressure"]["final_pending"])
                self.assertEqual(0, final["pressure"]["committed_batches"])

    async def test_unknown_commit_permanent_rollback_and_close_errors_remain_fatal(self):
        plans = [({"unknown_commit": True}, ConnectionError, 1),
                 ({"body_conflict": "23505"}, Conflict, 0),
                 ({"body_conflict": "55P03", "rollback_failure": True}, ConnectionError, 0),
                 ({"body_conflict": "55P03", "close_failure": True}, ConnectionError, 0)]
        for plan, exception, commits in plans:
            with self.subTest(plan=plan), tempfile.TemporaryDirectory(prefix="dirty-fatal-") as folder:
                store, observations, indices = Store([plan]), [], []
                closed = asyncio.Event()
                def batch(index):
                    indices.append(index)
                    bg._apply_dirty_batch(index, self.manifest, store.connect, store.mark, store.record)
                def record(event, **values):
                    observations.append((event, values))
                async def dispatcher(args, *, stop):
                    try:
                        await asyncio.Future()
                    finally:
                        closed.set()
                async def producer(shutdown, stop):
                    await bg._dirty_producer(self.manifest, Path(folder), shutdown, stop, batch, record=record)
                with self.assertRaises(exception):
                    await asyncio.wait_for(bg._run_with_producer(dispatcher, None, {}, producer), 2)
                self.assertTrue(closed.is_set())
                self.assertEqual([0], indices)
                self.assertEqual(commits, store.commits)
                self.assertEqual(1, len(store.connections))
                self.assertFalse(any(event == "projection_dirty_deferred" for event, _ in observations))
                self.assertEqual("error", observations[-1][1]["pressure"]["status"])

    async def test_audit_failure_during_defer_after_commit_or_final_state_is_fatal_without_replay(self):
        targets = ("projection_dirty_deferred", "projection_dirty_batch_committed",
                   "projection_dirty_producer_stopped")
        for target in targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory(prefix="dirty-audit-") as folder:
                store = Store([{"body_conflict": "55P03"}] * 5 if target == "projection_dirty_deferred" else [])
                stop, indices, events = {}, [], []
                closed = asyncio.Event()
                def record(event, **values):
                    events.append((event, values))
                    if event == target:
                        raise OSError("synthetic audit failure")
                    if event == "projection_dirty_producer_progress":
                        stop["requested"] = True
                def batch(index):
                    indices.append(index)
                    bg._apply_dirty_batch(index, self.manifest, store.connect, store.mark, record)
                async def dispatcher(args, *, stop):
                    try:
                        await asyncio.Future()
                    finally:
                        closed.set()
                async def producer(shutdown, current_stop):
                    await bg._dirty_producer(self.manifest, Path(folder), shutdown, current_stop, batch, record=record)
                with patch.object(bg.time, "sleep"), self.assertRaisesRegex(OSError, "synthetic audit failure"):
                    await asyncio.wait_for(bg._run_with_producer(dispatcher, None, stop, producer), 2)
                self.assertTrue(closed.is_set())
                self.assertEqual([0], indices)
                self.assertEqual(0 if target == "projection_dirty_deferred" else 1, store.commits)


class DirtyProducerReportTests(unittest.TestCase):
    setUp = support.LinuxBackgroundIdentityTests.setUp
    write_fixture = support.LinuxBackgroundIdentityTests.write_fixture
    write_manifest = support.LinuxBackgroundIdentityTests.write_manifest

    def test_confirmed_complete_pressure_passes_with_separate_producer_status(self):
        with patch.object(bg.background, "_report_validated_background", return_value=self.report):
            result = bg.report_lab_background(self.identity)
        self.assertEqual("passed", result["status"])
        self.assertEqual("passed", result["pressure_verification"]["status"])
        self.assertEqual("run", result["pressure_verification"]["scope"])
        self.assertEqual("stopped", result["pressure_verification"]["segments"][0]["producer_status"])
        self.assertEqual("process_segment", result["pressure_verification"]["segments"][0]["scope"])

    def test_pending_zero_production_or_absent_pressure_fail_even_when_finite_jobs_are_done(self):
        valid = dict(self.state["dirty_producer"])
        variants = ({**valid, "final_pending": 1, "pending_state": "rolled_back"},
                    {**valid, "committed_batches": 0}, {}, {**valid, "status": "error"})
        for pressure in variants:
            with self.subTest(pressure=pressure):
                self.state["dirty_producer"] = pressure
                (self.runtime / bg.STATE).write_text(json.dumps(self.state), encoding="utf-8")
                events = [dict(event) for event in self.events]
                if pressure:
                    events[-1]["pressure"] = pressure
                else:
                    events = []
                (self.runtime / bg.background.EVENTS).write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
                with patch.object(bg.background, "_report_validated_background", return_value=dict(self.report)):
                    with self.assertRaisesRegex(RuntimeError, "Synthetic pressure is incomplete"):
                        bg.report_lab_background(self.identity)
                saved = json.loads((self.runtime / "background-report.json").read_text(encoding="utf-8"))
                self.assertEqual("incomplete", saved["status"])
                self.assertEqual("incomplete", saved["pressure_verification"]["status"])
                self.assertTrue(saved["finite_task_verification"]["all_present_and_done"])

    def test_restart_cannot_hide_previous_pending_failed_or_unfinished_segment(self):
        for previous in ("pending", "failed", "missing_stop"):
            with self.subTest(previous=previous):
                old = [{**event, "pressure": {**event["pressure"], "segment_started_at": "synthetic-previous"}}
                       for event in self.events]
                if previous == "pending":
                    old[-1]["pressure"].update(final_pending=1, pending_state="rolled_back")
                elif previous == "failed":
                    old[-1]["event"] = "projection_dirty_producer_failed"
                    old[-1]["pressure"]["status"] = "error"
                else:
                    old.pop()
                result = bg._pressure_run_integrity("owned-run", old + self.events, self.state)
                self.assertEqual("incomplete", result["status"])
                self.assertEqual(2, result["segment_count"])
                self.assertEqual("passed", result["segments"][1]["status"])

    def test_run_totals_include_healthy_segments_and_require_commit_and_last_state_matches(self):
        old = [{**event, "pressure": {**event["pressure"], "segment_started_at": "synthetic-previous"}}
               for event in self.events]
        events = old + self.events
        result = bg._pressure_run_integrity("owned-run", events, self.state)
        self.assertEqual("passed", result["status"])
        self.assertEqual(2, result["committed_batches"])
        self.assertEqual(2, result["committed_batch_events"])
        self.assertEqual(0, result["final_pending"])
        altered = [event for event in events if event is not old[1]]
        self.assertEqual("incomplete", bg._pressure_run_integrity("owned-run", altered, self.state)["status"])
        self.assertEqual("incomplete", bg._pressure_run_integrity("owned-run", events, {"dirty_producer": {}})["status"])


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
        async def producer(manifest, runtime, shutdown, stop, dirty_batch, *, record=None):
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
