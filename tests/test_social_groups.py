"""群组 (docs/social-friends-groups-2026-10-27.md §4, §7)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_group_service as groups
from classroom_app.services.social_common import SocialError, group_key
from tests.social_test_support import SocialDbTestCase


class SocialGroupTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        for other in (self.b, self.c, self.d):
            self.befriend(self.a, other)

    def create(self, members=None, name="学习小组"):
        return self.run_service(groups.create_group, self.user(self.a), name, members or [self.b, self.c])

    def system_lines(self, group_id: int) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT body FROM social_messages WHERE conversation_key = ? "
                                "AND message_type = 'system' ORDER BY id", (group_key(group_id),)).fetchall()
        return [row[0] for row in rows]

    def test_create_only_with_friends_and_reports_skips(self):
        with self.assertRaises(SocialError):
            self.run_service(groups.create_group, self.user(self.a), "空群", [])
        with self.assertRaises(SocialError):
            self.run_service(groups.create_group, self.user(self.a), "陌生群", [self.e])
        card, events = self.create([self.b, self.e])
        self.assertEqual(card["member_count"], 2)
        self.assertEqual(card["my_role"], "owner")
        self.assertEqual(card["skipped"], [{"identity": self.e, "reason": "只能邀请好友进群"}])
        for key in ("id", "name", "avatar_url", "member_count", "online_count", "my_role", "conversation_key",
                    "unread", "last_message"):
            self.assertIn(key, card)
        self.assertIn("创建了群聊", self.system_lines(card["id"])[0])
        self.assertIn("group_changed", [event["type"] for _, event in events])

    def test_invite_skips_people_who_blocked_the_inviter(self):
        card, _ = self.create([self.b])
        self.run_service(friends.block_user, self.user(self.c), self.a)
        result, _ = self.run_service(groups.add_members, self.user(self.b), card["id"], [self.c])
        self.assertEqual(result["added"], [])
        result, _ = self.run_service(groups.add_members, self.user(self.a), card["id"], [self.c, self.d])
        self.assertEqual(result["added"], [self.d])
        self.assertEqual(result["skipped"][0]["identity"], self.c)

    def test_roles_kick_transfer_leave_dissolve(self):
        card, _ = self.create([self.b, self.c, self.d])
        gid = card["id"]
        with self.assertRaises(SocialError):
            self.run_service(groups.remove_member, self.user(self.b), gid, self.c)
        self.run_service(groups.set_admin, self.user(self.a), gid, self.b, True)
        self.run_service(groups.remove_member, self.user(self.b), gid, self.c)
        with self.assertRaises(SocialError):  # admins cannot kick the owner
            self.run_service(groups.remove_member, self.user(self.b), gid, self.a)
        self.run_service(groups.update_group, self.user(self.b), gid, announcement="周五交作业")
        detail, _ = self.run_service(groups.group_detail, self.user(self.a), gid)
        self.assertEqual(detail["announcement"], "周五交作业")
        self.assertEqual({item["identity"] for item in detail["members"]}, {self.a, self.b, self.d})
        with self.assertRaises(SocialError):
            self.run_service(groups.leave_group, self.user(self.a), gid)
        self.run_service(groups.transfer_owner, self.user(self.a), gid, self.d)
        self.run_service(groups.leave_group, self.user(self.a), gid)
        with self.assertRaises(SocialError):
            self.run_service(groups.dissolve_group, self.user(self.b), gid)
        self.run_service(groups.dissolve_group, self.user(self.d), gid)
        lines = self.system_lines(gid)
        for fragment in ("设为管理员", "被移出了群聊", "更新了群公告", "转让给了", "退出了群聊", "解散了群聊"):
            self.assertTrue(any(fragment in line for line in lines), fragment)
        listing, _ = self.run_service(groups.list_groups, self.user(self.b))
        self.assertEqual(listing, [])

    def test_member_caps(self):
        with patch.object(groups, "GROUP_MAX_MEMBERS", 2):
            card, _ = self.create([self.b, self.c])
        self.assertEqual(card["member_count"], 2)
        self.assertIn("上限", card["skipped"][0]["reason"])
        with patch.object(groups, "MAX_CREATED_GROUPS", 1):
            with self.assertRaises(SocialError) as ctx:
                self.create([self.b])
        self.assertEqual(ctx.exception.status_code, 409)

    def test_me_settings_and_rejoin(self):
        card, _ = self.create([self.b])
        updated, _ = self.run_service(groups.update_me, self.user(self.b), card["id"], nickname="小乙", muted=True)
        self.assertEqual(updated, {"group_id": card["id"], "nickname": "小乙", "muted": True})
        self.run_service(groups.leave_group, self.user(self.b), card["id"])
        result, _ = self.run_service(groups.add_members, self.user(self.a), card["id"], [self.b])
        self.assertEqual(result["added"], [self.b])
        detail, _ = self.run_service(groups.group_detail, self.user(self.b), card["id"])
        self.assertEqual(detail["me"], {"nickname": "", "muted": False})

    def test_http_routes(self):
        client = self.client_as(self.a)
        created = client.post("/api/social/groups", json={"name": "HTTP 群", "member_identities": [self.b]})
        self.assertEqual(created.status_code, 200, created.text)
        gid = created.json()["id"]
        self.assertEqual(client.get("/api/social/groups").json()["items"][0]["id"], gid)
        self.assertEqual(client.patch(f"/api/social/groups/{gid}", json={"name": "新名字"}).json()["name"], "新名字")
        self.assertEqual(client.post(f"/api/social/groups/{gid}/members",
                                     json={"identities": [self.c]}).json()["added"], [self.c])
        self.assertEqual(client.post(f"/api/social/groups/{gid}/admins",
                                     json={"identity": self.c, "admin": True}).json()["role"], "admin")
        self.assertEqual(client.delete(f"/api/social/groups/{gid}/members/{self.c}").status_code, 200)
        self.assertEqual(client.patch(f"/api/social/groups/{gid}/me", json={"muted": True}).json()["muted"], True)
        self.assertEqual(client.get(f"/api/social/groups/{gid}/files").json(), {"items": [], "has_more": False})
        self.assertEqual(client.post(f"/api/social/groups/{gid}/transfer", json={"identity": self.b}).status_code, 200)
        self.assertEqual(client.post(f"/api/social/groups/{gid}/leave").status_code, 200)
        self.assertEqual(client.get(f"/api/social/groups/{gid}").status_code, 403)
        self.assertEqual(client.post(f"/api/social/groups/{gid}/dissolve").status_code, 403)


if __name__ == "__main__":
    unittest.main()
