import asyncio
import json
import sys
import types
import unittest
from unittest.mock import patch

from classroom_app.routers import polls


class PollBroadcastDeadlineTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, broadcast):
        module = types.ModuleType("classroom_app.services.chat_handler")
        module.manager = types.SimpleNamespace(broadcast=broadcast, rooms={1: {"client": object()}})
        return patch.dict(sys.modules, {module.__name__: module})

    async def test_stalled_socket_is_canceled_without_failing_committed_poll_request(self):
        canceled = asyncio.Event()
        async def stalled(room, message):
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()
        with self.manager(stalled), patch.object(polls, "POLL_BROADCAST_TIMEOUT_SECONDS", 0.02), patch.object(polls, "record_websocket_sent") as metrics:
            await asyncio.wait_for(polls._broadcast_poll_changed([1], reason="poll_vote", poll_id=7), timeout=1)
        self.assertTrue(canceled.is_set())
        metrics.assert_not_called()

    async def test_deadline_bounds_whole_fanout_instead_of_restarting_per_classroom(self):
        started, delivered = [], []
        async def delayed(room, message):
            started.append(room)
            await asyncio.sleep(0.025)
            delivered.append(room)
        with self.manager(delayed), patch.object(polls, "POLL_BROADCAST_TIMEOUT_SECONDS", 0.04), patch.object(polls, "record_websocket_sent"):
            await polls._broadcast_poll_changed([1, 2, 3], reason="poll_vote")
        self.assertLess(len(delivered), 3)
        self.assertGreater(len(started), len(delivered))

    async def test_one_failed_socket_does_not_lose_other_classroom_notifications(self):
        calls = []
        async def deliver(room, message):
            calls.append((room, json.loads(message)))
            if room == 1:
                raise ConnectionError("synthetic disconnected socket")
        with self.manager(deliver), patch.object(polls, "record_websocket_sent") as metrics:
            await polls._broadcast_poll_changed([1, 2, 2], reason="poll_vote", poll_id=7)
        self.assertEqual({1, 2}, {room for room, payload in calls})
        self.assertEqual(2, len(calls))
        self.assertTrue(all(payload["poll_id"] == 7 for room, payload in calls))
        metrics.assert_called_once_with(2, 1)

    async def test_external_cancellation_is_not_swallowed_as_a_broadcast_timeout(self):
        started = asyncio.Event()
        async def stalled(room, message):
            started.set()
            await asyncio.Event().wait()
        with self.manager(stalled):
            work = asyncio.create_task(polls._broadcast_poll_changed([1], reason="poll_vote"))
            await started.wait()
            work.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await work
