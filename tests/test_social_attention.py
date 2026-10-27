"""Synthetic users, isolated DB and mocked SMTP: inbox/quiet/@ delivery contract."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

from classroom_app.services import social_conversation_service as conversations
from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_group_service as groups
from classroom_app.services import social_message_service as messages
from classroom_app.services import social_notification_service as attention
from classroom_app.services import email_notification_service as emails
from classroom_app.services.social_common import SocialError, dm_key
from tests.social_test_support import SocialDbTestCase


class SocialAttentionTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.befriend(self.a, self.c)
        self.key = dm_key(self.a, self.b)
        with self.connect() as conn:
            conn.execute("DELETE FROM user_notification_preferences WHERE event_key LIKE 'social-popup:%'")
            conn.execute("DELETE FROM email_outbox")
            conn.execute("DELETE FROM teacher_email_configs")
            conn.execute("UPDATE students SET email = ? WHERE id = ?", ("synthetic@example.test", self.user(self.b)["id"]))
            conn.commit()

    def send(self, sender=None, key=None, body="聊天", **extra):
        return self.run_service(messages.send_message, self.user(sender or self.a), key or self.key,
                                {"body": body, **extra})

    def inbox(self, who=None):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM message_center_notifications "
                "WHERE recipient_identity = ? AND ref_type = 'social_message' ORDER BY id", (who or self.b,))]

    def test_ordinary_messages_aggregate_and_read_cursor_does_not_clear_newer(self):
        first, _ = self.send(body="第一条")
        last, _ = self.send(body="第二条")
        cards = self.inbox()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["aggregate_count"], 2)
        self.assertEqual(cards[0]["body_preview"], "第二条")
        self.assertIn("2", cards[0]["title"])
        self.assertEqual(cards[0]["email_status"], "not_required")
        self.run_service(messages.mark_read, self.user(self.b), self.key, first["id"])
        self.assertIsNone(self.inbox()[0]["read_at"])
        _, events = self.run_service(messages.mark_read, self.user(self.b), self.key, last["id"])
        self.assertTrue(self.inbox()[0]["read_at"])
        self.assertTrue(any(targets == [self.b] and event["type"] == "read" for targets, event in events))
        self.send(body="新一轮")
        self.assertEqual(len(self.inbox()), 2)

    def test_muting_dm_and_group_preserves_inbox_and_badge(self):
        _, events = self.run_service(attention.set_preferences, self.user(self.b), self.key, True)
        self.assertEqual(events[0][1]["type"], "conversation_preferences")
        _, events = self.send()
        self.assertTrue(next(event["message"]["muted"] for targets, event in events if targets == [self.b]))
        self.assertEqual(len(self.inbox()), 1)
        items, _ = self.run_service(conversations.list_conversations, self.user(self.b))
        self.assertTrue(items[0]["muted"])
        self.assertEqual(items[0]["unread"], 1)
        group, _ = self.run_service(groups.create_group, self.user(self.a), "静音群", [self.b])
        self.run_service(groups.update_me, self.user(self.b), group["id"], muted=True)
        self.send(key=group["conversation_key"])
        with self.connect() as conn:
            self.assertEqual(conversations.unread_total(conn, self.b), 2)

    def test_recalling_last_ordinary_message_keeps_earlier_unread_card(self):
        first, _ = self.send(body="仍需查看")
        last, _ = self.send(body="撤回这条")
        self.run_service(messages.recall_message, self.user(self.a), last["id"])
        card = self.inbox()[0]
        self.assertIsNone(card["read_at"])
        self.assertEqual(card["aggregate_count"], 1)
        self.assertEqual(card["body_preview"], "仍需查看")
        self.assertEqual(json.loads(card["metadata_json"])["message_id"], first["id"])

    def test_preferences_require_conversation_access_and_boolean(self):
        with self.assertRaises(SocialError):
            self.run_service(attention.set_preferences, self.user(self.c), self.key, True)
        with self.assertRaises(SocialError):
            self.run_service(attention.set_preferences, self.user(self.b), self.key, "false")
        result, _ = self.run_service(attention.preferences, self.user(self.b), self.key)
        self.assertFalse(result["muted"])

    def test_dm_mention_only_targets_peer_and_counts_once_per_nonce(self):
        sent, _ = self.send(mentions=[self.b, self.b, self.c, "all"], client_nonce="mention-once")
        self.assertEqual(sent["mentions"], [self.b])
        self.assertEqual(sent["mention_quota"]["remaining"], 2)
        repeated, events = self.send(mentions=[self.b], client_nonce="mention-once")
        self.assertEqual(repeated["id"], sent["id"])
        self.assertFalse(events)
        quota, _ = self.run_service(attention.mention_quota, self.user(self.a))
        self.assertEqual(quota["used"], 1)
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(json.loads(self.inbox()[0]["metadata_json"])["event_type"], "social_mention")

    def test_daily_limit_is_shared_across_conversations_and_recall_does_not_refund(self):
        sent, _ = self.send(mentions=[self.b])
        self.run_service(messages.recall_message, self.user(self.a), sent["id"])
        group, _ = self.run_service(groups.create_group, self.user(self.a), "同日", [self.b, self.c])
        self.send(key=group["conversation_key"], mentions=["all"])
        self.send(mentions=[self.b])
        with self.assertRaises(SocialError) as raised:
            self.send(mentions=[self.b])
        self.assertEqual(raised.exception.status_code, 429)
        self.send(body="仍可普通聊天")
        quota, _ = self.run_service(attention.mention_quota, self.user(self.a))
        self.assertEqual(quota["used"], 3)

    def test_teacher_twenty_admin_unlimited_uses_current_db_role(self):
        with self.connect() as conn:
            teacher = self.user(self.teacher)
            self.assertIsNone(attention.mention_quota(conn, teacher)["limit"])
            conn.execute("UPDATE teachers SET is_super_admin = 0 WHERE id = ?", (teacher["id"],))
            self.assertEqual(attention.mention_quota(conn, teacher)["limit"], 20)
            # Rollback keeps the shared synthetic fixture admin unchanged.
            conn.rollback()

    def test_beijing_midnight_resets_counter(self):
        with patch.object(attention, "utc_now", return_value=datetime(2026, 10, 27, 15, 59, tzinfo=timezone.utc)):
            self.send(mentions=[self.b])
        with patch.object(attention, "utc_now", return_value=datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)):
            quota, _ = self.run_service(attention.mention_quota, self.user(self.a))
        self.assertEqual(quota["date"], "2026-10-28")
        self.assertEqual(quota["remaining"], 3)

    def test_failed_send_rolls_back_quota_and_inbox(self):
        with self.assertRaises(SocialError):
            self.send(mentions=[self.b], reply_to_id=999999)
        quota, _ = self.run_service(attention.mention_quota, self.user(self.a))
        self.assertEqual(quota["used"], 0)
        self.assertFalse(self.inbox())

    def test_nonce_cannot_return_another_conversations_message(self):
        self.send(client_nonce="cross-key")
        with self.assertRaises(SocialError):
            self.send(key=dm_key(self.a, self.c), client_nonce="cross-key")

    def test_email_context_escapes_html_and_excludes_pre_join_messages(self):
        group, _ = self.run_service(groups.create_group, self.user(self.a), "课程", [self.b])
        self.send(key=group["conversation_key"], body="加入前秘密")
        self.run_service(groups.add_members, self.user(self.a), group["id"], [self.c])
        self.send(key=group["conversation_key"], body="<script>alert(1)</script> & 对话", mentions=[self.c])
        payload = self.inbox(self.c)[0]
        text, markup = emails._build_email_content(payload, "同学", notification_id=payload["id"])
        self.assertNotIn("加入前秘密", text)
        self.assertIn("<script>", text)
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("打开聊天", markup)
        self.assertTrue(emails.notification_email_required("social", payload=payload))

    def test_email_revalidation_cancels_recalled_blocked_or_left_targets(self):
        sent, _ = self.send(mentions=[self.b])
        payload = self.inbox()[0]
        with self.connect() as conn:
            self.assertIsNotNone(attention.refresh_email_payload(conn, payload))
        self.run_service(messages.recall_message, self.user(self.a), sent["id"])
        with self.connect() as conn:
            self.assertIsNone(attention.refresh_email_payload(conn, payload))
        self.send(mentions=[self.b])
        payload = self.inbox()[-1]
        self.run_service(friends.block_user, self.user(self.b), self.a)
        with self.connect() as conn:
            self.assertIsNone(attention.refresh_email_payload(conn, payload))

    def test_platform_sender_is_required_and_ordinary_chat_never_emails(self):
        with patch.object(emails, "platform_sender_teacher_id", return_value=0):
            self.send(mentions=[self.b])
            self.assertEqual(self.inbox()[0]["email_status"], "not_configured")
        self.send(body="普通消息")
        ordinary = self.inbox()[-1]
        self.assertFalse(emails.notification_email_required("social", "important", payload=ordinary))

    def test_http_preferences_quota_and_message_contract(self):
        client = self.client_as(self.b)
        self.assertEqual(client.patch(f"/api/social/conversations/{self.key}/preferences",
                                     json={"muted": True}).status_code, 200)
        self.assertTrue(client.get(f"/api/social/conversations/{self.key}/preferences").json()["muted"])
        self.assertEqual(client.get("/api/social/mention-quota").json()["limit"], 3)
        result = client.get(f"/api/social/conversations/{self.key}/messages").json()
        self.assertTrue(result["muted"])
        self.assertIn("mention_quota", result)

    def test_real_outbox_uses_platform_sender_and_worker_refreshes_context(self):
        teacher_id = self.user(self.teacher)["id"]
        with self.connect() as conn:
            emails.create_teacher_email_config(conn, teacher_id, {
                "label": "Synthetic server", "smtp_host": "smtp.invalid", "smtp_port": 465,
                "from_email": "platform@example.test", "enabled": True,
            })
            conn.commit()
        old, _ = self.send(body="将撤回的上下文")
        with patch.object(emails, "platform_sender_teacher_id", return_value=teacher_id):
            self.send(body="@乙 请查看", mentions=[self.b], client_nonce="mail-once")
            self.send(body="@乙 请查看", mentions=[self.b], client_nonce="mail-once")
        with self.connect() as conn:
            jobs = [dict(r) for r in conn.execute("SELECT * FROM email_outbox")]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["teacher_id"], teacher_id)
        self.assertIn("将撤回的上下文", jobs[0]["body_text"])
        self.run_service(messages.recall_message, self.user(self.a), old["id"])
        with patch.object(emails, "_send_outbox_message") as smtp:
            self.assertEqual(emails.process_email_job(jobs[0]), "sent")
            smtp.assert_called_once()
            self.assertNotIn("将撤回的上下文", smtp.call_args.args[1]["body_text"])
        self.assertEqual(self.inbox()[-1]["email_status"], "sent")

    def test_worker_skips_mention_after_leaving_group(self):
        teacher_id = self.user(self.teacher)["id"]
        with self.connect() as conn:
            emails.create_teacher_email_config(conn, teacher_id, {
                "smtp_host": "smtp.invalid", "from_email": "platform@example.test", "enabled": True,
            })
            conn.commit()
        group, _ = self.run_service(groups.create_group, self.user(self.a), "临时群", [self.b])
        with patch.object(emails, "platform_sender_teacher_id", return_value=teacher_id):
            self.send(key=group["conversation_key"], mentions=[self.b])
        with self.connect() as conn:
            job = dict(conn.execute("SELECT * FROM email_outbox LIMIT 1").fetchone())
        self.run_service(groups.leave_group, self.user(self.b), group["id"])
        with patch.object(emails, "_send_outbox_message") as smtp:
            self.assertEqual(emails.process_email_job(job), "skipped")
            smtp.assert_not_called()
