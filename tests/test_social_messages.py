"""消息：权限、撤回、拍一拍、频率、幂等、@提醒、已读与未读 (docs/social-friends-groups-2026-10-27.md §5)."""
from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from classroom_app.services import social_conversation_service as conversations
from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_group_service as groups
from classroom_app.services import social_message_service as messages
from classroom_app.services.rate_limit_service import RateLimitExceededError
from classroom_app.services.social_common import SocialError, dm_key, utc_now
from tests.social_test_support import SocialDbTestCase


class SocialMessageTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.befriend(self.a, self.c)
        self.key = dm_key(self.a, self.b)

    def send(self, sender, key, body="你好", **extra):
        return self.run_service(messages.send_message, self.user(sender), key, {"body": body, **extra})

    def backdate(self, message_id: int, minutes: int = 5) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE social_messages SET created_at = ? WHERE id = ?",
                         ((utc_now() - timedelta(minutes=minutes)).isoformat(), message_id))
            conn.commit()

    def test_dm_requires_friendship_and_no_block(self):
        message, events = self.send(self.a, self.key, "**hello**", body_format="md")
        self.assertTrue(message["mine"])
        self.assertEqual(message["body_format"], "md")
        for key in ("id", "conversation_key", "sender", "body", "body_format", "message_type", "reply_to",
                    "mentions", "media", "created_at", "recalled", "mine"):
            self.assertIn(key, message)
        pushed = {targets[0]: event["message"]["mine"] for targets, event in events}
        self.assertEqual(pushed, {self.a: True, self.b: False})
        with self.assertRaises(SocialError):  # b and c are not friends
            self.send(self.b, dm_key(self.b, self.c))
        with self.assertRaises(SocialError):  # outsiders cannot even read
            self.run_service(conversations.list_messages, self.user(self.c), self.key)
        self.run_service(friends.block_user, self.user(self.b), self.a)
        with self.assertRaises(SocialError):
            self.send(self.a, self.key)
        self.run_service(friends.unblock_user, self.user(self.b), self.a)
        with self.assertRaises(SocialError) as ctx:  # the block also ended the friendship
            self.send(self.a, self.key)
        self.assertIn("不是好友", ctx.exception.detail)
        history, _ = self.run_service(conversations.list_messages, self.user(self.a), self.key)
        self.assertEqual(len(history["items"]), 2)  # welcome + hello stay readable

    def test_group_requires_current_membership(self):
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b, self.c])
        key = card["conversation_key"]
        self.send(self.b, key, "大家好")
        self.run_service(groups.leave_group, self.user(self.b), card["id"])
        with self.assertRaises(SocialError) as ctx:
            self.send(self.b, key)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_body_limits_and_empty(self):
        with self.assertRaises(SocialError):
            self.send(self.a, self.key, "")
        with self.assertRaises(SocialError):
            self.send(self.a, self.key, "字" * 4001)

    def test_idempotent_client_nonce(self):
        first, _ = self.send(self.a, self.key, "一次", client_nonce="nonce-1")
        again, events = self.send(self.a, self.key, "一次", client_nonce="nonce-1")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(events, [])
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_messages WHERE client_nonce = 'nonce-1'"), 1)

    def test_rate_limit_returns_429(self):
        for index in range(10):
            self.send(self.a, self.key, f"m{index}")
        with self.assertRaises(RateLimitExceededError):
            self.send(self.a, self.key, "too many")
        client = self.client_as(self.a)
        response = client.post(f"/api/social/conversations/{self.key}/messages", json={"body": "x"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_recall_window_and_admin_recall(self):
        message, _ = self.send(self.a, self.key)
        with self.assertRaises(SocialError):
            self.run_service(messages.recall_message, self.user(self.b), message["id"])
        _, events = self.run_service(messages.recall_message, self.user(self.a), message["id"])
        self.assertEqual(events[0][1]["type"], "message_recalled")
        history, _ = self.run_service(conversations.list_messages, self.user(self.b), self.key)
        self.assertTrue(history["items"][-1]["recalled"])
        self.assertEqual(history["items"][-1]["body"], "")
        old, _ = self.send(self.a, self.key, "旧消息")
        self.backdate(old["id"])
        with self.assertRaises(SocialError) as ctx:
            self.run_service(messages.recall_message, self.user(self.a), old["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b])
        group_message, _ = self.send(self.b, card["conversation_key"], "群消息")
        self.backdate(group_message["id"])
        result, _ = self.run_service(messages.recall_message, self.user(self.a), group_message["id"])
        self.assertTrue(result["recalled"])

    def test_nudge_once_per_pair_per_minute(self):
        message, events = self.run_service(messages.nudge, self.user(self.a), self.key)
        self.assertEqual(message["message_type"], "nudge")
        self.assertIn("拍了拍", message["body"])
        self.assertIn({"type": "nudge", "conversation_key": self.key, "from": self.a}, [e for _, e in events])
        with self.assertRaises(SocialError) as ctx:
            self.run_service(messages.nudge, self.user(self.a), self.key)
        self.assertEqual(ctx.exception.status_code, 429)
        self.run_service(messages.nudge, self.user(self.b), self.key)  # the other direction is its own pair

    def test_mentions_notify_and_all_is_privileged(self):
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b, self.c])
        key = card["conversation_key"]
        with self.assertRaises(SocialError):
            self.send(self.b, key, "@全体", mentions=["all"])
        self.run_service(friends.block_user, self.user(self.c), self.b)
        message, _ = self.send(self.b, key, "@甲 @丙 看这里", mentions=[self.a, self.c, self.e])
        self.assertEqual(message["mentions"], [self.a, self.c])
        notified = self.count("SELECT COUNT(*) FROM message_center_notifications WHERE category = 'social' "
                              "AND ref_id LIKE 'mention:%'")
        self.assertEqual(notified, 1)  # c blocked b, so only a is pinged
        self.send(self.a, key, "全体注意", mentions=["all"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM message_center_notifications "
                                    "WHERE ref_id LIKE 'mention:%'"), 3)

    def test_read_cursor_unread_counts_and_reply(self):
        first, _ = self.send(self.a, self.key, "一")
        second, _ = self.send(self.a, self.key, "二", reply_to_id=first["id"])
        self.assertEqual(second["reply_to"]["id"], first["id"])
        self.assertEqual(second["reply_to"]["preview"], "一")
        items, _ = self.run_service(conversations.list_conversations, self.user(self.b))
        dm = next(item for item in items if item["conversation_key"] == self.key)
        self.assertEqual(dm["unread"], 2)  # the system welcome line does not count
        self.assertEqual(dm["last_message"]["preview"], "二")
        _, events = self.run_service(messages.mark_read, self.user(self.b), self.key, second["id"])
        self.assertEqual(events[0], ([self.a], {"type": "read", "conversation_key": self.key, "reader": self.b,
                                                 "last_message_id": second["id"]}))
        items, _ = self.run_service(conversations.list_conversations, self.user(self.b))
        self.assertEqual(next(item for item in items if item["conversation_key"] == self.key)["unread"], 0)
        history, _ = self.run_service(conversations.list_messages, self.user(self.a), self.key)
        self.assertEqual(history["peer_read_message_id"], second["id"])
        card, _ = self.run_service(groups.create_group, self.user(self.a), "群", [self.b])
        self.send(self.a, card["conversation_key"], "群里好")
        listing, _ = self.run_service(groups.list_groups, self.user(self.b))
        self.assertEqual(listing[0]["unread"], 1)  # the message only: the creation line never counts
        with self.connect() as conn:
            self.assertEqual(conversations.unread_total(conn, self.b), 1)

    def test_pagination(self):
        with patch.object(messages, "RATE_WINDOWS", ((10, 1000), (60, 1000))):
            for index in range(5):
                self.send(self.a, self.key, f"m{index}")
        page, _ = self.run_service(conversations.list_messages, self.user(self.a), self.key, limit=2)
        self.assertTrue(page["has_more"])
        self.assertEqual([item["body"] for item in page["items"]], ["m3", "m4"])
        older, _ = self.run_service(conversations.list_messages, self.user(self.a), self.key,
                                    before_id=page["items"][0]["id"], limit=10)
        self.assertFalse(older["has_more"])
        self.assertEqual(len(older["items"]), 4)

    def test_http_routes(self):
        client = self.client_as(self.a)
        sent = client.post(f"/api/social/conversations/{self.key}/messages",
                           json={"body": "hi", "body_format": "text", "client_nonce": "n-http"})
        self.assertEqual(sent.status_code, 200, sent.text)
        listing = client.get("/api/social/conversations").json()["items"]
        dm = next(item for item in listing if item["type"] == "dm")
        for key in ("conversation_key", "type", "title", "avatar_url", "last_message", "unread", "online",
                    "peer_identity"):
            self.assertIn(key, dm)
        self.assertEqual(client.get(f"/api/social/conversations/{self.key}/messages").json()["items"][-1]["body"], "hi")
        self.assertEqual(client.post(f"/api/social/conversations/{self.key}/read",
                                     json={"last_message_id": sent.json()["id"]}).status_code, 200)
        self.assertEqual(client.post(f"/api/social/conversations/{self.key}/nudge").status_code, 200)
        self.assertEqual(client.post(f"/api/social/messages/{sent.json()['id']}/recall").json()["recalled"], True)
        self.assertEqual(client.get("/api/social/conversations/dm:bad/messages").status_code, 404)


if __name__ == "__main__":
    unittest.main()
