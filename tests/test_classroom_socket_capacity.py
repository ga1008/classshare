import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from classroom_app.services import chat_handler as chat


class ClassroomSocketCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_hundred_simultaneous_presence_changes_share_one_roster(self):
        manager = chat.MultiRoomConnectionManager()
        manager.ensure_room(1)
        for index in range(200):
            key = f"student_{index}"
            manager.room_participants[1][key] = {key}
            manager.room_user_info[1][key] = {"name": key, "role": "student"}
        manager.broadcast = AsyncMock()
        manager.broadcast_alias_states = AsyncMock()
        await asyncio.gather(*(manager.refresh_room_presence(1) for _ in range(200)))
        self.assertEqual(1, manager.broadcast.await_count)
        payload = json.loads(manager.broadcast.await_args.args[1])
        self.assertEqual(200, len(payload["data"]))
        self.assertEqual({"name", "role"}, set(payload["data"][0]))
        self.assertEqual(1, manager.broadcast_alias_states.await_count)
        self.assertFalse(manager.presence_tasks)

    async def test_slow_socket_is_closed_while_healthy_peer_receives_message(self):
        manager = chat.MultiRoomConnectionManager()
        manager.ensure_room(1)
        blocked = AsyncMock()
        blocked.send_text.side_effect = lambda *_: asyncio.sleep(30)
        # AsyncMock does not await a coroutine returned by a lambda itself.
        async def stall(*_):
            await asyncio.sleep(30)
        blocked.send_text.side_effect = stall
        healthy = AsyncMock()
        manager.rooms[1] = {"slow": blocked, "healthy": healthy}
        manager.connection_state["slow"] = {"room_id": 1, "participant_key": "student_1", "user": {}}
        manager.room_participants[1] = {"student_1": {"slow"}}
        with patch.object(chat, "SOCKET_SEND_TIMEOUT_SECONDS", .02), patch.object(chat, "_report_presence"):
            await manager.broadcast(1, '{"type":"test"}')
        healthy.send_text.assert_awaited_once()
        blocked.close.assert_awaited_once_with(code=1013)
        self.assertNotIn("slow", manager.rooms[1])
        self.assertNotIn("student_1", manager.room_participants[1])

    async def test_failed_initial_handshake_removes_registration_before_returning(self):
        manager = chat.MultiRoomConnectionManager()
        websocket = AsyncMock()
        websocket.path_params = {"class_offering_id": 1}
        async def stall(*_):
            await asyncio.sleep(30)
        websocket.send_text.side_effect = stall
        with patch.object(chat, "SOCKET_SEND_TIMEOUT_SECONDS", .02), patch.object(chat, "_report_presence"):
            with self.assertRaises(TimeoutError):
                await manager.connect(websocket, {"id": "student_1", "name": "甲", "role": "student"})
        self.assertFalse(manager.connection_state)
        self.assertFalse(manager.rooms[1])
        self.assertFalse(manager.room_user_info[1])
        self.assertFalse(manager.room_alias_sessions[1])
        websocket.close.assert_awaited_once_with(code=1013)


if __name__ == "__main__":
    unittest.main()
