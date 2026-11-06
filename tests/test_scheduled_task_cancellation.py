"""Shutdown observes committed claims and real handler/finalizer threads."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
import io
import threading
import unittest
from unittest.mock import patch

from classroom_app.database import get_db_connection
from classroom_app.services import scheduled_task_service as service
from tests.sqlite_database_fixture import isolated_sqlite_database


class ScheduledTaskCancellationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        # Handler imports are startup work, outside controlled cancellation
        # gates and their short thread-wait deadlines.
        from classroom_app.services import scheduled_task_handlers  # noqa: F401

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(service._HANDLERS))
        with get_db_connection() as conn:
            conn.execute("DELETE FROM scheduled_tasks")
            conn.execute("CREATE TABLE IF NOT EXISTS scheduler_cancel_effects(task_id INTEGER PRIMARY KEY)")
            conn.execute("DELETE FROM scheduler_cancel_effects")

    def schedule(self, key="unit:cancel"):
        with get_db_connection() as conn:
            return service.schedule_task(conn, task_kind="unit_cancel", dedupe_key=key,
                run_at=datetime.now()-timedelta(seconds=2), priority=10, max_attempts=1)

    def effect(self, task):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO scheduler_cancel_effects(task_id) VALUES(?)", (task["id"],))
        return "committed once"

    def row(self, task_id):
        with get_db_connection() as conn:
            return dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())

    def effect_count(self):
        with get_db_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM scheduler_cancel_effects").fetchone()[0]

    async def wait_event(self, event):
        self.assertTrue(await asyncio.wait_for(asyncio.to_thread(event.wait, 4), timeout=5))

    async def finish(self, task, *releases):
        for event in releases:
            event.set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=6)

    async def cancel_repeatedly(self, task, count=3):
        for _ in range(count):
            task.cancel()
            await asyncio.sleep(0.025)

    async def test_cancel_after_claim_commit_observes_result_and_executes_once(self):
        task_id = self.schedule()
        claimed, release = threading.Event(), threading.Event()
        original = service._claim_due_tasks
        def blocked(*args, **kwargs):
            rows = original(*args, **kwargs)
            claimed.set()
            if not release.wait(4):
                raise TimeoutError("test claim response gate")
            return rows
        service.register_task_handler("unit_cancel", self.effect)
        with patch.object(service, "_claim_due_tasks", side_effect=blocked) as claim:
            work = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=20, lane="normal"))
            try:
                await self.wait_event(claimed)
                self.assertEqual("running", self.row(task_id)["status"])
                await self.cancel_repeatedly(work)
                self.assertFalse(work.done())
                self.assertEqual(0, self.effect_count())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(work, timeout=5)
                self.assertEqual(1, claim.call_count, "Stop cannot drain another claim slot")
            finally:
                await self.finish(work, release)
        self.assertEqual(("done", 1), (self.row(task_id)["status"], self.effect_count()))
        self.assertEqual(0, (await service.process_due_scheduled_tasks_once(limit=1, lane="normal"))["claimed"])

    async def test_real_worker_stop_during_claim_drains_owned_slot(self):
        task_id = self.schedule()
        claimed, release = threading.Event(), threading.Event()
        original = service._claim_due_tasks
        def blocked(*args, **kwargs):
            rows = original(*args, **kwargs)
            if kwargs.get("lane") == "normal" and rows and rows[0]["id"] == task_id:
                claimed.set()
                if not release.wait(4):
                    raise TimeoutError("test worker claim gate")
            return rows
        service.register_task_handler("unit_cancel", self.effect)
        with patch.object(service, "_claim_due_tasks", side_effect=blocked):
            worker = asyncio.create_task(service.run_scheduler_worker_forever("unit-cancel-claim", poll_seconds=5))
            try:
                await self.wait_event(claimed)
                worker.cancel()
                await asyncio.sleep(0.04)
                self.assertFalse(worker.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(worker, timeout=5)
            finally:
                await self.finish(worker, release)
        self.assertEqual(("done", 1), (self.row(task_id)["status"], self.effect_count()))

    async def test_direct_task_multiple_cancel_keeps_handler_and_finalizer_leases(self):
        task_id = self.schedule()
        handler_started, handler_release = threading.Event(), threading.Event()
        final_started, final_release = threading.Event(), threading.Event()
        def handler(task):
            handler_started.set()
            if not handler_release.wait(4):
                raise TimeoutError("test handler gate")
            return self.effect(task)
        original_success = service._mark_success
        def finalize(*args):
            final_started.set()
            if not final_release.wait(4):
                raise TimeoutError("test finalizer gate")
            return original_success(*args)
        service.register_task_handler("unit_cancel", handler)
        task = service._claim_due_tasks(1, lane="normal")[0]
        with patch.object(service, "SCHEDULER_LEASE_HEARTBEAT_SECONDS", 0.01), \
             patch.object(service, "_renew_task_lease", wraps=service._renew_task_lease) as renew, \
             patch.object(service, "_mark_success", side_effect=finalize):
            work = asyncio.create_task(service._run_claimed_task(task))
            try:
                await self.wait_event(handler_started)
                await self.cancel_repeatedly(work)
                self.assertFalse(work.done())
                self.assertGreater(renew.call_count, 0)
                handler_release.set()
                await self.wait_event(final_started)
                before = renew.call_count
                await self.cancel_repeatedly(work)
                self.assertFalse(work.done())
                self.assertEqual("running", self.row(task_id)["status"])
                self.assertGreater(renew.call_count, before)
                final_release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(work, timeout=5)
            finally:
                await self.finish(work, handler_release, final_release)
        self.assertEqual(("done", 1), (self.row(task_id)["status"], self.effect_count()))

    async def test_independent_cancelled_slots_preserve_each_others_claims(self):
        first, second = self.schedule("unit:one"), self.schedule("unit:two")
        started = {first: threading.Event(), second: threading.Event()}
        releases = {first: threading.Event(), second: threading.Event()}
        def handler(task):
            started[task["id"]].set()
            if not releases[task["id"]].wait(4):
                raise TimeoutError("test independent slot gate")
            return self.effect(task)
        service.register_task_handler("unit_cancel", handler)
        one = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=1, lane="normal"))
        two = None
        try:
            await self.wait_event(started[first])
            two = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=1, lane="normal"))
            await self.wait_event(started[second])
            first_token, second_token = self.row(first)["locked_by"], self.row(second)["locked_by"]
            self.assertNotEqual(first_token, second_token)
            await asyncio.gather(self.cancel_repeatedly(one), self.cancel_repeatedly(two))
            releases[first].set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(one, timeout=5)
            self.assertEqual("done", self.row(first)["status"])
            self.assertEqual(("running", second_token), (self.row(second)["status"], self.row(second)["locked_by"]))
            releases[second].set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(two, timeout=5)
        finally:
            await self.finish(one, *releases.values())
            if two is not None:
                await self.finish(two)
        self.assertEqual(2, self.effect_count())

    async def test_cancel_with_no_claim_stops_without_dispatch_or_next_claim(self):
        started, release = threading.Event(), threading.Event()
        def empty_claim(*_args, **_kwargs):
            started.set()
            if not release.wait(4):
                raise TimeoutError("test empty claim gate")
            return []
        with patch.object(service, "_claim_due_tasks", side_effect=empty_claim) as claim, \
             patch.object(service, "_dispatch_task") as dispatch:
            work = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=20))
            try:
                await self.wait_event(started)
                await self.cancel_repeatedly(work)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(work, timeout=5)
            finally:
                await self.finish(work, release)
        self.assertEqual(1, claim.call_count)
        dispatch.assert_not_called()

    async def test_unknown_claim_result_keeps_diagnostic_and_stale_takeover(self):
        task_id = self.schedule()
        started, release = threading.Event(), threading.Event()
        original = service._claim_due_tasks
        original_token = []
        def unknown_claim(*args, **kwargs):
            rows = original(*args, **kwargs)
            original_token.append(rows[0]["locked_by"])
            started.set()
            if not release.wait(4):
                raise TimeoutError("test unknown claim gate")
            raise ConnectionError("synthetic lost response after claim commit")
        service.register_task_handler("unit_cancel", self.effect)
        output = io.StringIO()
        loop = asyncio.get_running_loop()
        ambient_errors = []
        previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: ambient_errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_exception_handler)
        with contextlib.redirect_stdout(output), patch.object(service, "_claim_due_tasks", side_effect=unknown_claim):
            work = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=1))
            try:
                await self.wait_event(started)
                await self.cancel_repeatedly(work)
                release.set()
                with self.assertRaises(asyncio.CancelledError) as stopped:
                    await asyncio.wait_for(work, timeout=5)
                self.assertIsInstance(stopped.exception.__cause__, ConnectionError)
            finally:
                await self.finish(work, release)
        self.assertIn("cancelled all claim slot ended with ConnectionError", output.getvalue())
        self.assertEqual([], ambient_errors, "The owner consumes the error instead of leaving an unobserved Future")
        self.assertEqual(("running", original_token[0]), (self.row(task_id)["status"], self.row(task_id)["locked_by"]))
        self.assertEqual(0, self.effect_count())
        self.assertEqual(0, (await service.process_due_scheduled_tasks_once(limit=1))["claimed"])
        with get_db_connection() as conn:
            conn.execute("UPDATE scheduled_tasks SET updated_at='2000-01-01' WHERE id=?", (task_id,))
        seen = []
        def recovered(task):
            seen.append(task["locked_by"])
            return self.effect(task)
        service.register_task_handler("unit_cancel", recovered)
        self.assertEqual(1, (await service.process_due_scheduled_tasks_once(limit=1))["done"])
        self.assertNotEqual(original_token[0], seen[0])
        self.assertEqual(("done", 1), (self.row(task_id)["status"], self.effect_count()))

    async def test_claim_error_without_cancellation_is_not_hidden(self):
        task_id = self.schedule()
        with patch.object(service, "_claim_due_tasks", side_effect=ConnectionError("synthetic unavailable claim")):
            with self.assertRaisesRegex(ConnectionError, "unavailable claim"):
                await service.process_due_scheduled_tasks_once(limit=1)
        self.assertEqual(("pending", ""), (self.row(task_id)["status"], self.row(task_id)["locked_by"]))

    async def test_internally_cancelled_owned_work_does_not_loop_or_fake_success(self):
        async def self_cancelled():
            raise asyncio.CancelledError
        work = asyncio.create_task(self_cancelled())
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(service._await_owned_work(work, operation="unit internal cancellation"), timeout=1)
        self.assertTrue(work.cancelled())

    async def test_cancel_during_unknown_finalization_preserves_actual_commit_outcome(self):
        service.register_task_handler("unit_cancel", self.effect)
        original = service._mark_success
        for committed in (False, True):
            with self.subTest(commit_happened=committed):
                task_id = self.schedule(f"unit:finalize:{committed}")
                started, release = threading.Event(), threading.Event()
                def unknown_finalizer(task, result):
                    started.set()
                    if not release.wait(4):
                        raise TimeoutError("test unknown finalizer gate")
                    if committed:
                        original(task, result)
                    raise ConnectionError("synthetic unknown finalization outcome")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), patch.object(service, "_mark_success", side_effect=unknown_finalizer):
                    work = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=1))
                    try:
                        await self.wait_event(started)
                        await self.cancel_repeatedly(work)
                        self.assertFalse(work.done())
                        release.set()
                        with self.assertRaises(asyncio.CancelledError) as stopped:
                            await asyncio.wait_for(work, timeout=5)
                        self.assertIsInstance(stopped.exception.__cause__, ConnectionError)
                    finally:
                        await self.finish(work, release)
                self.assertIn("ended with ConnectionError", output.getvalue())
                self.assertEqual("done" if committed else "running", self.row(task_id)["status"])
                with get_db_connection() as conn:
                    self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM scheduler_cancel_effects WHERE task_id=?", (task_id,)).fetchone()[0])

    async def test_operator_cancellation_is_not_undone_by_drained_handler(self):
        task_id = self.schedule()
        started, release = threading.Event(), threading.Event()
        def handler(task):
            started.set()
            if not release.wait(4):
                raise TimeoutError("test operator cancellation gate")
            return self.effect(task)
        service.register_task_handler("unit_cancel", handler)
        work = asyncio.create_task(service.process_due_scheduled_tasks_once(limit=1))
        try:
            await self.wait_event(started)
            with get_db_connection() as conn:
                self.assertEqual(1, service.cancel_tasks_by_dedupe(conn, "unit:cancel"))
            cancelled_row = self.row(task_id)
            await self.cancel_repeatedly(work)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(work, timeout=5)
        finally:
            await self.finish(work, release)
        self.assertEqual(cancelled_row, self.row(task_id))
        self.assertEqual(1, self.effect_count())
