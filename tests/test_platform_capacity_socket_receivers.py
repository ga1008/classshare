"""Exercise socket receiver protocol with fake WebSockets and real OS pipes."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.platform_capacity_socket_workers import ROOT, SocketWorkerGroup, _receive_sockets


class FakeSocket:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.frames.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self):
        self.closed = True


class ReceiverProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.parent, self.child = multiprocessing.Pipe(duplex=True)
        self.sockets = {"/ws/7": FakeSocket(), "/ws/social": FakeSocket()}
        self.specs = [{"path": path, "channel": channel, "token": "synthetic-token"}
                      for path, channel in (("/ws/7", "classroom"), ("/ws/social", "social"))]
        self.task = None

    async def asyncTearDown(self):
        if self.task and not self.task.done():
            self.parent.send("stop")
            await asyncio.wait_for(self.task, 2)
        self.parent.close()
        self.child.close()

    async def read_until(self, predicate):
        async with asyncio.timeout(2):
            while True:
                if self.parent.poll():
                    message = self.parent.recv()
                    if predicate(message):
                        return message
                await asyncio.sleep(.01)

    @staticmethod
    def counts(message):
        return Counter({(channel, event): count for channel, event, count in message["counts"]})

    async def start(self):
        async def connect(url, **kwargs):
            self.assertIsNone(kwargs["proxy"])
            self.assertEqual("access_token=synthetic-token", kwargs["additional_headers"]["Cookie"])
            return self.sockets[url.removeprefix("ws://127.0.0.1:18767")]

        self.connector = patch("websockets.connect", side_effect=connect)
        self.connector.start()
        self.addCleanup(self.connector.stop)
        self.task = asyncio.create_task(_receive_sockets(self.child, "http://127.0.0.1:18767", self.specs, None))

    async def test_ready_requires_protocol_hello_and_history_then_stop_preserves_final_counts(self):
        await self.start()
        self.sockets["/ws/7"].frames.put_nowait(json.dumps({"type": "hello"}))
        self.sockets["/ws/social"].frames.put_nowait(json.dumps({"type": "message", "message": {"id": 5}}))
        early = await self.read_until(lambda message: self.counts(message)[("social", "frames")] == 1)
        self.assertFalse(early["ready"])
        self.assertEqual(self.counts(early)[("classroom", "application_ready")], 0)
        self.sockets["/ws/7"].frames.put_nowait(json.dumps({"type": "history"}))
        self.sockets["/ws/social"].frames.put_nowait(json.dumps({"type": "hello"}))
        self.sockets["/ws/social"].frames.put_nowait(json.dumps({"type": "hello"}))
        ready = await self.read_until(lambda message: message["ready"])
        self.assertEqual(self.counts(ready)[("social", "application_ready")], 1)
        self.parent.send("stop")
        await asyncio.wait_for(self.task, 2)
        done = await self.read_until(lambda message: message["type"] == "done")
        counts = self.counts(done)
        self.assertEqual(counts[("classroom", "frames")], 2)
        self.assertEqual(counts[("social", "frames")], 3)
        self.assertFalse(any(count for (_, event), count in counts.items() if event == "closed_early"))
        self.assertTrue(all(socket.closed for socket in self.sockets.values()))

    async def test_malformed_frame_and_normal_early_close_are_both_reported(self):
        await self.start()
        self.sockets["/ws/7"].frames.put_nowait("not JSON")
        self.sockets["/ws/social"].frames.put_nowait(None)
        snapshot = await self.read_until(lambda message: self.counts(message)[("social", "closed_early")] == 1)
        counts = self.counts(snapshot)
        self.assertFalse(snapshot["ready"])
        self.assertEqual(counts[("classroom", "JSONDecodeError")], 1)
        self.assertEqual(counts[("classroom", "closed_early")], 1)
        self.assertEqual(counts[("social", "closed_early")], 1)
        self.assertEqual(counts[("classroom", "connected")], 1)
        self.assertEqual(counts[("social", "connected")], 1)


class SpawnLoopbackSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_spawn_children_receive_fake_loopback_server_frames_and_exit_cleanly(self):
        from websockets.asyncio.server import serve

        temporary = ROOT / '.codex-temp'
        temporary.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='socket-worker-test-', dir=temporary) as directory:
            runtime = Path(directory)
            (runtime / 'fixture.json').write_text(json.dumps({
                'uiV3Synthetic': True, 'databasePath': str(runtime / 'db/classroom.db'), 'classOfferingId': 7,
            }), encoding='utf-8')

            established = set()
            async def fake_server(websocket):
                established.add(websocket)
                try:
                    frame = 'history' if websocket.request.path == '/ws/7' else 'hello'
                    await websocket.send(json.dumps({'type': frame}))
                    await websocket.wait_closed()
                finally:
                    established.remove(websocket)

            async with serve(fake_server, '127.0.0.1', 0) as server:
                port = server.sockets[0].getsockname()[1]
                specs = [{'token': f'synthetic-{identity}', 'path': path, 'channel': channel}
                         for identity in range(2)
                         for path, channel in (('/ws/7', 'classroom'), ('/ws/social', 'social'))]
                group = SocketWorkerGroup(runtime, f'http://127.0.0.1:{port}', specs, workers=2, connect_limit=1)
                try:
                    await group.start()
                    await group.wait_ready(timeout=10)
                    # Both children release their one handshake slot while
                    # retaining all four established connections.
                    self.assertEqual(len(established), 4)
                finally:
                    await group.stop()
                for channel in ('classroom', 'social'):
                    self.assertEqual(group.counts[(channel, 'connected')], 2)
                    self.assertEqual(group.counts[(channel, 'application_ready')], 2)
                    self.assertEqual(group.counts[(channel, 'frames')], 2)
                self.assertFalse(any(count for (_, event), count in group.counts.items()
                                     if event not in {'connected', 'application_ready', 'frames'}))
                self.assertEqual(len(group.worker_status), 2)
                self.assertEqual(len({item['pid'] for item in group.worker_status}), 2)
                self.assertTrue(all(item['pid'] != os.getpid() for item in group.worker_status))
                self.assertTrue(all(item['metrics_complete'] and item['exitcode'] == 0 for item in group.worker_status))
                self.assertTrue(all(item['handshake_peak'] == 1 for item in group.worker_status))
                self.assertEqual(group.timing['aggregate_handshake_limit'], 2)
                self.assertEqual(group.timing['handshakes_started'], 4)
                self.assertEqual(group.timing['handshakes_finished'], 4)
                self.assertEqual(group.timing['not_started_sockets'], 0)
                self.assertLessEqual(group.timing['first_application_ready_ms'], group.timing['last_application_ready_ms'])
                self.assertEqual(group.fault_details, [])
