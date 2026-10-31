import asyncio
import json
import unittest
from unittest.mock import patch

from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_social_actions import (
    maybe_block,
    maybe_create_group,
    maybe_invite_group,
    maybe_leave_group,
    maybe_unblock,
)
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase, ZeroSleep


class SocialActionTest(WandererWorldTestCase):
    def _ctx(self, caller, slug="atai"):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, slug)
        return ActionContext(conn_factory=self.connect, wanderer=wanderer, ai_caller=caller, sleep=ZeroSleep())

    def _friend(self, conn, me: str, peer: str):
        conn.execute(
            "INSERT INTO social_friendships (identity_a, identity_b, status, requester_identity, created_at) "
            "VALUES (?, ?, 'accepted', ?, ?)", (me, peer, me, "2026-10-01T00:00:00"))

    @property
    def ctx_student_id(self):
        with self.connect() as conn:
            return int(self.wanderer(conn, "atai")["student_id"])

    def _second_student(self, number="S-0002", name="另一个同学"):
        with self.connect() as conn:
            conn.execute("INSERT INTO students (student_id_number, name, class_id) VALUES (?, ?, ?)",
                         (number, name, self.class_id))
            pk = int(conn.execute("SELECT id FROM students WHERE student_id_number = ?", (number,)).fetchone()[0])
            conn.commit()
        return pk

    def test_wanderer_can_block_a_friend_after_negative_history(self):
        me = f"student:{self.ctx_student_id}"
        peer = f"student:{self.student_pk}"
        with self.connect() as conn:
            self._friend(conn, me, peer)
            wanderer = self.wanderer(conn, "atai")
            conn.execute("INSERT INTO wanderer_relationships (wanderer_id, peer_identity, peer_kind, affinity, "
                         "interactions, last_delta, last_mood, last_reason, feelings_json, updated_at) "
                         "VALUES (?, ?, 'student', -70, 3, -5, '烦躁', '反复越界', '[]', '2026-10-30T00:00:00')",
                         (wanderer["id"], peer))
            conn.commit()
        caller = StubCaller(json.dumps({"action": "block", "target": peer, "reason": "我受够了"}, ensure_ascii=False))
        with patch("classroom_app.services.wanderer_social_actions._push") as push:
            push.return_value = None
            result = asyncio.run(maybe_block(self._ctx(caller)))
        self.assertEqual(result[0]["kind"], "social_block")
        with self.connect() as conn:
            private = conn.execute("SELECT 1 FROM private_message_blocks WHERE owner_identity = ? AND blocked_identity = ?",
                                   (me, peer)).fetchone()
            social = conn.execute("SELECT 1 FROM social_blocks WHERE owner_identity = ? AND blocked_identity = ?",
                                  (me, peer)).fetchone()
            memory = conn.execute("SELECT kind, importance FROM wanderer_episodic_memories WHERE wanderer_id = "
                                  "(SELECT id FROM wanderer_agents WHERE slug = 'atai')").fetchone()
        self.assertTrue(private is not None or social is not None)
        self.assertEqual((memory["kind"], memory["importance"]), ("relationship", 0.9))

    def test_wanderer_can_unblock_after_cooling_down(self):
        me = f"student:{self.ctx_student_id}"
        peer = f"student:{self.student_pk}"
        with self.connect() as conn:
            self._friend(conn, me, peer)
            wanderer = self.wanderer(conn, "atai")
            conn.execute("INSERT INTO private_message_blocks (owner_identity, owner_role, owner_user_pk, "
                         "blocked_identity, blocked_role, blocked_user_pk, blocked_display_name, created_at) "
                         "VALUES (?, 'student', ?, ?, 'student', ?, '真实同学', '2026-10-01T00:00:00')",
                         (me, self.ctx_student_id, peer, self.student_pk))
            conn.execute("INSERT INTO wanderer_relationships (wanderer_id, peer_identity, peer_kind, affinity, "
                         "interactions, last_delta, last_mood, last_reason, feelings_json, updated_at) "
                         "VALUES (?, ?, 'student', 0, 3, 0, '平静', '气消了', '[]', '2026-10-30T00:00:00')",
                         (wanderer["id"], peer))
            conn.commit()
        caller = StubCaller(json.dumps({"action": "unblock", "target": peer, "reason": "冷静下来了"}, ensure_ascii=False))
        with patch("classroom_app.services.wanderer_social_actions._push") as push:
            push.return_value = None
            result = asyncio.run(maybe_unblock(self._ctx(caller)))
        self.assertEqual(result[0]["kind"], "social_unblock")
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM private_message_blocks WHERE owner_identity = ? AND blocked_identity = ?",
                               (me, peer)).fetchone()
        self.assertIsNone(row)

    def _group(self, me, members):
        with self.connect() as conn:
            conn.execute("INSERT INTO social_groups (name, owner_identity, max_members, created_at, updated_at) "
                         "VALUES (?, ?, 200, '2026-10-30T10:00:00', '2026-10-30T10:00:00')", ("测试群", me))
            gid = int(conn.execute("SELECT id FROM social_groups ORDER BY id DESC LIMIT 1").fetchone()[0])
            for identity, role in members:
                conn.execute("INSERT INTO social_group_members (group_id, member_identity, role, joined_at) "
                             "VALUES (?, ?, ?, '2026-10-30T10:00:00')", (gid, identity, role))
            conn.commit()
        return gid

    def test_wanderer_can_invite_and_leave_group(self):
        me = f"student:{self.ctx_student_id}"
        peer = f"student:{self.student_pk}"
        third = f"student:{self._second_student('S-0003', '第三位同学')}"
        with self.connect() as conn:
            self._friend(conn, me, peer)
            self._friend(conn, me, third)
            conn.commit()
        gid = self._group(me, [(me, "owner"), (peer, "member")])
        caller = StubCaller(json.dumps({"action": "invite", "group_id": gid, "members": [third], "reason": "一起聊"},
                                        ensure_ascii=False))
        with patch("classroom_app.services.wanderer_social_actions._push") as push:
            push.return_value = None
            result = asyncio.run(maybe_invite_group(self._ctx(caller)))
        self.assertEqual(result[0]["kind"], "social_group_invite")
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM social_group_members WHERE group_id = ? AND member_identity = ? "
                               "AND left_at IS NULL", (gid, third)).fetchone()
        self.assertIsNotNone(row)

        # Make another wanderer an ordinary member in a second group.
        with self.connect() as conn:
            momo = self.wanderer(conn, "momo")
            leave_me = f"student:{momo['student_id']}"
            self._friend(conn, leave_me, peer)
            conn.commit()
        gid2 = self._group(peer, [(peer, "owner"), (leave_me, "member")])
        ctx = self._ctx(StubCaller(json.dumps({"action": "leave", "group_id": gid2, "reason": "不合适"},
                                                ensure_ascii=False)), slug="momo")
        with patch("classroom_app.services.wanderer_social_actions._push") as push:
            push.return_value = None
            result = asyncio.run(maybe_leave_group(ctx))
        self.assertEqual(result[0]["kind"], "social_group_leave")
        with self.connect() as conn:
            left = conn.execute("SELECT left_at FROM social_group_members WHERE group_id = ? AND member_identity = ?",
                                (gid2, leave_me)).fetchone()
        self.assertTrue(left["left_at"])

    def test_wanderer_can_create_a_group_from_friends(self):
        me = f"student:{self.ctx_student_id}"
        peer_a = f"student:{self.student_pk}"
        peer_b = f"student:{self._second_student()}"
        with self.connect() as conn:
            self._friend(conn, me, peer_a)
            self._friend(conn, me, peer_b)
            conn.commit()
        caller = StubCaller(json.dumps({"action": "create", "name": "晚上一起跑步",
                                        "members": [peer_a, peer_b], "reason": "聊得来"}, ensure_ascii=False))
        with patch("classroom_app.services.wanderer_social_actions._push") as push:
            push.return_value = None
            result = asyncio.run(maybe_create_group(self._ctx(caller)))
        self.assertEqual(result[0]["kind"], "social_group_create")
        with self.connect() as conn:
            group = conn.execute("SELECT name FROM social_groups WHERE name = '晚上一起跑步'").fetchone()
            members = conn.execute("SELECT COUNT(*) FROM social_group_members WHERE group_id = "
                                   "(SELECT id FROM social_groups WHERE name = '晚上一起跑步') AND left_at IS NULL"
                                   ).fetchone()[0]
        self.assertIsNotNone(group)
        self.assertEqual(int(members), 3, result)


if __name__ == "__main__":
    unittest.main()
