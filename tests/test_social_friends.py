"""好友与黑名单 (docs/social-friends-groups-2026-10-27.md §2, §3, §7)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_identity_service as identities
from classroom_app.services.social_common import SocialError, dm_key
from tests.social_test_support import SocialDbTestCase


class SocialFriendLifecycleTest(SocialDbTestCase):
    def test_request_accept_creates_welcome_message_notifications_and_events(self):
        result, events = self.run_service(friends.send_request, self.user(self.a), self.b, "一起学习吧")
        self.assertEqual(result["status"], "pending")
        self.assertEqual([event["type"] for _, event in events], ["friend_request"])
        incoming, _ = self.run_service(friends.list_requests, self.user(self.b))
        self.assertEqual(incoming["incoming"][0]["user"]["identity"], self.a)
        self.assertEqual(incoming["incoming"][0]["message"], "一起学习吧")
        self.assertNotIn("email", incoming["incoming"][0]["user"])

        accepted, events = self.run_service(friends.accept_request, self.user(self.b), result["request_id"])
        self.assertEqual(accepted["status"], "accepted")
        kinds = sorted(event["type"] for _, event in events)
        self.assertEqual(kinds, ["friend_accepted", "friend_accepted", "message"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_messages WHERE conversation_key = ? "
                                    "AND message_type = 'system'", (dm_key(self.a, self.b),)), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM message_center_notifications WHERE category = 'social'"), 3)
        listing, _ = self.run_service(friends.list_friends, self.user(self.a))
        self.assertEqual(listing["counts"]["total"], 1)
        card = listing["items"][0]
        for key in ("identity", "name", "remark", "role_label", "avatar_url", "is_wanderer", "tier_badge", "mood",
                    "online", "last_seen_at", "conversation_key", "unread", "homepage_url"):
            self.assertIn(key, card)
        self.assertEqual(card["unread"], 0)  # the system welcome line is never unread

    def test_mutual_request_auto_accepts(self):
        self.run_service(friends.send_request, self.user(self.a), self.b)
        result, events = self.run_service(friends.send_request, self.user(self.b), self.a)
        self.assertEqual(result["status"], "accepted")
        self.assertIn("friend_accepted", [event["type"] for _, event in events])

    def test_only_addressee_handles_and_requester_withdraws(self):
        result, _ = self.run_service(friends.send_request, self.user(self.a), self.b)
        with self.assertRaises(SocialError) as ctx:
            self.run_service(friends.accept_request, self.user(self.a), result["request_id"])
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(SocialError):
            self.run_service(friends.withdraw_request, self.user(self.b), result["request_id"])
        self.run_service(friends.withdraw_request, self.user(self.a), result["request_id"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_friendships"), 0)

    def test_decline_cooldown_blocks_re_request_for_seven_days(self):
        result, _ = self.run_service(friends.send_request, self.user(self.a), self.b)
        self.run_service(friends.decline_request, self.user(self.b), result["request_id"])
        with self.assertRaises(SocialError) as ctx:
            self.run_service(friends.send_request, self.user(self.a), self.b)
        self.assertEqual(ctx.exception.status_code, 429)
        # The person who declined can still reach out.
        again, _ = self.run_service(friends.send_request, self.user(self.b), self.a)
        self.assertEqual(again["status"], "pending")

    def test_self_and_ai_identities_rejected(self):
        with self.assertRaises(SocialError):
            self.run_service(friends.send_request, self.user(self.a), self.a)
        with self.assertRaises(SocialError):
            self.run_service(friends.send_request, self.user(self.a), "assistant:3")

    def test_daily_request_limit(self):
        with patch.object(friends, "DAILY_REQUEST_LIMIT", 2):
            self.run_service(friends.send_request, self.user(self.a), self.b)
            self.run_service(friends.send_request, self.user(self.a), self.c)
            with self.assertRaises(SocialError) as ctx:
                self.run_service(friends.send_request, self.user(self.a), self.d)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_friend_limit(self):
        with patch.object(friends, "FRIEND_LIMIT", 1):
            self.befriend(self.a, self.b)
            with self.assertRaises(SocialError) as ctx:
                self.run_service(friends.send_request, self.user(self.a), self.c)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_delete_and_remark(self):
        self.befriend(self.a, self.b)
        self.run_service(friends.set_remark, self.user(self.a), self.b, "同桌")
        listing, _ = self.run_service(friends.list_friends, self.user(self.a))
        self.assertEqual(listing["items"][0]["remark"], "同桌")
        other, _ = self.run_service(friends.list_friends, self.user(self.b))
        self.assertEqual(other["items"][0]["remark"], "")
        _, events = self.run_service(friends.delete_friend, self.user(self.a), self.b)
        self.assertEqual({event["type"] for _, event in events}, {"friend_removed"})
        # History stays after unfriending.
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_messages"), 1)


class SocialBlockLinkageTest(SocialDbTestCase):
    def test_block_ends_friendship_and_refuses_uniformly_both_ways(self):
        self.befriend(self.a, self.b)
        self.run_service(friends.block_user, self.user(self.a), self.b)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_friendships"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM private_message_blocks"), 1)
        for sender, target in ((self.b, self.a), (self.a, self.b)):
            with self.assertRaises(SocialError) as ctx:
                self.run_service(friends.send_request, self.user(sender), target)
            self.assertEqual(ctx.exception.detail, friends.UNIFORM_REFUSAL)

    def test_block_drops_pending_request_and_unblock_restores(self):
        self.run_service(friends.send_request, self.user(self.b), self.a)
        self.run_service(friends.block_user, self.user(self.a), self.b)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_friendships"), 0)
        blocks, _ = self.run_service(friends.list_blocks, self.user(self.a))
        self.assertEqual(blocks[0]["identity"], self.b)
        self.run_service(friends.unblock_user, self.user(self.a), self.b)
        result, _ = self.run_service(friends.send_request, self.user(self.b), self.a)
        self.assertEqual(result["status"], "pending")

    def test_relation_reports_my_block_but_never_theirs(self):
        self.run_service(friends.block_user, self.user(self.a), self.b)
        with self.connect() as conn:
            mine = identities.public_users(conn, self.a, [self.b])[0]
            theirs = identities.public_users(conn, self.b, [self.a])[0]
        self.assertEqual(mine["relation"], "blocked")
        self.assertEqual(theirs["relation"], "none")


class SocialFriendApiTest(SocialDbTestCase):
    def test_http_contract(self):
        client = self.client_as(self.a)
        response = client.post("/api/social/friends/requests", json={"target_identity": self.b, "message": "hi"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()), {"status", "request_id"})
        self.assertEqual(client.get("/api/social/friends/requests").json()["outgoing"][0]["user"]["identity"], self.b)
        search = client.get("/api/social/users/search", params={"q": "乙"}).json()["items"]
        self.assertEqual(search[0]["relation"], "pending_out")
        self.assertEqual(set(search[0]), {"identity", "name", "role_label", "org_label", "avatar_url",
                                          "is_wanderer", "tier_badge", "relation"})
        refused = client.post("/api/social/friends/requests", json={"target_identity": self.a})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("detail", refused.json())
        profile = client.get(f"/api/social/users/{self.b}/profile").json()
        for key in ("mood", "online", "last_seen_at", "remark", "cultivation", "recent", "private_message_url"):
            self.assertIn(key, profile)
        self.assertEqual(client.post("/api/social/blocks", json={"identity": self.c}).status_code, 200)
        self.assertEqual(client.get("/api/social/blocks").json()["items"][0]["identity"], self.c)
        self.assertEqual(client.delete(f"/api/social/blocks/{self.c}").status_code, 200)
        suggestions = client.get("/api/social/users/suggestions").json()["items"]
        self.assertIn(self.c, [item["identity"] for item in suggestions])
        self.assertNotIn(self.b, [item["identity"] for item in suggestions])


if __name__ == "__main__":
    unittest.main()
