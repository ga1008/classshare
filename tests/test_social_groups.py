"""群组 (docs/social-friends-groups-2026-10-27.md §4, §7)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_conversation_service as conversations
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
        updated, _ = self.run_service(groups.update_me, self.user(self.b), card["id"], nickname="小乙", muted=True, alias="我的学习群")
        self.assertEqual(updated, {"group_id": card["id"], "nickname": "小乙", "muted": True,
                                   "alias": "我的学习群", "display_name": "我的学习群"})
        self.run_service(groups.leave_group, self.user(self.b), card["id"])
        result, _ = self.run_service(groups.add_members, self.user(self.a), card["id"], [self.b])
        self.assertEqual(result["added"], [self.b])
        detail, _ = self.run_service(groups.group_detail, self.user(self.b), card["id"])
        self.assertEqual(detail["me"], {"nickname": "", "muted": False, "alias": ""})

    def test_alias_is_private_in_every_projection_and_push(self):
        card, _ = self.create([self.b])
        gid = card["id"]
        original_lines = self.system_lines(gid)
        own, events = self.run_service(groups.update_me, self.user(self.b), gid, alias="私人代称")
        self.assertEqual(own["display_name"], "私人代称")
        self.assertEqual(len(events), 1)
        self.assertEqual(set(events[0][0]), {self.b})
        self.assertEqual(events[0][1], {"type": "group_changed", "group_id": gid, "reason": "self_settings"})
        for identity, expected in ((self.a, card["name"]), (self.b, "私人代称")):
            detail, _ = self.run_service(groups.group_detail, self.user(identity), gid)
            listing, _ = self.run_service(groups.list_groups, self.user(identity))
            chats, _ = self.run_service(conversations.list_conversations, self.user(identity))
            self.assertEqual(detail["display_name"], expected)
            self.assertEqual(listing[0]["display_name"], expected)
            self.assertEqual(chats[0]["title"], expected)
            self.assertEqual(chats[0]["group_name"], card["name"])
            self.assertEqual(detail["name"], card["name"])
            self.assertTrue(all("alias" not in member for member in detail["members"]))
            if identity == self.a:
                self.assertNotIn("私人代称", str((detail, listing, chats)))
        self.assertEqual(self.system_lines(gid), original_lines)
        cleared, _ = self.run_service(groups.update_me, self.user(self.b), gid, alias="  ")
        self.assertEqual(cleared["alias"], "")
        self.assertEqual(cleared["display_name"], card["name"])

    def test_nickname_remains_public_but_alias_independent(self):
        card, _ = self.create([self.b])
        gid = card["id"]
        self.run_service(groups.update_me, self.user(self.a), gid, alias="甲的代称")
        _, events = self.run_service(groups.update_me, self.user(self.b), gid, alias="乙的代称", nickname="公开昵称")
        public = [event for targets, event in events if set(targets) == {self.a, self.b}]
        self.assertEqual(public, [{"type": "group_changed", "group_id": gid, "reason": "nickname"}])
        self.assertNotIn("代称", str(events))
        renamed, _ = self.run_service(groups.update_group, self.user(self.a), gid, name="正式新群名")
        self.assertEqual(renamed["display_name"], "甲的代称")
        self.assertEqual(next(m for m in renamed["members"] if m["identity"] == self.b)["nickname"], "公开昵称")
        b, _ = self.run_service(groups.group_detail, self.user(self.b), gid)
        self.assertEqual(b["display_name"], "乙的代称")
        self.assertEqual(b["name"], "正式新群名")

    def test_settings_validate_and_refuse_nonmembers_without_writes(self):
        card, _ = self.create([self.b])
        for alias in (["bad"], {"bad": 1}, "长" * 31):
            with self.assertRaises(SocialError):
                self.run_service(groups.update_me, self.user(self.b), card["id"], nickname="不能保存", alias=alias)
        with self.assertRaises(SocialError) as raised:
            self.run_service(groups.update_me, self.user(self.c), card["id"], alias="非成员")
        self.assertEqual(raised.exception.status_code, 403)
        detail, _ = self.run_service(groups.group_detail, self.user(self.b), card["id"])
        self.assertEqual(detail["me"]["nickname"], "")

    def test_group_info_conflict_and_noop_do_not_emit_messages(self):
        card, _ = self.create([self.b])
        gid = card["id"]
        initial, _ = self.run_service(groups.group_detail, self.user(self.a), gid)
        count = len(self.system_lines(gid))
        unchanged, events = self.run_service(groups.update_group, self.user(self.a), gid, name=initial["name"],
                                            announcement="", expected_updated_at=initial["updated_at"])
        self.assertEqual(unchanged["updated_at"], initial["updated_at"])
        self.assertEqual(events, [])
        self.assertEqual(len(self.system_lines(gid)), count)
        self.run_service(groups.update_group, self.user(self.a), gid, announcement="新的公告",
                         expected_updated_at=initial["updated_at"])
        with self.assertRaises(SocialError) as raised:
            self.run_service(groups.update_group, self.user(self.a), gid, name="旧窗口修改",
                             expected_updated_at=initial["updated_at"])
        self.assertEqual(raised.exception.status_code, 409)
        fresh, _ = self.run_service(groups.group_detail, self.user(self.a), gid)
        self.assertEqual(fresh["name"], initial["name"])
        self.assertEqual(fresh["announcement"], "新的公告")
        self.assertEqual(len(self.system_lines(gid)), count + 1)

    def test_member_can_invite_their_friends_and_partial_retry_is_idempotent(self):
        card, _ = self.create([self.b])
        self.befriend(self.b, self.d)
        result, _ = self.run_service(groups.add_members, self.user(self.b), card["id"], [self.d, self.e])
        self.assertEqual(result["added"], [self.d])
        self.assertEqual(len(result["skipped"]), 1)
        before = self.system_lines(card["id"])
        result, events = self.run_service(groups.add_members, self.user(self.b), card["id"], [self.d, self.e])
        self.assertEqual(result["added"], [])
        self.assertEqual(events, [])
        self.assertEqual(self.system_lines(card["id"]), before)

    def test_http_routes(self):
        client = self.client_as(self.a)
        created = client.post("/api/social/groups", json={"name": "HTTP 群", "member_identities": [self.b]})
        self.assertEqual(created.status_code, 200, created.text)
        gid = created.json()["id"]
        self.assertEqual(client.get("/api/social/groups").json()["items"][0]["id"], gid)
        baseline = client.get(f"/api/social/groups/{gid}").json()
        self.assertEqual(client.patch(f"/api/social/groups/{gid}", json={"name": "新名字"}).json()["name"], "新名字")
        self.assertEqual(client.patch(f"/api/social/groups/{gid}", json={"name": "冲突草稿",
            "expected_updated_at": baseline["updated_at"]}).status_code, 409)
        mine = client.patch(f"/api/social/groups/{gid}/me", json={"alias": "仅我可见"}).json()
        self.assertEqual(mine["alias"], "仅我可见")
        self.assertEqual(mine["display_name"], "仅我可见")
        self.assertEqual(client.get(f"/api/social/groups/{gid}").json()["name"], "新名字")
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
