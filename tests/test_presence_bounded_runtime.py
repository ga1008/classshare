"""Synthetic presence queues: real bounded threads, no network or business DB."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from classroom_app.services import presence_service as presence
from classroom_app.services import social_common


async def until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Synthetic presence condition did not complete")
        await asyncio.sleep(.005)


class PresenceBoundedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        presence.reset_for_tests()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="presence-test")
        self.enterContext(patch.object(social_common, "social_executor", return_value=self.executor))
        self.enterContext(patch.object(presence, "_listeners", []))
        self.stored = {}
        self.calls = []
        self.release_events = []
        self.notify_release = asyncio.Event()
        self.writer = self.enterContext(patch.object(presence, "_persist_last_seen_batch", side_effect=self.persist))
        presence.start_presence_runtime()

    def persist(self, items):
        self.calls.append(list(items))
        for identity, stamp in items:
            self.stored[identity] = max(stamp, self.stored.get(identity, ""))
        return {"ok": True}

    def gate(self):
        event = threading.Event()
        self.release_events.append(event)
        return event

    async def asyncTearDown(self):
        for event in self.release_events:
            event.set()
        self.notify_release.set()
        if presence._runtime is not None:
            for slot in presence._runtime.slots.values():
                slot.retry_at = slot.notice_retry_at = 0
            result = await presence.stop_presence_runtime(timeout=3)
            self.assertTrue(result["clean"], result)
        self.executor.shutdown(wait=True)
        presence.reset_for_tests()

    async def test_200_identities_repeated_force_closes_coalesce_into_two_real_jobs(self):
        release = self.gate()
        active, peak = [0], [0]
        lock = threading.Lock()
        def blocked(items):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            release.wait(3)
            result = self.persist(items)
            with lock:
                active[0] -= 1
            return result
        self.writer.side_effect = blocked
        clock = {"stamp": "2026-11-09T00:00:00.000000+00:00"}
        with patch.object(presence, "now_iso", side_effect=lambda: clock["stamp"]):
            for index in range(200):
                presence.touch(f"student:{index}", force=True)
            await until(lambda: active[0] == 2)
            for tick in range(1, 11):
                clock["stamp"] = f"2026-11-09T00:00:{tick:02d}.000000+00:00"
                for index in range(200):
                    presence.touch(f"student:{index}", force=True)
            self.assertEqual(200, len(presence._runtime.slots))
            self.assertEqual(2, len(presence._runtime.writes))
            self.assertLessEqual(self.executor._work_queue.qsize(), 2)
            release.set()
            result = await presence.stop_presence_runtime(timeout=3)
        self.assertTrue(result["clean"])
        self.assertEqual(2, peak[0])
        self.assertEqual({f"student:{i}": clock["stamp"] for i in range(200)}, self.stored)
        self.assertTrue(all(len(batch) <= 64 for batch in self.calls))

    async def test_old_inflight_commit_never_clears_a_newer_version_or_overlaps_identity(self):
        release = self.gate()
        self.writer.side_effect = lambda items: (release.wait(3), self.persist(items))[1]
        runtime = presence._runtime
        runtime.write("student:1", "2026-11-09T00:00:01.000000+00:00")
        await until(lambda: len(runtime.writes) == 1)
        runtime.write("student:1", "2026-11-09T00:00:02.000000+00:00")
        runtime.write("student:1", "2026-11-09T00:00:03.000000+00:00")
        await asyncio.sleep(.02)
        self.assertEqual(1, len(runtime.writes))
        release.set()
        await until(lambda: not runtime.slots)
        self.assertEqual(["2026-11-09T00:00:01.000000+00:00", "2026-11-09T00:00:03.000000+00:00"],
                         [items[0][1] for items in self.calls])

    async def test_unknown_commit_retries_latest_value_without_false_confirmation(self):
        attempts = [0]
        def uncertain(items):
            attempts[0] += 1
            self.persist(items)  # Commit happened, acknowledgement was lost.
            if attempts[0] == 1:
                return {"ok": False, "unknown_commit": True, "error": "SyntheticLostAck"}
            return {"ok": True}
        self.writer.side_effect = uncertain
        runtime = presence._runtime
        runtime.write("student:1", "2026-11-09T00:00:01.000000+00:00")
        await until(lambda: runtime.stats["write_failures"] == 1)
        self.assertNotIn("student:1", presence._last_written)
        self.assertTrue(runtime.snapshot()["degraded"])
        runtime.write("student:1", "2026-11-09T00:00:02.000000+00:00")
        await until(lambda: not runtime.slots)
        self.assertEqual(2, attempts[0])
        self.assertEqual(1, runtime.stats["unknown_commit_count"])
        self.assertEqual("2026-11-09T00:00:02.000000+00:00", self.stored["student:1"])

    async def test_hot_identity_cannot_starve_older_waiting_identities(self):
        release = self.gate()
        self.writer.side_effect = lambda items: (release.wait(3), self.persist(items))[1]
        runtime = presence._runtime
        with patch.object(presence, "PERSIST_WORKERS", 1), patch.object(presence, "PERSIST_BATCH_SIZE", 1):
            runtime.write("student:hot", "old")
            await until(lambda: bool(runtime.writes))
            runtime.write("student:waiting1", "stamp")
            runtime.write("student:waiting2", "stamp")
            for _ in range(100):
                runtime.write("student:hot", "updated")
            release.set()
            await until(lambda: not runtime.slots)
        self.assertEqual(["student:hot", "student:waiting1", "student:waiting2", "student:hot"],
                         [batch[0][0] for batch in self.calls])

    async def test_executor_rejection_does_not_strand_writing_slot(self):
        actual_submit = self.executor.submit
        admitted = [0]
        def submit(*args, **kwargs):
            admitted[0] += 1
            if admitted[0] == 1:
                raise RuntimeError("Synthetic executor admission failure")
            return actual_submit(*args, **kwargs)
        with patch.object(self.executor, "submit", side_effect=submit):
            presence._runtime.write("student:1", "stamp")
            await until(lambda: presence._runtime.stats["write_failures"] == 1)
            self.assertFalse(presence._runtime.slots["student:1"].writing)
            self.assertFalse(presence._runtime.writes)
            await until(lambda: not presence._runtime.slots)
        self.assertEqual("stamp", self.stored["student:1"])

    async def test_confirmation_cache_is_bounded_and_retired_identities_are_removed(self):
        with patch.object(presence, "MAILBOX_CAPACITY", 3):
            for index in range(20):
                presence._remember_write_confirmation(f"student:{index}")
            self.assertEqual(["student:17", "student:18", "student:19"], list(presence._last_written))
            presence._runtime.write("student:retired", "stamp")
            await until(lambda: not presence._runtime.slots)
            self.assertNotIn("student:retired", presence._last_written)

    async def test_total_presence_induced_execution_is_two_writes_plus_two_listeners(self):
        release = self.gate()
        active, peak = [0], [0]
        lock = threading.Lock()
        def block():
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            release.wait(3)
            with lock:
                active[0] -= 1
        def writer(items):
            block()
            return self.persist(items)
        async def listener(*_):
            await asyncio.wrap_future(self.executor.submit(block))
        self.writer.side_effect = writer
        presence.add_listener(listener)
        for index in range(200):
            presence.connection_opened(f"student:{index}", f"socket:{index}", "social_ws")
        await until(lambda: active[0] == 4)
        report = presence.runtime_snapshot()
        self.assertEqual((2, 2), (report["inflight_db"], report["inflight_notify"]))
        self.assertEqual(4, peak[0])
        self.assertLessEqual(self.executor._work_queue.qsize(), 2)
        release.set()
        self.assertTrue((await presence.stop_presence_runtime(timeout=3))["clean"])

    async def test_full_mailbox_keeps_admitted_latest_but_reports_lost_final_close(self):
        release = self.gate()
        self.writer.side_effect = lambda items: (release.wait(3), self.persist(items))[1]
        with patch.object(presence, "MAILBOX_CAPACITY", 2), patch.object(presence, "OFFLINE_GRACE_SECONDS", .01):
            presence.connection_opened("student:1", "one", "social_ws")
            presence.connection_opened("student:2", "two", "social_ws")
            await until(lambda: bool(presence._runtime.writes))
            presence.connection_opened("student:3", "three", "social_ws")
            presence.connection_closed("student:3", "three")
            presence._runtime.write("student:1", "2099-01-01T00:00:00.000000+00:00")
            await asyncio.sleep(.02)
            self.assertFalse(presence.is_connected("student:3"))
            self.assertEqual(2, len(presence._runtime.slots))
            self.assertGreater(presence.runtime_snapshot()["overflow_count"], 0)
            self.assertEqual(1, presence.runtime_snapshot()["unpersisted_final_close_count"])
            release.set()
            await until(lambda: not presence._runtime.slots)
            self.assertEqual("2099-01-01T00:00:00.000000+00:00", self.stored["student:1"])
            self.assertNotIn("student:3", self.stored)
            self.assertTrue(presence.runtime_snapshot()["degraded"])

    async def test_still_connected_overflow_identity_gets_reconciliation_opportunity(self):
        release = self.gate()
        self.writer.side_effect = lambda items: (release.wait(3), self.persist(items))[1]
        with patch.object(presence, "MAILBOX_CAPACITY", 1):
            presence.connection_opened("student:1", "one", "social_ws")
            await until(lambda: bool(presence._runtime.writes))
            presence.connection_opened("student:2", "two", "social_ws")
            release.set()
            await until(lambda: "student:2" in self.stored)
            self.assertGreater(presence.runtime_snapshot()["reconciled"], 0)

    async def test_notifications_are_bounded_and_same_identity_finishes_offline(self):
        delivered = []
        async def listener(identity, online, stamp):
            await self.notify_release.wait()
            delivered.append((identity, online))
        presence.add_listener(listener)
        for index in range(200):
            presence._runtime.notify(f"student:{index}", True, "online")
        await until(lambda: len(presence._runtime.notices) == 2)
        for index in range(200):
            presence._runtime.notify(f"student:{index}", False, "offline")
        self.assertEqual(2, len(presence._runtime.notices))
        self.notify_release.set()
        await until(lambda: not presence._runtime.slots)
        latest = dict(delivered)
        self.assertEqual({f"student:{i}": False for i in range(200)}, latest)

    async def test_confirmed_write_with_slow_notification_keeps_60_second_throttle(self):
        async def listener(*_):
            await self.notify_release.wait()
        presence.add_listener(listener)
        presence.connection_opened("student:1", "one", "social_ws")
        await until(lambda: "student:1" in presence._last_written)
        slot = presence._runtime.slots["student:1"]
        version = slot.version
        for _ in range(100):
            presence.touch("student:1")
        self.assertEqual(version, slot.version)
        self.assertEqual(1, len(self.calls))
        self.notify_release.set()

    async def test_multiple_tabs_grace_reconnect_never_emits_false_offline(self):
        states = []
        async def listener(_identity, online, _stamp):
            states.append(online)
        presence.add_listener(listener)
        with patch.object(presence, "OFFLINE_GRACE_SECONDS", .04):
            presence.connection_opened("student:1", "one", "social_ws")
            presence.connection_opened("student:1", "two", "classroom_ws")
            await until(lambda: states == [True])
            presence.connection_closed("student:1", "one")
            presence.connection_closed("student:1", "two")
            await asyncio.sleep(.01)
            presence.connection_opened("student:1", "three", "social_ws")
            await asyncio.sleep(.06)
            self.assertTrue(presence.is_connected("student:1"))
            self.assertEqual([True], states)
            presence.connection_closed("student:1", "three")
            await until(lambda: states == [True, False])

    async def test_shutdown_timeout_and_reset_do_not_cancel_actual_db_thread_then_restart(self):
        release = self.gate()
        self.writer.side_effect = lambda items: (release.wait(3), self.persist(items))[1]
        presence.touch("student:1", force=True)
        await until(lambda: bool(presence._runtime.writes))
        future = next(iter(presence._runtime.writes))
        result = await presence.stop_presence_runtime(timeout=.03)
        self.assertFalse(result["clean"])
        self.assertFalse(future.cancelled())
        self.assertFalse(future.done())
        with self.assertRaises(RuntimeError):
            presence.reset_for_tests()
        with self.assertRaises(RuntimeError):
            presence.start_presence_runtime()
        release.set()
        self.assertTrue((await presence.stop_presence_runtime(timeout=2))["clean"])
        self.assertTrue(future.done())
        presence.start_presence_runtime()
        presence.touch("student:2", force=True)
        await until(lambda: "student:2" in self.stored)

    async def test_shutdown_retains_listener_task_that_owns_a_real_thread(self):
        release = self.gate()
        held = []
        async def listener(*_):
            future = self.executor.submit(release.wait, 3)
            held.append(future)
            await asyncio.wrap_future(future)
        presence.add_listener(listener)
        presence._runtime.notify("student:1", True, "stamp")
        await until(lambda: bool(held))
        result = await presence.stop_presence_runtime(timeout=.02)
        self.assertFalse(result["clean"])
        self.assertFalse(held[0].cancelled())
        self.assertFalse(next(iter(presence._runtime.notices)).cancelled())
        release.set()
        self.assertTrue((await presence.stop_presence_runtime(timeout=2))["clean"])

    async def test_clean_restart_does_not_resurrect_old_lifecycle_connections(self):
        presence.connection_opened("student:1", "old-loop-socket", "social_ws")
        self.assertTrue(presence.is_connected("student:1"))
        self.assertTrue((await presence.stop_presence_runtime(timeout=2))["clean"])
        presence.start_presence_runtime()
        self.assertFalse(presence.is_connected("student:1"))
        presence.connection_opened("student:1", "new-loop-socket", "social_ws")
        self.assertTrue(presence.is_connected("student:1"))


class PresencePersistenceContractTests(unittest.TestCase):
    def test_multirow_upsert_preserves_newer_timestamps(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE user_presence(identity TEXT PRIMARY KEY,last_seen_at TEXT)")
        statements = []
        class Borrowed:
            def execute(self, sql, params=()):
                statements.append(sql)
                return conn.execute(sql, params)
            def commit(self): conn.commit()
            def rollback(self): conn.rollback()
            def close(self): pass
        try:
            with patch("classroom_app.db.connection.get_configured_db_engine", return_value="sqlite"), \
                 patch("classroom_app.database.get_db_connection", return_value=Borrowed()), \
                 patch.object(presence, "ensure_schema"):
                latest = [(f"student:{i}", "2026-11-09T12:00:00.000000+00:00") for i in range(64)]
                self.assertTrue(presence._persist_last_seen_batch(latest)["ok"])
                self.assertTrue(presence._persist_last_seen_batch([(key, "2026-11-08T12:00:00.000000+00:00") for key, _ in latest])["ok"])
            self.assertEqual(2, len(statements))
            self.assertEqual(latest, conn.execute("SELECT identity,last_seen_at FROM user_presence ORDER BY rowid").fetchall())
        finally:
            conn.close()

    def test_postgres_budget_single_upsert_and_ambiguous_commit_are_explicit(self):
        conn = Mock()
        conn.commit.side_effect = RuntimeError("Synthetic lost acknowledgement")
        with patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"), \
             patch("classroom_app.db.postgres.connect_postgres", return_value=conn) as connect:
            outcome = presence._persist_last_seen_batch([("student:1", "stamp")])
        connect.assert_called_once_with(checkout_timeout=1.0)
        self.assertEqual(2, conn.execute.call_count)
        self.assertIn("250ms", conn.execute.call_args_list[0].args[0])
        self.assertIn("1000ms", conn.execute.call_args_list[0].args[0])
        self.assertTrue(outcome["unknown_commit"])
        self.assertFalse(outcome["ok"])
        conn.close.assert_called_once()


class PresenceAppShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_presence_health_degrades_without_changing_database_readiness(self):
        source = Path(__file__).parents[1] / "classroom_app/app.py"
        nodes = [n for n in ast.parse(source.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.AsyncFunctionDef) and n.name in {"internal_health", "internal_readiness"}]
        for node in nodes:
            node.decorator_list = []
        diagnostics = {"errors": {}, "stale": [], "pending": [], "age_seconds": {"database_probe": 1}}
        namespace = {
            "get_behavior_write_pipeline_stats": lambda: {"alive": True, "queue_depth": 0, "queue_capacity": 20000},
            "_health_snapshots": SimpleNamespace(snapshot=lambda: {"diagnostics": diagnostics, "database_probe": {"ok": True}}),
            "presence_service": SimpleNamespace(runtime_snapshot=lambda: {"degraded": True}),
            "anyio": SimpleNamespace(to_thread=SimpleNamespace(current_default_thread_limiter=lambda: SimpleNamespace(total_tokens=40))),
            "get_deployment_release_id": lambda: "synthetic", "AI_ASSISTANT_URL": "unused", "DB_PATH": "unused",
            "asdict": lambda _: {}, "database_backend_state": lambda: None, "app_timezone_name": lambda: "UTC",
            "local_iso": lambda: "stamp", "get_pool_stats": lambda: {},
            "social_push_service": SimpleNamespace(outbound_snapshot=lambda: {}),
            "JSONResponse": lambda content, **kwargs: SimpleNamespace(content=content, **kwargs),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
        self.assertEqual("degraded", (await namespace["internal_health"]())["status"])
        ready = await namespace["internal_readiness"]()
        self.assertEqual(200, ready.status_code)
        self.assertEqual("ok", ready.content["status"])

    async def test_actual_app_shutdown_does_not_close_pool_after_unclean_presence(self):
        source = Path(__file__).parents[1] / "classroom_app/app.py"
        node = next(n for n in ast.parse(source.read_text(encoding="utf-8")).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "shutdown_event")
        node.decorator_list = []
        # Replace only the local import with its injected fake; execute the
        # actual shutdown ordering without importing/starting the application.
        node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
        async def run_sync(fn): return fn()
        namespace = {
            "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync)),
            "_health_snapshots": SimpleNamespace(stop=Mock()), "stop_password_verifier": Mock(),
            "social_push_service": SimpleNamespace(stop_outbound_writers=AsyncMock()),
            "presence_service": SimpleNamespace(stop_presence_runtime=AsyncMock(return_value={"clean": False})),
            "ai_client": SimpleNamespace(__aexit__=AsyncMock()),
            "stop_behavior_write_pipeline": Mock(), "close_connection_pool": Mock(),
        }
        for name in ("stop_discussion_mood_refresh_tasks", "stop_smart_attendance_advice_worker", "stop_durable_process_job_workers",
                     "stop_student_career_job_workers", "stop_behavior_profile_scheduler"):
            namespace[name] = AsyncMock()
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        with self.assertRaisesRegex(RuntimeError, "pool was not closed"):
            await namespace["shutdown_event"]()
        namespace["close_connection_pool"].assert_not_called()
        namespace["presence_service"].stop_presence_runtime.return_value = {"clean": True}
        await namespace["shutdown_event"]()
        namespace["close_connection_pool"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
