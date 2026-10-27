"""散修社交 (docs/social-friends-groups-2026-10-27.md §10) + 社交后端两处修复.

AI is always stubbed, sleeps are recorded instead of slept, pushes are captured;
never touches a real database, model or network.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services import blog_service, file_service, social_push_service
from classroom_app.services import social_conversation_service as conversations
from classroom_app.services import social_friend_service as friends
from classroom_app.services import social_group_service as groups
from classroom_app.services import social_media_retention_service as retention
from classroom_app.services import social_media_service as media
from classroom_app.services import social_message_service as messages
from classroom_app.services.social_common import dm_key, now_iso
from classroom_app.services.wanderer_actions_social import (
    GROUP_DAILY_CAP,
    group_reply_probability,
    handle_wanderer_social_reply,
    social_context,
    typing_seconds,
    watch_group,
)
from classroom_app.services.wanderer_identity_service import build_wanderer_user_dict, log_wanderer_action
from classroom_app.services.wanderer_settings_service import SETTING_DAILY_AI_CALL_CAP, wanderer_runtime_setting_set
from classroom_app.services.wanderer_social_friends import (
    TARGET_REQUEST_OUT,
    handle_wanderer_friend_request,
    maybe_befriend,
    sync_wanderer_presence,
)
from classroom_app.services.wanderer_social_triggers import sociability
from tests.social_test_support import SocialDbTestCase, sha
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase, ZeroSleep, turn

ALWAYS_AWAKE = {"active_hours": [[0, 24]], "weekday_weights": {}}
NEVER_AWAKE = {"active_hours": [], "weekday_weights": {}}
ALWAYS_MULTI = {"ignore_prob": 0, "emoji_only_prob": 0, "multi_part_prob": 1, "max_parts": 3}
ALWAYS_REPLY = {"ignore_prob": 0, "emoji_only_prob": 0, "multi_part_prob": 0, "max_parts": 2}
SOCIAL_TABLES = ("social_friendships", "social_groups", "social_group_members", "social_messages",
                 "social_message_media", "social_dm_reads", "user_presence", "private_message_blocks")


class FixedRandom(random.Random):
    """``random()`` (and so ``uniform``/``choices``) is fixed; ints/shuffles stay seeded."""

    def __init__(self, value: float, seed: int = 3):
        super().__init__(seed)
        self.value = value

    def random(self) -> float:
        return self.value


class PushRecorder:
    def __init__(self):
        self.events: list[tuple[list[str], dict]] = []

    async def __call__(self, identities, event):
        targets = sorted(identities)
        self.events.append((targets, dict(event)))
        return len(targets)

    def of_type(self, kind: str) -> list[tuple[list[str], dict]]:
        return [item for item in self.events if item[1].get("type") == kind]


class WandererSocialTest(WandererWorldTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            from classroom_app.services.social_common import ensure_schema

            ensure_schema(conn)
            for table in SOCIAL_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        self.pushes = PushRecorder()
        for patcher in (patch.object(social_push_service, "push", self.pushes),
                        patch.object(social_push_service, "is_app_process", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.real = f"student:{self.student_pk}"
        self.teacher_identity = f"teacher:{self.teacher_id}"
        self.atai = self.set_wanderer("atai", schedule_json=ALWAYS_AWAKE, reply_policy_json=ALWAYS_REPLY,
                                      behaviors_json={})
        self.lele = self.set_wanderer("lele", schedule_json=ALWAYS_AWAKE, behaviors_json={})
        self.xiaoman = self.set_wanderer("xiaoman", schedule_json=ALWAYS_AWAKE, behaviors_json={})

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def ident(wanderer: dict) -> str:
        return f"student:{wanderer['student_id']}"

    @staticmethod
    def user_of(identity: str) -> dict:
        role, pk = identity.split(":")
        return {"id": int(pk), "role": role, "name": identity}

    def call(self, fn, *args, **kwargs):
        with self.connect() as conn:
            result = fn(conn, *args, **kwargs)
            conn.commit()
        return result

    def befriend(self, person: str, wanderer: dict) -> str:
        self.call(friends.send_request, self.user_of(person), self.ident(wanderer), "你好")
        self.call(friends.send_request, build_wanderer_user_dict(wanderer), person, "")
        return dm_key(person, self.ident(wanderer))

    def send(self, sender: str, key: str, body: str = "在吗", **extra) -> dict:
        return self.call(messages.send_message, self.user_of(sender), key, {"body": body, **extra})

    def tasks(self, kind: str) -> list[dict]:
        rows = self.rows("SELECT * FROM scheduled_tasks WHERE task_kind = ? ORDER BY id", (kind,))
        for row in rows:
            row["payload"] = json.loads(row["payload_json"] or "{}")
        return rows

    def run_reply(self, wanderer: dict, key: str, caller, *, rng=None, mentioned=False, sleep=None) -> str:
        task = {"payload": {"wanderer_id": wanderer["id"], "conversation_key": key, "mentioned": mentioned}}
        return asyncio.run(handle_wanderer_social_reply(task, ai_caller=caller, sleep=sleep or ZeroSleep(),
                                                        rng=rng or FixedRandom(0.5)))

    def messages_from(self, identity: str, key: str) -> list[dict]:
        return self.rows("SELECT * FROM social_messages WHERE sender_identity = ? AND conversation_key = ? ORDER BY id",
                         (identity, key))

    def reasons(self, wanderer: dict) -> list[str]:
        return [json.loads(row["detail_json"]).get("reason", "") for row in self.actions(wanderer["id"])]

    def make_group(self, *wanderers: dict) -> tuple[int, str]:
        for wanderer in wanderers:
            self.befriend(self.real, wanderer)
        card = self.call(groups.create_group, self.user_of(self.real), "修炼群", [self.ident(w) for w in wanderers])
        return int(card["id"]), card["conversation_key"]

    def insert_messages(self, key: str, sender: str, count: int, *, prefix: str) -> None:
        kind = "group" if key.startswith("group:") else "dm"
        with self.connect() as conn:
            for index in range(count):
                conn.execute(
                    "INSERT INTO social_messages (conversation_type, conversation_key, sender_identity, body, "
                    "message_type, client_nonce, created_at) VALUES (?, ?, ?, '刷', 'text', ?, ?)",
                    (kind, key, sender, f"{prefix}-{index}", now_iso()))
            conn.commit()

    # -- friend requests ----------------------------------------------------

    def test_friend_request_delay_follows_personality(self):
        self.assertGreater(sociability(self.lele), 0.8)
        self.assertLess(sociability(self.xiaoman), 0.35)
        before = datetime.now()
        for wanderer in (self.lele, self.xiaoman):
            self.call(friends.send_request, self.user_of(self.real), self.ident(wanderer), "交个朋友")
        rows = {row["payload"]["wanderer_id"]: row for row in self.tasks("wanderer_friend_request")}
        delay = {key: (datetime.fromisoformat(row["run_at"]) - before).total_seconds() for key, row in rows.items()}
        self.assertTrue(55 <= delay[self.lele["id"]] <= 180, delay)
        self.assertTrue(300 <= delay[self.xiaoman["id"]] <= 1805, delay)

    def test_nothing_is_scheduled_while_the_system_is_off(self):
        with patch.dict("os.environ", {"WANDERER_ENABLED": "false"}):
            self.call(friends.send_request, self.user_of(self.real), self.ident(self.lele), "")
        self.assertEqual(self.tasks("wanderer_friend_request"), [])

    def _request_id(self, requester: str, wanderer: dict) -> int:
        result = self.call(friends.send_request, self.user_of(requester), self.ident(wanderer), "你好呀")
        return int(result["request_id"])

    @staticmethod
    def _decide(wanderer: dict, request_id: int, value: float, *, deferred=False) -> str:
        task = {"payload": {"wanderer_id": wanderer["id"], "request_id": request_id, "deferred": deferred}}
        return asyncio.run(handle_wanderer_friend_request(task, rng=FixedRandom(value)))

    def _status(self, request_id: int) -> str:
        return self.rows("SELECT status FROM social_friendships WHERE id = ?", (request_id,))[0]["status"]

    def test_outgoing_wanderer_accepts_and_shy_one_defers_then_declines(self):
        accepted = self._request_id(self.real, self.lele)
        self.assertEqual(self._decide(self.lele, accepted, 0.9), "friend request: accept")
        self.assertEqual(self._status(accepted), "accepted")
        self.assertTrue(self.pushes.of_type("friend_accepted"))
        shy = self._request_id(self.real, self.xiaoman)
        self.assertEqual(self._decide(self.xiaoman, shy, 0.1), "friend request: defer")
        task = [row for row in self.tasks("wanderer_friend_request") if row["payload"]["request_id"] == shy][0]
        self.assertEqual(task["status"], "pending")
        self.assertTrue(task["payload"]["deferred"])
        self.assertGreater(datetime.fromisoformat(task["run_at"]), datetime.now() + timedelta(minutes=50))
        self.assertEqual(self._decide(self.xiaoman, shy, 0.99, deferred=True), "friend request: decline")
        self.assertEqual(self._status(shy), "declined")
        decisions = [json.loads(row["detail_json"])["decision"] for row in self.actions(self.xiaoman["id"])
                     if row["action_kind"] == "social_friend"]
        self.assertEqual(decisions, ["defer", "decline"])

    def test_friend_task_is_per_requester_and_follows_the_live_request(self):
        request_id = self._request_id(self.real, self.lele)
        task = self.tasks("wanderer_friend_request")[0]
        self.assertEqual(task["dedupe_key"], f"wanderer:friend:{self.lele['id']}:{self.real}")
        stale = {"payload": {"wanderer_id": self.lele["id"], "request_id": 999999, "requester": self.real}}
        self.assertEqual(asyncio.run(handle_wanderer_friend_request(stale, rng=FixedRandom(0.5))),
                         "friend request: accept")
        self.assertEqual(self._status(request_id), "accepted")

    def test_teacher_request_is_always_accepted(self):
        request_id = self._request_id(self.teacher_identity, self.xiaoman)
        self.assertEqual(self._decide(self.xiaoman, request_id, 0.99), "friend request: accept")

    def test_rude_request_is_declined(self):
        with self.connect() as conn:
            result = friends.send_request(conn, self.user_of(self.real), self.ident(self.lele), "你个废物")
            conn.commit()
        self.assertEqual(self._decide(self.lele, int(result["request_id"]), 0.0), "friend request: decline")

    # -- direct messages ----------------------------------------------------

    def test_dm_burst_coalesces_into_one_reply_task(self):
        key = self.befriend(self.real, self.atai)
        for body in ("在吗", "问你个事", "篮球赛去不去"):
            self.send(self.real, key, body)
        tasks = self.tasks("wanderer_social_reply")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["payload"]["conversation_key"], key)
        first_run_at = tasks[0]["run_at"]
        delay = (datetime.fromisoformat(first_run_at) - datetime.now()).total_seconds()
        self.assertTrue(0 <= delay <= 600, delay)
        self.send(self.real, key, "人呢")
        self.assertEqual(self.tasks("wanderer_social_reply")[0]["run_at"], first_run_at)  # not pushed back

    def test_asleep_wanderer_answers_in_its_next_window(self):
        hour = (datetime.now() + timedelta(hours=2)).hour
        self.set_wanderer("atai", schedule_json={"active_hours": [[hour, hour + 1]], "weekday_weights": {}})
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key, "睡了吗")
        delay = (datetime.fromisoformat(self.tasks("wanderer_social_reply")[0]["run_at"]) - datetime.now())
        self.assertTrue(timedelta(minutes=50) <= delay <= timedelta(hours=2, minutes=20), delay)

    def test_multi_part_reply_types_and_posts_each_part(self):
        self.set_wanderer("atai", reply_policy_json=ALWAYS_MULTI)
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key, "明天一起打球？")
        parts = ["冲！", "几点？", "我带球，你带水，球场见不见不散"]
        caller = StubCaller(turn("multi", parts, mood="兴奋"))
        sleep = ZeroSleep()
        result = self.run_reply(self.atai, key, caller, sleep=sleep)
        self.assertIn("ok parts=3", result)
        sent = [row["body"] for row in self.messages_from(self.ident(self.atai), key) if row["message_type"] == "text"]
        self.assertEqual(sent, parts)
        typing = self.pushes.of_type("typing")
        self.assertEqual(len(typing), 3)
        self.assertEqual(typing[0][0], [self.real])
        self.assertEqual(sleep.calls[:3], [typing_seconds(part) for part in parts])
        self.assertEqual(sleep.calls[0], 1.0)
        self.assertEqual(sleep.calls[2], round(len(parts[2]) * 0.12, 2))
        pushed = [item for item in self.pushes.of_type("message")
                  if item[1]["message"]["sender"]["identity"] == self.ident(self.atai)]
        self.assertEqual(len(pushed), 6)  # 3 messages x 2 participants
        self.assertTrue(self.pushes.of_type("read"))  # the peer sees the read receipt
        prompt = caller.calls[0][1]["system_prompt"]
        self.assertIn("【聊天场景】这是好友私聊", prompt)
        self.assertIn("真实同学（真实学生）", prompt)
        self.assertIn("明天一起打球？", prompt)
        self.assertEqual(caller.labels, ["wanderer:atai:social_chat"])
        logged = [row for row in self.actions(self.atai["id"]) if row["action_kind"] == "social_message"]
        self.assertEqual(len(logged), 1)
        threads = self.rows("SELECT thread_key FROM wanderer_memory_threads WHERE wanderer_id = ?", (self.atai["id"],))
        self.assertIn(f"social:{key}", [row["thread_key"] for row in threads])
        self.assertEqual(self.run_reply(self.atai, key, caller), "skipped: nothing new")  # a retry posts nothing

    def test_skip_turn_posts_nothing(self):
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key, "随便聊聊")
        self.run_reply(self.atai, key, StubCaller(turn("skip", [])))
        self.assertEqual([row for row in self.messages_from(self.ident(self.atai), key)
                          if row["message_type"] == "text"], [])
        self.assertIn("turn:skip", self.reasons(self.atai))
        self.assertEqual(self.pushes.of_type("typing"), [])

    def test_daily_cap_degrades_dm_to_emoji_and_silences_groups(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "0")
            conn.commit()
        key = self.befriend(self.real, self.atai)
        self.send(self.real, key, "早")
        caller = StubCaller(turn("reply", ["早啊"]))
        self.run_reply(self.atai, key, caller)
        self.assertEqual(caller.calls, [])
        texts = [row["body"] for row in self.messages_from(self.ident(self.atai), key) if row["message_type"] == "text"]
        self.assertEqual(len(texts), 1)
        self.assertLessEqual(len(texts[0]), 2)  # an emoji, not a sentence
        _group_id, group_key = self.make_group(self.lele)
        self.send(self.real, group_key, "大家好")
        self.run_reply(self.lele, group_key, caller, mentioned=True)
        self.assertEqual(caller.calls, [])
        self.assertEqual(self.messages_from(self.ident(self.lele), group_key), [])

    def test_social_behavior_switch_turns_the_wanderer_off(self):
        key = self.befriend(self.real, self.atai)
        self.set_wanderer("atai", behaviors_json={"social": 0})
        self.send(self.real, key, "在吗")
        self.assertEqual(self.tasks("wanderer_social_reply"), [])
        self.assertEqual(self.run_reply(self.atai, key, StubCaller()), "skipped: social_off")

    def test_per_conversation_rate_limit(self):
        key = self.befriend(self.real, self.atai)
        self.insert_messages(key, self.ident(self.atai), 6, prefix="seed")
        self.send(self.real, key, "你怎么这么多话")
        caller = StubCaller(turn("reply", ["嘿嘿"]))
        self.run_reply(self.atai, key, caller)
        self.assertEqual(caller.calls, [])
        self.assertIn("rate_limited", self.reasons(self.atai))

    # -- groups -------------------------------------------------------------

    def test_group_messages_debounce_into_one_watch(self):
        group_id, key = self.make_group(self.atai, self.lele)
        for body in ("一", "二", "三"):
            self.send(self.real, key, body)
        watches = self.tasks("wanderer_group_watch")
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0]["payload"], {"group_id": group_id})
        delay = (datetime.fromisoformat(watches[0]["run_at"]) - datetime.now()).total_seconds()
        self.assertTrue(25 <= delay <= 121, delay)

    def test_mention_forces_consideration_and_others_just_read(self):
        group_id, key = self.make_group(self.atai, self.lele)
        self.send(self.real, key, "@阿泰 周末打球吗", mentions=[self.ident(self.atai)])
        summary = asyncio.run(watch_group(group_id, rng=FixedRandom(0.99)))
        self.assertEqual([item["identity"] for item in summary["scheduled"]], [self.ident(self.atai)])
        self.assertTrue(summary["scheduled"][0]["mentioned"])
        self.assertTrue(5 <= summary["scheduled"][0]["delay"] <= 40)
        self.assertEqual(summary["passed"], [self.ident(self.lele)])
        cursor = self.rows("SELECT last_read_message_id FROM social_group_members WHERE member_identity = ?",
                           (self.ident(self.lele),))[0]["last_read_message_id"]
        self.assertGreater(cursor, 0)
        reply = [row for row in self.tasks("wanderer_social_reply") if row["payload"]["conversation_key"] == key]
        self.assertEqual(len(reply), 1)
        self.assertTrue(reply[0]["payload"]["mentioned"])
        caller = StubCaller(turn("reply", ["打！"]))
        self.run_reply(self.atai, key, caller, mentioned=True)
        self.assertEqual([row["body"] for row in self.messages_from(self.ident(self.atai), key)], ["打！"])
        prompt = caller.calls[0][1]["system_prompt"]
        self.assertIn("【聊天场景】这是一个群聊", prompt)
        self.assertIn("乐乐（散修", prompt)
        self.assertEqual(caller.labels, ["wanderer:atai:group_chat"])

    def test_wanderers_are_staggered(self):
        group_id, key = self.make_group(self.atai, self.lele, self.xiaoman)
        everyone = [self.ident(w) for w in (self.atai, self.lele, self.xiaoman)]
        self.send(self.real, key, "散修们出来聊天", mentions=everyone)
        summary = asyncio.run(watch_group(group_id, rng=FixedRandom(0.99, seed=11)))
        delays = [item["delay"] for item in summary["scheduled"]]
        self.assertEqual(len(delays), 3)
        steps = [delays[0]] + [later - earlier for earlier, later in zip(delays, delays[1:])]
        self.assertTrue(all(5 <= step <= 40 for step in steps), steps)

    def test_group_daily_cap_per_wanderer(self):
        group_id, key = self.make_group(self.atai)
        self.insert_messages(key, self.ident(self.atai), GROUP_DAILY_CAP, prefix="cap")
        self.send(self.real, key, "@阿泰 还在吗", mentions=[self.ident(self.atai)])
        summary = asyncio.run(watch_group(group_id, rng=FixedRandom(0.0)))
        self.assertEqual(summary["scheduled"], [])
        self.assertEqual(group_reply_probability(self.atai, interest=0, mentioned=True, streak=0,
                                                 last_is_wanderer=False, sent_today=GROUP_DAILY_CAP), 0.0)

    def test_wanderer_loop_guard(self):
        group_id, key = self.make_group(self.atai, self.lele, self.xiaoman)
        self.send(self.real, key, "你们聊")
        self.send(self.ident(self.atai), key, "我先说")
        self.send(self.ident(self.lele), key, "@小满 你说呢", mentions=[self.ident(self.xiaoman)])
        caller = StubCaller(turn("reply", ["嗯"]))
        self.run_reply(self.xiaoman, key, caller, mentioned=True)  # even a mention does not break the guard
        self.assertEqual(caller.calls, [])
        self.assertIn("wanderer_loop", self.reasons(self.xiaoman))
        self.send(self.ident(self.lele), key, "@阿泰 你呢", mentions=[self.ident(self.atai)])
        summary = asyncio.run(watch_group(group_id, rng=FixedRandom(0.0)))
        self.assertEqual(summary["scheduled"], [])  # two wanderer-only turns in a row: nobody joins in
        human = group_reply_probability(self.lele, interest=0, mentioned=False, streak=0, last_is_wanderer=False,
                                        sent_today=0)
        after_wanderer = group_reply_probability(self.lele, interest=0, mentioned=False, streak=1,
                                                 last_is_wanderer=True, sent_today=0)
        self.assertLess(after_wanderer, human * 0.3)

    def test_wanderer_never_answers_itself(self):
        _group_id, key = self.make_group(self.atai)
        self.send(self.ident(self.atai), key, "自言自语")
        self.assertEqual(self.run_reply(self.atai, key, StubCaller()), "skipped: nothing new")

    # -- proactive requests, presence, admin, 天道 ----------------------------

    def _engaged_student(self) -> None:
        """The real student replies (real name) to the wanderer's comment under a teacher's post."""
        post_id = self.make_post()
        with self.connect() as conn:
            mine = blog_service.add_comment(conn, build_wanderer_user_dict(self.atai), post_id, content_md="练起来！")
            blog_service.add_comment(conn, self.student, post_id, content_md="一起！", parent_comment_id=int(mine["id"]))
            conn.commit()

    def test_proactive_request_to_engaged_student_and_weekly_limit(self):
        self._engaged_student()
        ctx, _ = social_context(self.atai["id"], ai_caller=StubCaller(turn("reply", ["一起练球？"])),
                                sleep=ZeroSleep(), rng=FixedRandom(0.0))
        done = asyncio.run(maybe_befriend(ctx, chance=1.0))
        self.assertEqual([item["outcome"] for item in done], ["ok"])
        row = self.rows("SELECT * FROM social_friendships")[0]
        self.assertEqual(row["requester_identity"], self.ident(self.atai))
        self.assertEqual(row["request_message"], "一起练球？")
        self.assertEqual(row["status"], "pending")
        with self.connect() as conn:
            conn.execute("DELETE FROM social_friendships")
            log_wanderer_action(conn, self.atai["id"], "social_friend", target_type=TARGET_REQUEST_OUT, target_id="x")
            conn.commit()
        caller = StubCaller()
        ctx, _ = social_context(self.atai["id"], ai_caller=caller, sleep=ZeroSleep(), rng=FixedRandom(0.0))
        self.assertEqual(asyncio.run(maybe_befriend(ctx, chance=1.0)), [])  # 2 this week already
        self.assertEqual(caller.calls, [])

    def test_presence_changes_are_pushed_to_friends_only(self):
        from classroom_app.services.presence_service import presence_map

        self.befriend(self.real, self.atai)
        first = asyncio.run(sync_wanderer_presence())
        self.assertTrue(first["first_run"])
        self.assertEqual(self.pushes.of_type("presence"), [])
        self.set_wanderer("atai", schedule_json=NEVER_AWAKE)
        second = asyncio.run(sync_wanderer_presence())
        events = self.pushes.of_type("presence")
        self.assertEqual(second["pushed"], 1)
        self.assertEqual(events[0][0], [self.real])
        self.assertEqual(events[0][1]["identity"], self.ident(self.atai))
        self.assertFalse(events[0][1]["online"])
        self.assertEqual(len(self.rows("SELECT 1 FROM user_presence WHERE identity = ?", (self.ident(self.atai),))), 1)
        with self.connect() as conn:
            self.assertFalse(presence_map(conn, [self.ident(self.atai)])[self.ident(self.atai)]["online"])

    def test_admin_detail_and_dao_evidence_include_social_activity(self):
        from classroom_app.services.heavenly_dao_evidence import collect_evidence
        from classroom_app.services.wanderer_admin_service import build_wanderer_detail

        key = self.befriend(self.real, self.atai)
        for index in range(7):
            self.send(self.ident(self.atai), key, f"刷屏{index}")
        with self.connect() as conn:
            detail = build_wanderer_detail(conn, self.atai["id"])
            evidence = collect_evidence(conn, self.wanderer(conn, "atai"), datetime.now())
        self.assertEqual(detail["stats"]["social"]["friends"], 1)
        self.assertEqual(detail["stats"]["social"]["messages_today"], 7)
        self.assertEqual(evidence["social_24h"]["messages_24h"], 7)
        self.assertEqual(evidence["social_24h"]["conversations_flooded"], 1)
        self.assertEqual(evidence["recent_chat"][0], "刷屏6")


class SocialReferenceAndNonceTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        self.key = dm_key(self.a, self.b)

    def test_global_store_references_are_counted(self):
        data = b"shared global bytes " * 64
        digest = self.put_global_file(data)
        spec = {"sha256": sha(data), "size": len(data), "mime": "application/pdf", "filename": "讲义.pdf"}
        session, _ = self.run_service(media.upload_init, self.user(self.a), **spec)
        self.run_service(media.write_chunk, self.user(self.a), session["upload_id"], 0, data)
        token, _ = self.run_service(media.upload_complete, self.user(self.a), session["upload_id"])
        with self.connect() as conn:
            self.assertEqual(file_service.count_global_file_references(conn, digest), 1)  # the unused token
        self.run_service(messages.send_message, self.user(self.a), self.key,
                         {"body": "", "media_tokens": [token["media_token"]]})
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_message_media WHERE global_file_hash = ?",
                                    (digest,)), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_media_tokens WHERE global_file_hash <> ''"), 0)
        with self.connect() as conn:
            self.assertEqual(file_service.count_global_file_references(conn, digest), 1)
            conn.execute("UPDATE social_message_media SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
            retention.run_retention(conn)
            conn.commit()
        with self.connect() as conn:
            self.assertEqual(file_service.count_global_file_references(conn, digest), 0)
        self.assertTrue(file_service.resolve_global_file_path(digest).is_file())  # bytes untouched

    def test_social_store_media_has_no_global_reference(self):
        data = b"social-only bytes"
        with self.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(social_message_media)").fetchall()}
        self.assertIn("global_file_hash", columns)
        result, _ = self.run_service(media.precheck, self.user(self.a), sha256=sha(data), size=len(data),
                                     mime="text/plain", filename="a.txt")
        self.assertFalse(result["exists"])

    def test_scheduler_process_pushes_in_one_batch(self):
        posted: list[dict] = []

        async def fake_post(body):
            posted.append(body)
            return 7

        pairs = [([self.a], {"type": "message", "n": 1}), ([self.b], {"type": "message", "n": 2}),
                 ([self.a, self.b], {"type": "typing", "conversation_key": self.key})]
        remote = patch.object(social_push_service, "is_app_process", return_value=False)
        with remote, patch.object(social_push_service, "_post_internal", fake_post):
            delivered = asyncio.run(social_push_service.push_events(pairs))
        self.assertEqual(delivered, 7)
        self.assertEqual(len(posted), 1)
        self.assertEqual([item["event"].get("n") for item in posted[0]["batch"]], [1, 2, None])

    def test_internal_push_accepts_a_batch(self):
        from classroom_app.services.wanderer_settings_service import INTERNAL_TOKEN_HEADER, wanderer_internal_token

        client = self.client_as(self.a)
        headers = {INTERNAL_TOKEN_HEADER: wanderer_internal_token()}
        body = {"batch": [{"identities": [self.a], "event": {"type": "typing", "conversation_key": self.key}}]}
        response = client.post("/api/internal/social/push", json=body, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["delivered"], 0)  # nobody connected in the test
        bad = client.post("/api/internal/social/push", json={"batch": "x"}, headers=headers)
        self.assertEqual(bad.status_code, 400)
        denied = client.post("/api/internal/social/push", json=body, headers={INTERNAL_TOKEN_HEADER: "nope"})
        self.assertEqual(denied.status_code, 401)

    def test_concurrent_duplicate_nonce_returns_the_stored_message(self):
        client = self.client_as(self.a)
        url = f"/api/social/conversations/{self.key}/messages"
        first = client.post(url, json={"body": "只发一次", "client_nonce": "nonce-race"})
        self.assertEqual(first.status_code, 200)
        # The second request passed the pre-check before the first committed (the race window).
        with patch.object(messages, "_existing_by_nonce", return_value=None):
            second = client.post(url, json={"body": "只发一次", "client_nonce": "nonce-race"})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM social_messages WHERE client_nonce = 'nonce-race'"), 1)
        history, _ = self.run_service(conversations.list_messages, self.user(self.a), self.key)
        self.assertEqual([item["body"] for item in history["items"]].count("只发一次"), 1)
