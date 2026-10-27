"""Synthetic transaction interleavings at the point where sending waits for a lock."""
from __future__ import annotations

from unittest.mock import patch

from classroom_app.services import social_group_service as groups
from classroom_app.services import social_message_service as messages
from classroom_app.services import social_notification_service as attention
from classroom_app.services.social_common import SocialError
from tests.social_test_support import SocialDbTestCase


class SocialAuthorizationRaceTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.group, _ = self.run_service(groups.create_group, self.user(self.a), "合成权限交错群", [self.b])
        self.key = self.group["conversation_key"]

    def interleave(self, transition, action):
        """Apply a completed authority change between the early snapshot and lock acquisition."""
        original = messages.serialize_on
        state = {"changed": False}
        observed = []
        def acquire(conn, namespace, values):
            observed.append(namespace)
            if not state["changed"]:
                state["changed"] = True
                transition(conn)
            return original(conn, namespace, values)
        with self.connect() as conn, patch.object(messages, "serialize_on", acquire):
            result = action(conn)
            conn.commit()
        self.assertTrue(state["changed"])
        return result, observed

    def assert_send_denied(self, transition, *, nudge=False):
        def action(conn):
            with self.assertRaises(SocialError):
                if nudge:
                    messages.nudge(conn, self.user(self.b), self.key, self.a)
                else:
                    messages.send_message(conn, self.user(self.b), self.key,
                                          {"body": "权限撤销后的草稿", "mentions": [self.a]})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages WHERE sender_identity = ? "
                                          "AND message_type <> 'system'", (self.b,)).fetchone()[0], 0)
            self.assertEqual(attention.mention_quota(conn, self.user(self.b))["used"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM message_center_notifications "
                                          "WHERE actor_identity = ? AND ref_type = 'social_message'", (self.b,)).fetchone()[0], 0)
        self.interleave(transition, action)

    def test_removed_member_cannot_send_or_email_after_wait(self):
        self.assert_send_denied(lambda conn: groups.remove_member(conn, self.user(self.a), self.group["id"], self.b))

    def test_departed_member_cannot_send_after_wait(self):
        self.assert_send_denied(lambda conn: groups.leave_group(conn, self.user(self.b), self.group["id"]))

    def test_dissolved_group_cannot_receive_after_wait(self):
        self.assert_send_denied(lambda conn: groups.dissolve_group(conn, self.user(self.a), self.group["id"]))

    def test_removed_member_cannot_nudge_after_wait(self):
        self.assert_send_denied(lambda conn: groups.remove_member(conn, self.user(self.a), self.group["id"], self.b),
                                nudge=True)

    def test_demoted_admin_cannot_use_stale_mention_all_permission(self):
        self.run_service(groups.set_admin, self.user(self.a), self.group["id"], self.b, True)
        def action(conn):
            with self.assertRaises(SocialError) as raised:
                messages.send_message(conn, self.user(self.b), self.key, {"body": "全体提醒", "mentions": ["all"]})
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(attention.mention_quota(conn, self.user(self.b))["used"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_messages WHERE sender_identity = ? "
                                          "AND message_type <> 'system'", (self.b,)).fetchone()[0], 0)
        self.interleave(lambda conn: groups.set_admin(conn, self.user(self.a), self.group["id"], self.b, False), action)

    def test_former_owner_cannot_use_stale_mention_all_permission(self):
        def action(conn):
            with self.assertRaises(SocialError) as raised:
                messages.send_message(conn, self.user(self.a), self.key, {"body": "全体提醒", "mentions": ["all"]})
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(attention.mention_quota(conn, self.user(self.a))["used"], 0)
        self.interleave(lambda conn: groups.transfer_owner(conn, self.user(self.a), self.group["id"], self.b), action)

    def test_recall_rechecks_manager_authority_after_wait(self):
        self.run_service(groups.set_admin, self.user(self.a), self.group["id"], self.b, True)
        sent, _ = self.run_service(messages.send_message, self.user(self.a), self.key, {"body": "其他人的消息"})
        def action(conn):
            with self.assertRaises(SocialError):
                messages.recall_message(conn, self.user(self.b), sent["id"])
            self.assertIsNone(conn.execute("SELECT recalled_at FROM social_messages WHERE id = ?", (sent["id"],)).fetchone()[0])
        self.interleave(lambda conn: groups.set_admin(conn, self.user(self.a), self.group["id"], self.b, False), action)

    def test_sends_keep_sender_before_conversation_lock_order(self):
        original = messages.serialize_on
        for action in (lambda conn: messages.send_message(conn, self.user(self.b), self.key,
                                                           {"body": "消息", "mentions": [self.a]}),
                       lambda conn: messages.nudge(conn, self.user(self.b), self.key, self.a)):
            order = []
            def acquire(conn, namespace, values):
                order.append(namespace)
                return original(conn, namespace, values)
            with self.connect() as conn, patch.object(messages, "serialize_on", acquire):
                action(conn)
                conn.commit()
            self.assertLess(order.index("social-send"), order.index("social-attention"))

    def test_group_settings_recheck_authority_after_lock_wait(self):
        self.run_service(groups.set_admin, self.user(self.a), self.group["id"], self.b, True)
        original = groups.serialize_on
        changed = False
        def acquire(conn, namespace, values):
            nonlocal changed
            if not changed:
                changed = True
                conn.execute("UPDATE social_group_members SET role = 'member' WHERE group_id = ? AND member_identity = ?",
                             (self.group["id"], self.b))
            return original(conn, namespace, values)
        with self.connect() as conn, patch.object(groups, "serialize_on", acquire):
            with self.assertRaises(SocialError) as raised:
                groups.update_group(conn, self.user(self.b), self.group["id"], name="不能保存")
            self.assertEqual(raised.exception.status_code, 403)
            self.assertNotEqual(conn.execute("SELECT name FROM social_groups WHERE id = ?", (self.group["id"],)).fetchone()[0], "不能保存")

    def test_invite_rechecks_membership_after_group_lock(self):
        self.befriend(self.b, self.c)
        original = groups.serialize_on
        locks = []
        def acquire(conn, namespace, values):
            locks.append(namespace)
            if namespace == "social-attention":
                conn.execute("UPDATE social_group_members SET left_at = '2026-10-28' "
                             "WHERE group_id = ? AND member_identity = ?", (self.group["id"], self.b))
            return original(conn, namespace, values)
        with self.connect() as conn, patch.object(groups, "serialize_on", acquire):
            with self.assertRaises(SocialError) as raised:
                groups.add_members(conn, self.user(self.b), self.group["id"], [self.c])
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM social_group_members WHERE group_id = ?",
                                          (self.group["id"],)).fetchone()[0], 2)
        self.assertEqual(locks, ["social-group-membership", "social-attention"])

    def test_personal_settings_recheck_active_membership(self):
        original = groups.serialize_on
        def acquire(conn, namespace, values):
            conn.execute("UPDATE social_groups SET dissolved_at = '2026-10-28' WHERE id = ?", (self.group["id"],))
            return original(conn, namespace, values)
        with self.connect() as conn, patch.object(groups, "serialize_on", acquire):
            with self.assertRaises(SocialError) as raised:
                groups.update_me(conn, self.user(self.b), self.group["id"], alias="不能保存", muted=True)
            self.assertEqual(raised.exception.status_code, 404)
            row = conn.execute("SELECT alias, muted FROM social_group_members WHERE group_id = ? AND member_identity = ?",
                               (self.group["id"], self.b)).fetchone()
            self.assertEqual(tuple(row), ("", 0))
