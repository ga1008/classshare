"""Capacity receiver lifecycle and isolation without a server, process or DB."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tools import platform_capacity_socket_workers as workers


class Connection:
    def __init__(self):
        self.incoming = deque()
        self.sent = []
        self.closed = False
        self.on_send = None
        self.last_counts = []

    def poll(self):
        if self.closed:
            raise OSError("closed synthetic pipe")
        return bool(self.incoming)

    def recv(self):
        item = self.incoming.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            self.last_counts = item.get("counts", self.last_counts)
        return item

    def send(self, value):
        if self.closed:
            raise BrokenPipeError("closed synthetic pipe")
        self.sent.append(value)
        if self.on_send:
            self.on_send(value)

    def close(self):
        self.closed = True


class Process:
    def __init__(self, parent, *, pid, graceful=True, resist_terminate=False, start_error=False):
        self.parent = parent
        self.pid = pid
        self.exitcode = None
        self.alive = False
        self.graceful = graceful
        self.resist_terminate = resist_terminate
        self.start_error = start_error
        self.terminated = self.killed = self.closed = False
        self.joins = []
        parent.on_send = self.command

    def command(self, value):
        if value == "stop" and self.graceful:
            self.parent.incoming.append({"type": "done", "ready": False, "counts": self.parent.last_counts})
            self.alive, self.exitcode = False, 0

    def start(self):
        if self.start_error:
            raise RuntimeError("synthetic spawn failure")
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        if not self.resist_terminate:
            self.alive, self.exitcode = False, -15

    def kill(self):
        self.killed = True
        self.alive, self.exitcode = False, -9

    def join(self, timeout):
        self.joins.append(timeout)

    def close(self):
        if self.alive:
            raise ValueError("cannot close an alive process")
        self.closed = True


class Context:
    def __init__(self, *, start_error_on=None, construct_error_on=None, **process_options):
        self.pairs = []
        self.processes = []
        self.start_error_on, self.construct_error_on = start_error_on, construct_error_on
        self.options = process_options
        self.chunks = []
        self.handshake_limits = []

    def Pipe(self, *, duplex):
        assert duplex
        pair = Connection(), Connection()
        self.pairs.append(pair)
        return pair

    def Process(self, *, target, args, daemon):
        assert daemon and target is workers._worker_entry
        index = len(self.pairs)
        if index == self.construct_error_on:
            raise RuntimeError("synthetic process construction failure")
        self.chunks.append(args[3])
        self.handshake_limits.append(args[5])
        process = Process(self.pairs[-1][0], pid=1000 + index,
                          start_error=index == self.start_error_on, **self.options)
        self.processes.append(process)
        return process


class Fixture:
    def make_fixture(self):
        temporary = workers.ROOT / ".codex-temp"
        temporary.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="socket-worker-unit-", dir=temporary)
        self.addCleanup(directory.cleanup)
        self.runtime = Path(directory.name).resolve()
        self.fixture = {"uiV3Synthetic": True, "classOfferingId": 7,
                        "databasePath": str(self.runtime / "db/classroom.db")}
        self.write_fixture()
        self.specs = [{"token": identity, "channel": channel, "path": path}
                      for identity in ("synthetic-a", "synthetic-b")
                      for channel, path in (("classroom", "/ws/7"), ("social", "/ws/social"))]

    def write_fixture(self):
        (self.runtime / "fixture.json").write_text(json.dumps(self.fixture), encoding="utf-8")


class WorkerGroupTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.make_fixture()
        self.groups = []

    async def asyncTearDown(self):
        for group in self.groups:
            await group.stop()

    async def group(self, *, connect_limit=0, **options):
        context = Context(**options)
        group = workers.SocketWorkerGroup(self.runtime, "http://127.0.0.1:18767", self.specs,
                                          workers=2, context=context, connect_limit=connect_limit)
        self.groups.append(group)
        await group.start()
        return group, context

    @staticmethod
    def snapshot(frames=1, *, ready=True):
        return {"type": "snapshot", "ready": ready, "counts": [
            [channel, event, count]
            for channel in ("classroom", "social")
            for event, count in (("connected", 1), ("application_ready", 1), ("frames", frames))]}

    async def test_cumulative_snapshots_replace_counts_and_keep_each_identity_in_one_process(self):
        group, context = await self.group()
        for parent, _child in context.pairs:
            parent.incoming.append(self.snapshot(3))
        group.refresh()
        self.assertEqual(group.counts[("social", "frames")], 6)
        context.pairs[0][0].incoming.extend([self.snapshot(5), self.snapshot(7)])
        group.refresh()
        self.assertEqual(group.counts[("social", "frames")], 10)
        self.assertEqual(group.counts[("social", "connected")], 2)
        await group.wait_ready(timeout=.01)
        self.assertEqual([{spec["token"] for spec in chunk} for chunk in context.chunks],
                         [{"synthetic-a"}, {"synthetic-b"}])

    async def test_handshake_configuration_is_explicit_and_diagnostic_snapshots_replace(self):
        group, context = await self.group(connect_limit=8)
        self.assertEqual(context.handshake_limits, [8, 8])
        self.assertEqual(group.timing["aggregate_handshake_limit"], 16)
        self.assertEqual(group.timing["handshake_mode"], "bounded")
        first, second = (pair[0] for pair in context.pairs)
        detail = {"channel": "social", "phase": "connect", "error": "ConnectionRefusedError",
                  "winerror": 10061, "errno": 10061, "detail": "synthetic refused", "target": "ws://127.0.0.1:18767/ws/social"}
        message = self.snapshot()
        message.update(faults=[detail], timing={"first_application_ready_at": group.started_at + .4,
                       "last_application_ready_at": group.started_at + .8, "handshake_peak": 8,
                       "handshakes_started": 2, "handshakes_finished": 2, "handshakes_cancelled": 0})
        first.incoming.extend([message, message])
        other = self.snapshot()
        other.update(timing={"first_application_ready_at": group.started_at + .2,
                             "last_application_ready_at": group.started_at + .9, "handshake_peak": 3})
        second.incoming.append(other)
        group.refresh()
        self.assertEqual(len(group.fault_details), 1)
        self.assertEqual(group.fault_details[0]["pid"], context.processes[0].pid)
        self.assertEqual(group.fault_details[0]["winerror"], 10061)
        self.assertEqual(group.timing["first_application_ready_ms"], 200)
        self.assertEqual(group.timing["last_application_ready_ms"], 900)
        self.assertEqual(group.timing["handshakes_started"], 2)
        self.assertEqual(group.timing["handshakes_finished"], 2)
        self.assertEqual(group.timing["not_started_sockets"], 2)
        self.assertEqual([item["handshake_peak"] for item in group.worker_status], [8, 3])
        plain, plain_context = await self.group()
        self.assertEqual(plain_context.handshake_limits, [0, 0])
        self.assertEqual(plain.timing["handshake_mode"], "unbounded")

    async def test_readiness_deadline_uses_real_hello_timestamps_not_delayed_parent_poll(self):
        for last, succeeds in ((129.0, True), (131.0, False)):
            with self.subTest(last=last):
                group, context = await self.group()
                group.started_at = 100.0
                for parent, _child in context.pairs:
                    message = self.snapshot()
                    message["timing"] = {"first_application_ready_at": 101.0, "last_application_ready_at": last}
                    parent.incoming.append(message)
                with patch.object(workers.time, "perf_counter", return_value=200.0):
                    if succeeds:
                        await group.wait_ready(timeout=30)
                    else:
                        with self.assertRaisesRegex(workers.SocketWorkerError, "after the deadline"):
                            await group.wait_ready(timeout=30)
                await group.stop()

    async def test_one_ready_child_never_satisfies_all_worker_readiness(self):
        group, context = await self.group()
        context.pairs[0][0].incoming.append(self.snapshot())
        with self.assertRaisesRegex(workers.SocketWorkerError, "timed out"):
            await group.wait_ready(timeout=0)
        self.assertEqual(group.counts[("worker", "readiness_timeout")], 1)

    async def test_fatal_eof_death_protocol_and_early_close_all_fail_readiness(self):
        for failure in ("fatal", "eof", "death", "malformed", "closed_early"):
            with self.subTest(failure=failure):
                group, context = await self.group()
                for parent, _child in context.pairs:
                    parent.incoming.append(self.snapshot())
                parent, process = context.pairs[0][0], context.processes[0]
                if failure == "fatal":
                    parent.incoming.append({"type": "fatal", "error": "SyntheticError"})
                elif failure == "eof":
                    parent.incoming.append(EOFError())
                elif failure == "death":
                    process.alive, process.exitcode = False, 9
                elif failure == "malformed":
                    parent.incoming.append({"type": "snapshot", "counts": [["social", "frames", -1]]})
                else:
                    message = self.snapshot()
                    message["counts"].append(["social", "closed_early", 1])
                    parent.incoming.append(message)
                with self.assertRaisesRegex(workers.SocketWorkerError, "failed"):
                    await group.wait_ready(timeout=.01)
                self.assertEqual(group.counts[("social", "connected")], 2)
                self.assertFalse(group.worker_status[0]["metrics_complete"])
                await group.stop()

    async def test_done_retains_last_counters_but_cannot_certify_live_receivers(self):
        group, context = await self.group()
        message = self.snapshot(9)
        message["type"] = "done"
        context.pairs[0][0].incoming.append(message)
        group.refresh()
        self.assertEqual(group.counts[("social", "frames")], 9)
        self.assertTrue(group.worker_status[0]["finished"])
        self.assertTrue(group.worker_status[0]["metrics_complete"])
        self.assertEqual(group.counts[("worker", "unexpected_completion")], 1)
        with self.assertRaises(workers.SocketWorkerError):
            await group.wait_ready(timeout=.01)

    async def test_graceful_stop_closes_pipes_and_process_handles_once(self):
        group, context = await self.group()
        for parent, _child in context.pairs:
            parent.incoming.append(self.snapshot(6))
        group.refresh()
        await group.stop()
        await group.stop()
        self.assertEqual(group.counts[("social", "frames")], 12)
        self.assertFalse(group.faults)
        for process, (parent, child) in zip(context.processes, context.pairs):
            self.assertEqual(parent.sent, ["stop"])
            self.assertTrue(parent.closed and child.closed and process.closed)
            self.assertEqual(process.joins, [0])
        self.assertTrue(group.monitor.done())
        self.assertTrue(all(item["exitcode"] == 0 for item in group.worker_status))
        self.assertTrue(all(item["metrics_complete"] for item in group.worker_status))

    async def test_unresponsive_child_escalates_with_bounded_waits_and_cleans_handles(self):
        group, context = await self.group(graceful=False, resist_terminate=True)
        with patch.object(group, "_wait_exit", new=AsyncMock(side_effect=[False, False, True])) as wait:
            await group.stop()
        self.assertEqual([call.args[0] for call in wait.call_args_list], [10, 3, 2])
        self.assertTrue(all(process.terminated and process.killed and process.closed for process in context.processes))
        self.assertTrue(all(parent.closed and child.closed for parent, child in context.pairs))
        self.assertEqual(group.counts[("worker", "forced_terminate")], 2)

    async def test_partial_spawn_failures_leave_no_process_or_pipe_handle(self):
        for failure_option in ("start_error_on", "construct_error_on"):
            with self.subTest(failure=failure_option):
                context = Context(**{failure_option: 2})
                group = workers.SocketWorkerGroup(self.runtime, "http://127.0.0.1:18767", self.specs,
                                                  workers=2, context=context)
                self.groups.append(group)
                with self.assertRaisesRegex(RuntimeError, "synthetic"):
                    await group.start()
                self.assertTrue(all(parent.closed and child.closed for parent, child in context.pairs))
                self.assertTrue(all(process.closed and not process.is_alive() for process in context.processes))
                self.assertEqual(group.counts[("worker", "spawn_error")], 1)


class WorkerBoundaryTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.make_fixture()

    def test_synthetic_fixture_and_explicit_loopback_origin_and_paths_are_required(self):
        self.assertEqual(self.runtime, workers._validate_spec(self.runtime, "http://127.0.0.1:18767", self.specs))
        for origin in ("https://example.com:18767", "http://localhost:18767", "http://127.0.0.1",
                       "http://u:p@127.0.0.1:18767", "http://127.0.0.1:18767/prod", "ftp://127.0.0.1:18767"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                workers._validate_spec(self.runtime, origin, self.specs)
        for change in ({"path": "/ws/8"}, {"path": "//example.com/ws"}, {"token": "cookie\r\nInjected: x"}, {"channel": "other"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                workers._validate_spec(self.runtime, "http://127.0.0.1:18767", [{**self.specs[0], **change}])
        self.fixture["uiV3Synthetic"] = False
        self.write_fixture()
        with self.assertRaises(ValueError):
            workers._validate_spec(self.runtime, "http://127.0.0.1:18767", self.specs)

    def test_owned_certificate_is_required_for_tls_and_not_used_for_http(self):
        certificate = self.runtime / "synthetic-ca.pem"
        certificate.write_text("synthetic certificate marker", encoding="utf-8")
        workers._validate_spec(self.runtime, "https://127.0.0.1:18769", self.specs, certificate)
        with self.assertRaises(ValueError):
            workers._validate_spec(self.runtime, "http://127.0.0.1:18767", self.specs, certificate)
        with self.assertRaises((ValueError, FileNotFoundError)):
            workers._validate_spec(self.runtime, "https://127.0.0.1:18769", self.specs)

    def test_handshake_limit_rejects_ambiguous_or_negative_values(self):
        for value in (-1, True, False, 1.5, "8", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workers.SocketWorkerGroup(self.runtime, "http://127.0.0.1:18767", self.specs, connect_limit=value)

    def test_native_fault_diagnostic_retains_codes_but_scrubs_credentials_and_is_bounded(self):
        token = "synthetic-secret-token"
        error = ConnectionRefusedError(10061, "access_token=" + token + "; authorization: other-secret " + "长" * 400)
        error.winerror = 10061
        detail = workers.socket_fault_detail(error, channel="social", phase="connect",
                    target="ws://127.0.0.1:18767/ws/social", tokens=[token])
        self.assertEqual(detail["winerror"], 10061)
        self.assertEqual(detail["errno"], 10061)
        self.assertEqual(detail["error"], "ConnectionRefusedError")
        self.assertNotIn(token, detail["detail"])
        self.assertNotIn("other-secret", detail["detail"])
        self.assertLessEqual(len(detail["detail"]), 200)


class HandshakeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cap_limits_only_handshake_while_more_established_sockets_remain_live(self):
        pipe = Connection()
        release, two_started, ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active = peak = calls = 0
        sockets = []

        class Socket:
            def __init__(self):
                self.closed = False
                self.first = True
                self.wait = asyncio.Event()
            def __aiter__(self):
                return self
            async def __anext__(self):
                if self.first:
                    self.first = False
                    return json.dumps({"type": "hello"})
                await self.wait.wait()
                raise StopAsyncIteration
            async def close(self):
                self.closed = True

        async def connect(*_args, **_kwargs):
            nonlocal active, peak, calls
            active += 1
            calls += 1
            peak = max(peak, active)
            if calls >= 2:
                two_started.set()
            try:
                await release.wait()
                value = Socket()
                sockets.append(value)
                return value
            finally:
                active -= 1

        def snapshot(message):
            if message.get("ready"):
                ready.set()
        pipe.on_send = snapshot
        specs = [{"token": f"synthetic-{index}", "channel": "social", "path": "/ws/social"} for index in range(6)]
        with patch("websockets.connect", side_effect=connect):
            task = asyncio.create_task(workers._receive_sockets(pipe, "http://127.0.0.1:18767", specs, None, 2))
            try:
                await asyncio.wait_for(two_started.wait(), 1)
                await asyncio.sleep(0)
                self.assertEqual(calls, 2)
                self.assertEqual(peak, 2)
                release.set()
                await asyncio.wait_for(ready.wait(), 2)
                self.assertEqual(calls, 6)
                self.assertEqual(len(sockets), 6)
                self.assertTrue(all(not item.closed for item in sockets))
                last = pipe.sent[-1]
                self.assertEqual(last["timing"]["handshake_peak"], 2)
                self.assertEqual(last["timing"]["handshake_current"], 0)
                self.assertEqual(last["timing"]["handshakes_started"], 6)
                self.assertEqual(last["timing"]["handshakes_finished"], 6)
                self.assertEqual(last["timing"]["handshakes_cancelled"], 0)
            finally:
                release.set()
                pipe.incoming.append("stop")
                await asyncio.wait_for(task, 2)
        self.assertTrue(all(item.closed for item in sockets))

    async def test_abort_distinguishes_active_cancelled_handshakes_from_not_started_connectors(self):
        pipe = Connection()
        started = asyncio.Event()
        calls = 0
        async def connect(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                started.set()
            await asyncio.Event().wait()
        specs = [{"token": f"synthetic-{index}", "channel": "social", "path": "/ws/social"} for index in range(6)]
        with patch("websockets.connect", side_effect=connect):
            task = asyncio.create_task(workers._receive_sockets(pipe, "http://127.0.0.1:18767", specs, None, 2))
            try:
                await asyncio.wait_for(started.wait(), 1)
            finally:
                pipe.incoming.append("stop")
                await asyncio.wait_for(task, 2)
        self.assertEqual(calls, 2)
        done = pipe.sent[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["timing"]["handshakes_started"], 2)
        self.assertEqual(done["timing"]["handshakes_finished"], 0)
        self.assertEqual(done["timing"]["handshakes_cancelled"], 2)
        self.assertEqual(done["timing"]["connectors_cancelled"], 6)
        self.assertEqual(done["timing"]["handshake_current"], 0)
        self.assertFalse(done["faults"])
        self.assertFalse(done["counts"])

    async def test_connection_refusal_is_attempted_once_and_retains_redacted_native_evidence(self):
        pipe = Connection()
        reported = asyncio.Event()
        token = "synthetic-secret-only"
        error = ConnectionRefusedError(10061, "access_token=" + token)
        error.winerror = 10061
        def on_send(message):
            if message.get("faults"):
                reported.set()
        pipe.on_send = on_send
        with patch("websockets.connect", side_effect=error) as connect:
            task = asyncio.create_task(workers._receive_sockets(pipe, "http://127.0.0.1:18767",
                    [{"token": token, "channel": "social", "path": "/ws/social"}], None, 1))
            try:
                await asyncio.wait_for(reported.wait(), 2)
                self.assertEqual(connect.call_count, 1)
                last = pipe.sent[-1]
                self.assertFalse(last["ready"])
                self.assertEqual(last["faults"][0]["winerror"], 10061)
                self.assertNotIn(token, json.dumps(last))
            finally:
                pipe.incoming.append("stop")
                await asyncio.wait_for(task, 2)

class ChildBoundaryTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.make_fixture()

    def test_child_boundary_removes_credentials_and_rejects_external_connect_and_dns(self):
        environment = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                       "PRIVATE_API_KEY": "must-be-removed", "HTTPS_PROXY": "http://external.invalid",
                       "DATABASE_URL": "postgresql://external.invalid/never-connect"}
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(socket.socket, "connect", return_value="local") as connect, \
             patch.object(socket.socket, "connect_ex", return_value=0) as connect_ex, \
             patch.object(socket, "getaddrinfo", return_value=[]) as dns:
            workers._install_child_boundary(self.runtime)
            self.assertNotIn("PRIVATE_API_KEY", os.environ)
            self.assertNotIn("HTTPS_PROXY", os.environ)
            self.assertEqual(os.environ["DATABASE_URL"], "")
            self.assertEqual(os.environ["LANSHARE_DATA_ROOT"], str(self.runtime))
            self.assertEqual(socket.socket.connect(object(), ("127.0.0.1", 18767)), "local")
            self.assertEqual(socket.socket.connect_ex(object(), ("::1", 18767)), 0)
            socket.getaddrinfo("localhost", 18767)
            for operation in (lambda: socket.socket.connect(object(), ("example.com", 443)),
                              lambda: socket.socket.connect_ex(object(), ("8.8.8.8", 53)),
                              lambda: socket.getaddrinfo("example.com", 443)):
                with self.assertRaisesRegex(RuntimeError, "external"):
                    operation()
            connect.assert_called_once()
            connect_ex.assert_called_once()
            dns.assert_called_once()
