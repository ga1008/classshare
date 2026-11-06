"""Draft only: offline signal callbacks and dispatcher worker lifecycle.

No real OS signal, application database or provider is used. Existing scheduler
SQLite tests remain responsible for real claim/body/finalizer transaction fences.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, redirect_stdout
import io
import signal
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import scheduled_task_dispatcher as dispatcher
from classroom_app.services.scheduled_task_service import _await_owned_work


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


class Signals:
    def __init__(self, *, fail_int=False, before_loop=False):
        self.original = {signal.SIGTERM: object(), signal.SIGINT: object()}
        self.current = dict(self.original)
        self.fail_int, self.before_loop = fail_int, before_loop

    def getsignal(self, number):
        return self.current[number]

    def install(self, number, handler):
        old = self.current[number]
        if callable(handler) and number == signal.SIGINT and self.fail_int:
            raise ValueError("synthetic partial signal installation failure")
        self.current[number] = handler
        if callable(handler) and number == signal.SIGINT and self.before_loop:
            self.emit(signal.SIGTERM)
        return old

    def emit(self, number):
        self.current[number](number, None)


class DispatcherSignalLifecycleTests(unittest.TestCase):
    @contextmanager
    def runtime(self, signals, events, *, scheduler=None, wanderer=None, once=None, init_error=None,
                init_signal=False, heartbeat_hook=None, warmup=None):
        async def unused_worker(**_kwargs):
            raise AssertionError("--once must not launch a background worker")
        async def default_once():
            events.append("once")
            return {"claimed": 1, "failed": 1}
        def initialize():
            events.append("init")
            if init_error:
                raise init_error
            if init_signal:
                signals.emit(signal.SIGTERM)
        def heartbeat(_worker_id, *, status, last_error=""):
            if heartbeat_hook is not None:
                heartbeat_hook(status)
            events.append(("heartbeat", status, last_error))
        modules = {
            "classroom_app": module("classroom_app", __path__=[]),
            "classroom_app.config": module("classroom_app.config", ensure_runtime_directories=lambda: events.append("directories")),
            "classroom_app.database": module("classroom_app.database", init_database=initialize),
            "classroom_app.services": module("classroom_app.services", __path__=[], scheduled_task_handlers=object()),
            "classroom_app.services.scheduled_task_service": module("classroom_app.services.scheduled_task_service",
                process_due_scheduled_tasks_once=once or default_once,
                run_scheduler_worker_forever=scheduler or unused_worker, update_scheduler_heartbeat=heartbeat,
                _await_owned_work=_await_owned_work),
            "classroom_app.services.life_tip_service": module("classroom_app.services.life_tip_service", warm_life_tip_runtime=warmup or (lambda:True)),
            "classroom_app.services.wanderer_inbox_worker": module("classroom_app.services.wanderer_inbox_worker", run_forever=wanderer or unused_worker),
            "classroom_app.db": module("classroom_app.db", __path__=[]),
            "classroom_app.db.postgres": module("classroom_app.db.postgres", close_connection_pool=lambda: events.append("pool_closed")),
        }
        with patch.dict(sys.modules, modules), patch.object(dispatcher, "load_dotenv"), \
             patch.object(dispatcher.signal, "getsignal", side_effect=signals.getsignal), \
             patch.object(dispatcher.signal, "signal", side_effect=signals.install), \
             redirect_stdout(io.StringIO()):
            yield

    def main(self, *arguments):
        with patch.object(sys, "argv", ["scheduled_task_dispatcher.py", "--worker-id", "synthetic-worker", *arguments]):
            dispatcher.main()

    def test_signal_during_real_warm_thread_drains_before_terminal_and_never_claims(self):
        signals, events = Signals(), []
        entered, release = threading.Event(), threading.Event()
        def warm():
            events.append('warm_started');entered.set()
            if not release.wait(5):raise AssertionError('warm test release missing')
            events.append('warm_committed_closed')
        def signal_from_thread():
            if not entered.wait(5):return
            signals.emit(signal.SIGTERM)
            signals.emit(signal.SIGINT)
            release.set()
        trigger=threading.Thread(target=signal_from_thread)
        with self.runtime(signals,events,warmup=warm):
            trigger.start()
            self.main('--once')
            trigger.join(5)
        self.assertFalse(trigger.is_alive())
        self.assertNotIn('once',events)
        self.assertLess(events.index('warm_committed_closed'),events.index(('heartbeat','stopped','')))
        self.assertLess(events.index(('heartbeat','stopped','')),events.index('pool_closed'))
        self.assertEqual(signals.original,signals.current)

    def test_failed_warmup_degrades_without_skipping_the_real_once(self):
        signals,events=Signals(),[]
        def warm():raise RuntimeError('owned seed commit failed')
        with self.runtime(signals,events,warmup=warm):self.main('--once')
        self.assertEqual(1,events.count('once'))
        self.assertIn(('heartbeat','once',''),events)

    def test_signal_with_warm_commit_error_keeps_failure_and_never_claims(self):
        signals,events=Signals(),[]
        entered,release=threading.Event(),threading.Event()
        def warm():
            entered.set()
            if not release.wait(5):raise AssertionError('warm test release missing')
            events.append('warm_rolled_back_closed')
            raise RuntimeError('owned seed commit failed')
        def trigger():
            if entered.wait(5):signals.emit(signal.SIGTERM);release.set()
        thread=threading.Thread(target=trigger)
        with self.runtime(signals,events,warmup=warm):
            thread.start()
            with self.assertRaisesRegex(RuntimeError,'owned seed commit failed'):self.main('--once')
            thread.join(5)
        self.assertNotIn('once',events)
        self.assertIn(('heartbeat','error','RuntimeError'),events)
        self.assertLess(events.index('warm_rolled_back_closed'),events.index('pool_closed'))

    def test_term_and_second_int_drain_both_workers_before_stopped_and_pool_close(self):
        signals, events = Signals(), []
        async def scheduler(**_kwargs):
            events.append("scheduler_started")
            asyncio.get_running_loop().call_soon(signals.emit, signal.SIGTERM)
            try:
                await asyncio.Future()
            finally:
                events.append("scheduler_draining")
                signals.emit(signal.SIGINT)
                await asyncio.sleep(0.02)
                events.append("scheduler_drained")
        async def wanderer():
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0.01)
                events.append("wanderer_drained")
        with self.runtime(signals, events, scheduler=scheduler, wanderer=wanderer):
            self.main()
        heartbeat = ("heartbeat", "stopped", "")
        self.assertLess(events.index("scheduler_drained"), events.index(heartbeat))
        self.assertLess(events.index("wanderer_drained"), events.index(heartbeat))
        self.assertLess(events.index(heartbeat), events.index("pool_closed"))
        self.assertEqual(signals.original, signals.current)

    def test_once_keeps_normal_result_and_does_not_start_wanderer(self):
        signals, events = Signals(), []
        with self.runtime(signals, events):
            self.main("--once")
        self.assertEqual(1, events.count("once"))
        self.assertIn(("heartbeat", "once", ""), events)
        self.assertNotIn(("heartbeat", "stopped", ""), events)
        self.assertEqual("pool_closed", events[-1])
        self.assertEqual(signals.original, signals.current)

    def test_once_signal_finishes_current_work_without_claiming_another_or_reporting_once(self):
        signals, events = Signals(), []
        async def once():
            events.append("one_owned_slot")
            asyncio.get_running_loop().call_soon(signals.emit, signal.SIGTERM)
            try:
                await asyncio.Future()
            finally:
                signals.emit(signal.SIGINT)
                await asyncio.sleep(0.01)
                events.append("owned_slot_drained")
        with self.runtime(signals, events, once=once):
            self.main("--once")
        self.assertEqual(1, events.count("one_owned_slot"))
        self.assertIn(("heartbeat", "stopped", ""), events)
        self.assertFalse(any(isinstance(event, tuple) and event[1] == "once" for event in events))
        self.assertLess(events.index("owned_slot_drained"), events.index("pool_closed"))

    def test_runtime_error_drains_sibling_and_first_signal_during_cleanup_cannot_hide_it(self):
        signals, events = Signals(), []
        failure = RuntimeError("synthetic worker failure")
        async def scheduler(**_kwargs):
            await asyncio.sleep(0)
            raise failure
        async def wanderer():
            try:
                await asyncio.Future()
            finally:
                # This is the first signal, after error cleanup has begun.
                signals.emit(signal.SIGTERM)
                await asyncio.sleep(0.01)
                events.append("wanderer_drained")
        with self.runtime(signals, events, scheduler=scheduler, wanderer=wanderer):
            with self.assertRaises(RuntimeError) as raised:
                self.main()
        self.assertIs(failure, raised.exception)
        self.assertLess(events.index("wanderer_drained"), events.index("pool_closed"))
        self.assertIn(("heartbeat", "error", "RuntimeError"), events)
        self.assertEqual(signals.original, signals.current)

    def test_unknown_owned_result_cause_is_not_a_successful_signal_exit(self):
        signals, events = Signals(), []
        async def once():
            asyncio.get_running_loop().call_soon(signals.emit, signal.SIGTERM)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise asyncio.CancelledError from ConnectionError("synthetic unknown commit")
        with self.runtime(signals, events, once=once):
            with self.assertRaises(ConnectionError):
                self.main("--once")
        self.assertIn(("heartbeat", "error", "ConnectionError"), events)
        self.assertEqual("pool_closed", events[-1])
        self.assertEqual(signals.original, signals.current)

    def test_initialization_failure_remains_nonzero_without_fake_stopped_database_write(self):
        signals, events = Signals(), []
        with self.runtime(signals, events, init_error=RuntimeError("synthetic init failure")):
            with self.assertRaisesRegex(RuntimeError, "init failure"):
                self.main("--once")
        self.assertFalse(any(isinstance(event, tuple) and event[0] == "heartbeat" for event in events))
        self.assertEqual("pool_closed", events[-1])
        self.assertEqual(signals.original, signals.current)

    def test_partial_handler_installation_failure_restores_already_installed_handler(self):
        signals, events = Signals(fail_int=True), []
        with self.runtime(signals, events):
            with self.assertRaisesRegex(ValueError, "partial signal"):
                self.main()
        self.assertNotIn("init", events)
        self.assertEqual(signals.original, signals.current)

    def test_signal_before_loop_start_does_not_initialize_or_claim(self):
        signals, events = Signals(before_loop=True), []
        with self.runtime(signals, events):
            self.main()
        self.assertNotIn("init", events)
        self.assertNotIn("once", events)
        self.assertEqual(signals.original, signals.current)

    def test_signal_during_initialization_cannot_start_a_once_claim_or_workers(self):
        for arguments in (("--once",), ()):
            with self.subTest(arguments=arguments):
                signals, events = Signals(), []
                with self.runtime(signals, events, init_signal=True):
                    self.main(*arguments)
                self.assertNotIn("once", events)
                self.assertIn(("heartbeat", "stopped", ""), events)
                self.assertEqual("pool_closed", events[-1])
                self.assertEqual(signals.original, signals.current)

    def test_late_running_and_once_threads_finish_before_terminal_heartbeat(self):
        for old_status in ("running", "once"):
            with self.subTest(late_status=old_status):
                signals, events = Signals(), []
                entered, release = threading.Event(), threading.Event()
                def heartbeat_hook(status):
                    if status == old_status:
                        entered.set()
                        if not release.wait(4):
                            raise TimeoutError("synthetic late heartbeat gate")
                async def owned_work(**_kwargs):
                    from classroom_app.services.scheduled_task_service import update_scheduler_heartbeat
                    late = asyncio.create_task(asyncio.to_thread(update_scheduler_heartbeat,
                        "synthetic-worker", status=old_status))
                    assert await asyncio.to_thread(entered.wait, 4)
                    asyncio.get_running_loop().call_soon(signals.emit, signal.SIGTERM)
                    try:
                        await asyncio.Future()
                    finally:
                        late.cancel()  # Cancels the Future, not its running thread.
                        await asyncio.gather(late, return_exceptions=True)
                        asyncio.get_running_loop().call_later(0.03, release.set)
                        events.append("worker_coroutine_drained")
                async def sibling():
                    await asyncio.Future()
                with self.runtime(signals, events, scheduler=owned_work, wanderer=sibling,
                                  once=owned_work, heartbeat_hook=heartbeat_hook):
                    try:
                        self.main(*(("--once",) if old_status == "once" else ()))
                    finally:
                        release.set()
                late_write = ("heartbeat", old_status, "")
                terminal = ("heartbeat", "stopped", "")
                self.assertLess(events.index("worker_coroutine_drained"), events.index(late_write))
                self.assertLess(events.index(late_write), events.index(terminal))
                self.assertLess(events.index(terminal), events.index("pool_closed"))

    def test_successful_once_terminal_write_is_on_main_thread_after_runner(self):
        signals, events = Signals(), []
        main_thread = threading.get_ident()
        def heartbeat_hook(status):
            self.assertEqual("once", status)
            self.assertEqual(main_thread, threading.get_ident())
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
        with self.runtime(signals, events, heartbeat_hook=heartbeat_hook):
            self.main("--once")
        self.assertIn(("heartbeat", "once", ""), events)
        self.assertEqual("pool_closed", events[-1])


class DispatcherExternalCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_cancellation_without_owned_signal_is_not_swallowed(self):
        stop = {"requested": False, "cancel_sent": False, "draining": False, "loop": None, "task": None}
        entered = asyncio.Event()
        async def run(_args, *, stop):
            entered.set()
            await asyncio.Future()
        with patch.object(dispatcher, "_run", side_effect=run):
            task = asyncio.create_task(dispatcher._run_with_shutdown(SimpleNamespace(), stop))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stop["draining"])
        self.assertIsNone(stop["loop"])
