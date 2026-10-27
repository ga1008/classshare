"""统一在线 (social doc §8): connect/disconnect grace, classroom hook, 散修 simulation, persistence."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from classroom_app.services import chat_handler, presence_service
from classroom_app.services.wanderer_identity_service import invalidate_wanderer_cache, list_wanderers
from tests.social_test_support import SocialDbTestCase


class SocialPresenceTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.changes: list[tuple[str, bool]] = []

        async def listener(identity, online, last_seen_at):
            self.changes.append((identity, online))

        presence_service.add_listener(listener)
        self.addCleanup(presence_service._listeners.remove, listener)

    def test_connect_disconnect_with_grace(self):
        async def scenario():
            with patch.object(presence_service, "OFFLINE_GRACE_SECONDS", 0.05):
                self.assertTrue(presence_service.connection_opened(self.a, "c1", "social_ws"))
                self.assertFalse(presence_service.connection_opened(self.a, "c2", "classroom_ws"))
                presence_service.connection_closed(self.a, "c1")
                presence_service.connection_closed(self.a, "c2")
                self.assertTrue(presence_service.is_connected(self.a))  # inside the grace window
                # Reconnecting inside the grace is silent: no offline, no second online.
                self.assertFalse(presence_service.connection_opened(self.a, "c3", "social_ws"))
                presence_service.connection_closed(self.a, "c3")
                await asyncio.sleep(0.15)
                self.assertFalse(presence_service.is_connected(self.a))
                await asyncio.sleep(0.05)

        asyncio.run(scenario())
        self.assertEqual(self.changes, [(self.a, True), (self.a, False)])
        self.assertEqual(self.count("SELECT COUNT(*) FROM user_presence WHERE identity = ?", (self.a,)), 1)
        with self.connect() as conn:
            state = presence_service.presence_map(conn, [self.a])[self.a]
        self.assertFalse(state["online"])
        self.assertTrue(state["last_seen_at"])

    def test_snapshot_counts_sources(self):
        presence_service.connection_opened(self.a, "s1", "social_ws")
        presence_service.connection_opened(self.b, "k1", "classroom_ws")
        snapshot = presence_service.snapshot()
        self.assertEqual(snapshot["online_count"], 2)
        self.assertEqual(snapshot["by_source"], {"social_ws": 1, "classroom_ws": 1})

    def test_classroom_socket_reports_into_presence(self):
        user = {"id": f"student_{self.student_ids[2]}", "role": "student", "name": "丙同学"}
        chat_handler._report_presence(user, "room-conn", opened=True)
        self.assertTrue(presence_service.is_connected(self.c))
        chat_handler._report_presence({"id": "garbage", "role": "student"}, "x", opened=True)  # ignored, no raise
        chat_handler._report_presence(user, "room-conn", opened=False)
        self.assertFalse(presence_service.is_connected(self.c))  # no running loop: dropped at once

    def test_wanderer_simulated_online(self):
        from classroom_app.services.wanderer_persona_seeds import seed_wanderers

        with self.connect() as conn:
            seed_wanderers(conn)
            conn.commit()
            invalidate_wanderer_cache()
            wanderer = list_wanderers(conn)[0]
        identity = f"student:{wanderer['student_id']}"
        with self.connect() as conn:
            with patch.object(presence_service, "_in_active_hours", return_value=True):
                self.assertTrue(presence_service.is_online(conn, identity))
            with patch.object(presence_service, "_in_active_hours", return_value=False):
                self.assertFalse(presence_service.is_online(conn, identity))
            conn.execute("UPDATE wanderer_agents SET status = 'paused' WHERE id = ?", (wanderer["id"],))
            conn.commit()
            invalidate_wanderer_cache()
            with patch.object(presence_service, "_in_active_hours", return_value=True):
                self.assertFalse(presence_service.is_online(conn, identity))

    def test_presence_endpoint(self):
        self.befriend(self.a, self.b)
        presence_service.connection_opened(self.b, "s1", "social_ws")
        presence_service.connection_opened(self.c, "s2", "social_ws")
        client = self.client_as(self.a)
        items = client.get("/api/social/presence", params={"identities": f"{self.b},{self.c},bad"}).json()["items"]
        self.assertEqual(set(items), {self.b, self.c})
        self.assertTrue(items[self.b]["online"])
        # c is a stranger: online state is not disclosed at all.
        self.assertEqual(items[self.c], {"online": None, "last_seen_at": None})


if __name__ == "__main__":
    unittest.main()
