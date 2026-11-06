"""Bounded social FIFO delivery, using synthetic sockets and no database."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from classroom_app.services import social_push_service as push


class Socket:
    def __init__(self, gate=None):
        self.gate = gate
        self.started = asyncio.Event()
        self.sent = []
        self.closed = []

    async def send_text(self, text):
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        self.sent.append(json.loads(text))

    async def close(self, code):
        self.closed.append(code)


class OutboundTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        push.reset_for_tests()
        self.state = dict(push._state)
        push.mark_app_process()

    async def asyncTearDown(self):
        await push.stop_outbound_writers()
        push.reset_for_tests()
        push._state.update(self.state)

    async def test_slow_socket_cannot_delay_another_recipient(self):
        gate = asyncio.Event()
        slow, fast = Socket(gate), Socket()
        push.register("student:1", "slow", slow)
        push.register("student:2", "fast", fast)
        delivery = asyncio.create_task(push.deliver_local(["student:1", "student:2"], {"type": "pong"}))
        await asyncio.wait_for(fast.started.wait(), 1)
        self.assertFalse(delivery.done())
        gate.set()
        self.assertEqual(await delivery, 2)
        self.assertEqual(len(fast.sent), 1)

    async def test_commit_admission_does_not_wait_for_client_and_fifo_includes_control(self):
        gate = asyncio.Event()
        socket = Socket(gate)
        push.register("student:1", "c", socket)
        hello = push.queue_connection("student:1", "c", {"type": "hello"})
        self.assertEqual(await push.enqueue_committed_events([
            (["student:1"], {"type": "message", "message": {"id": 1}}),
            (["student:1"], {"type": "message", "message": {"id": 2}}),
        ]), 2)
        pong = push.queue_connection("student:1", "c", {"type": "pong"})
        self.assertFalse(hello.done())
        gate.set()
        self.assertTrue(await pong)
        self.assertTrue(await hello)
        self.assertEqual([item["type"] for item in socket.sent], ["hello", "message", "message", "pong"])
        self.assertEqual([item["message"]["id"] for item in socket.sent if item["type"] == "message"], [1, 2])

    async def test_overflow_closes_explicitly_and_completes_pending_false(self):
        socket = Socket(asyncio.Event())
        push.register("student:1", "c", socket)
        with patch.object(push, "MAX_OUTBOUND_FRAMES", 2):
            first = push.queue_connection("student:1", "c", {"type": "hello"})
            await socket.started.wait()
            second = push.queue_connection("student:1", "c", {"type": "pong"})
            third = push.queue_connection("student:1", "c", {"type": "pong"})
            self.assertEqual(await asyncio.gather(first, second, third), [False, False, False])
            await asyncio.gather(*list(push._closing_tasks))
        self.assertEqual(socket.closed, [1013])
        self.assertEqual(push.connection_count(), 0)
        self.assertEqual(push.outbound_snapshot()["overflow_disconnects"], 1)

    async def test_byte_bound_counts_utf8_and_internal_count_is_physical_delivery(self):
        socket = Socket()
        push.register("student:1", "c", socket)
        event = {"type": "message", "body": "学" * 5}
        chars = len(json.dumps(event, ensure_ascii=False))
        with patch.object(push, "MAX_OUTBOUND_BYTES", chars + 1):
            self.assertEqual(await push.deliver_local(["student:1"], event), 0)
            await asyncio.gather(*list(push._closing_tasks))
        self.assertEqual(socket.closed, [1013])
        self.assertEqual(socket.sent, [])

    async def test_replaced_writer_cannot_remove_reused_registration(self):
        old = Socket(asyncio.Event())
        new = Socket()
        push.register("student:1", "c", old)
        old_ack = push.queue_connection("student:1", "c", {"type": "hello"})
        await old.started.wait()
        push.register("student:1", "c", new)
        self.assertFalse(await old_ack)
        self.assertTrue(await push.send_connection("student:1", "c", {"type": "hello"}))
        self.assertEqual(push.connection_count(), 1)
        self.assertIs(push._connections["student:1"]["c"], new)

    async def test_hello_slot_buffers_commit_events_before_snapshot_finishes(self):
        socket = Socket()
        push.register("student:1", "c", socket, awaiting_hello=True)
        self.assertEqual(await push.enqueue_committed_events([
            (["student:1"], {"type": "message", "message": {"id": 3}}),
        ]), 1)
        await asyncio.sleep(0)
        self.assertEqual(socket.sent, [])
        hello = push.queue_connection("student:1", "c", {"type": "hello"}, initial=True)
        pong = push.queue_connection("student:1", "c", {"type": "pong"})
        self.assertTrue(await hello)
        self.assertTrue(await pong)
        self.assertEqual([item["type"] for item in socket.sent], ["hello", "message", "pong"])

    async def test_timeout_unregisters_and_shutdown_joins_all_writers(self):
        socket = Socket(asyncio.Event())
        push.register("student:1", "c", socket)
        with patch.object(push, "SEND_TIMEOUT_SECONDS", .01):
            self.assertEqual(await push.deliver_local(["student:1"], {"type": "pong"}), 0)
        self.assertEqual(socket.closed, [1011])
        await push.stop_outbound_writers()
        self.assertEqual(push.connection_count(), 0)
        self.assertEqual(push.outbound_snapshot()["writer_tasks"], 0)

    async def test_expired_queue_forces_recovery_instead_of_delivering_stale_backlog(self):
        gate = asyncio.Event()
        socket = Socket(gate)
        push.register("student:1", "c", socket)
        with patch.object(push, "monotonic", return_value=100.0):
            first = push.queue_connection("student:1", "c", {"type": "hello"})
            await socket.started.wait()
            second = push.queue_connection("student:1", "c", {"type": "pong"})
        with patch.object(push, "monotonic", return_value=106.0):
            gate.set()
            self.assertTrue(await first)
            self.assertFalse(await second)
        self.assertEqual([item["type"] for item in socket.sent], ["hello"])
        self.assertEqual(socket.closed, [1013])
        self.assertEqual(push.outbound_snapshot()["expired_disconnects"], 1)

    async def test_200_peer_join_storm_buffers_presence_without_consuming_message_budget(self):
        # This functional case delivers 40k in-memory presence frames. Match
        # the production loop: unittest's debug traceback capture can itself
        # consume the unchanged five-second critical-frame deadline.
        loop = asyncio.get_running_loop()
        previous_debug = loop.get_debug()
        loop.set_debug(False)
        try:
            await self._exercise_200_peer_join_storm()
        finally:
            loop.set_debug(previous_debug)

    async def _exercise_200_peer_join_storm(self):
        targets = [f"student:{index}" for index in range(200)]
        sockets = [Socket() for _ in targets]
        for identity, socket in zip(targets, sockets):
            push.register(identity, "c", socket, awaiting_hello=True)
        for identity in targets:
            self.assertEqual(await push.enqueue_committed_events([(targets, {
                "type": "presence", "identity": identity, "online": True, "last_seen_at": "first",
            })]), 200)
        # A newer state replaces only its own unsent mailbox entry.
        await push.enqueue_committed_events([(targets, {
            "type": "presence", "identity": targets[0], "online": False, "last_seen_at": "latest",
        })])
        await push.enqueue_committed_events([(targets, {"type": "message", "message": {"id": 7}})])
        snapshot = push.outbound_snapshot()
        self.assertEqual(snapshot["pending_critical_frames"], 400)  # hello + message per socket
        self.assertEqual(snapshot["pending_presence_entries"], 40_000)
        self.assertEqual(snapshot["overflow_disconnects"], 0)
        self.assertEqual(snapshot["presence_coalesced"], 200)
        await asyncio.gather(*[push.queue_connection(identity, "c", {"type": "hello"}, initial=True)
                               for identity in targets])
        # Confirmed presence is a tail fence for each independent mailbox.
        self.assertEqual(await push.deliver_local(targets, {
            "type": "presence", "identity": "teacher:1", "online": True,
        }), 200)
        for socket in sockets:
            self.assertEqual([item["type"] for item in socket.sent[:2]], ["hello", "message"])
            states = {item["identity"]: item for item in socket.sent if item["type"] == "presence"}
            self.assertEqual(len(states), 201)
            self.assertEqual((states[targets[0]]["online"], states[targets[0]]["last_seen_at"]), (False, "latest"))
            self.assertEqual(socket.closed, [])
        self.assertEqual(push.connection_count(), 200)
        self.assertEqual(push.outbound_snapshot()["pending_frames"], 0)

    async def test_coalesced_presence_ack_is_false_until_latest_state_physically_sends(self):
        socket = Socket()
        push.register("student:1", "c", socket, awaiting_hello=True)
        first = push.queue_connection("student:1", "c", {"type": "presence", "identity": "student:2", "online": True})
        second = push.queue_connection("student:1", "c", {"type": "presence", "identity": "student:2", "online": False})
        self.assertFalse(await first)
        self.assertFalse(second.done())
        self.assertTrue(await push.queue_connection("student:1", "c", {"type": "hello"}, initial=True))
        self.assertTrue(await second)
        self.assertEqual([item["type"] for item in socket.sent], ["hello", "presence"])
        self.assertFalse(socket.sent[-1]["online"])

    async def test_presence_mailbox_has_identity_and_shared_byte_bounds(self):
        socket = Socket()
        push.register("student:1", "c", socket, awaiting_hello=True)
        with patch.object(push, "MAX_PRESENCE_ENTRIES", 1):
            first = push.queue_connection("student:1", "c", {"type": "presence", "identity": "student:2"})
            second = push.queue_connection("student:1", "c", {"type": "presence", "identity": "student:3"})
            self.assertEqual(await asyncio.gather(first, second), [False, False])
            await asyncio.gather(*list(push._closing_tasks))
        self.assertEqual(socket.closed, [1013])
        socket = Socket()
        push.register("student:1", "d", socket, awaiting_hello=True)
        with patch.object(push, "MAX_OUTBOUND_BYTES", 180):
            # Each event fits individually; their shared budget does not.
            first = push.queue_connection("student:1", "d", {"type": "presence", "identity": "student:2", "value": "学" * 20})
            second = push.queue_connection("student:1", "d", {"type": "message", "body": "学" * 20})
            self.assertEqual(await asyncio.gather(first, second), [False, False])
            await asyncio.gather(*list(push._closing_tasks))
        self.assertEqual(socket.closed, [1013])

    async def test_presence_replacement_resizes_shared_utf8_budget_exactly(self):
        socket = Socket()
        push.register("student:1", "c", socket, awaiting_hello=True)
        writer = push._writers[("student:1", "c")]
        long = {"type": "presence", "identity": "student:2", "last_seen_at": "学" * 30}
        short = {**long, "last_seen_at": "s"}
        size = lambda event: len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
        with patch.object(push, "MAX_OUTBOUND_BYTES", size(long) + 40):
            first = push.queue_connection("student:1", "c", long)
            self.assertEqual(writer.bytes, size(long))
            second = push.queue_connection("student:1", "c", short)
            self.assertFalse(await first)
            self.assertEqual(writer.bytes, size(short))
            third = push.queue_connection("student:1", "c", long)
            self.assertFalse(await second)
            self.assertEqual(writer.bytes, size(long))
            self.assertEqual((writer.frames, writer.presence_frames), (1, 1))
            self.assertTrue(await push.queue_connection("student:1", "c", {"type": "hello"}, initial=True))
            self.assertTrue(await third)
        self.assertEqual(socket.closed, [])
        self.assertEqual((writer.frames, writer.presence_frames, writer.bytes), (0, 0, 0))

    async def test_unregister_with_inflight_presence_settles_all_acks_and_replacement_is_safe(self):
        old = Socket(asyncio.Event())
        push.register("student:1", "c", old)
        first = push.queue_connection("student:1", "c", {"type": "presence", "identity": "student:2"})
        await old.started.wait()
        writer = push._writers[("student:1", "c")]
        second = push.queue_connection("student:1", "c", {"type": "presence", "identity": "student:3"})
        control = push.queue_connection("student:1", "c", {"type": "pong"})
        replacement = Socket()
        push.register("student:1", "c", replacement)
        self.assertEqual(await asyncio.gather(first, second, control), [False, False, False])
        await asyncio.gather(writer.task, return_exceptions=True)
        self.assertEqual((writer.frames, writer.presence_frames, writer.bytes), (0, 0, 0))
        self.assertTrue(await push.send_connection("student:1", "c", {"type": "hello"}))
        self.assertIs(push._connections["student:1"]["c"], replacement)
        self.assertEqual(push.connection_count(), 1)


class AudienceVariantsTest(unittest.TestCase):
    def test_variants_keep_mine_and_muted_boundaries_with_single_shared_payload(self):
        from classroom_app.services import social_message_service as messages
        from classroom_app.services import social_notification_service as notifications

        viewers = [f"student:{index}" for index in range(200)]
        muted = {viewer: index % 2 == 0 for index, viewer in enumerate(viewers)}
        events = []
        message = {"id": 7, "sender": {"identity": viewers[0]}, "body": "合成消息"}
        with patch.object(messages, "participants", return_value=viewers), patch.object(
                notifications, "muted_map", return_value=muted):
            messages.push_message(events, object(), {"key": "group:1"}, message)
        expanded = {viewer: event["message"] for audience, event in events for viewer in audience}
        self.assertEqual(set(expanded), set(viewers))
        self.assertLessEqual(len(events), 4)
        for viewer in viewers:
            self.assertEqual(expanded[viewer]["mine"], viewer == viewers[0])
            self.assertEqual(expanded[viewer]["muted"], muted[viewer])
        self.assertNotIn("mine", message)
