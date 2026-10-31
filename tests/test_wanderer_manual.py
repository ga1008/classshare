import asyncio
import json
import unittest

from classroom_app.services import wanderer_platform_manual as manual
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_manual_action import MANUAL_DAILY_LIMIT, read_manual
from classroom_app.services.wanderer_prompt_service import build_system_prompt
from tests.wanderer_behavior_support import StubCaller, ZeroSleep
from tests.wanderer_test_support import WandererDbTestCase


class ManualContentTest(unittest.TestCase):
    def test_friend_request_mechanism_is_explained_without_inventing(self):
        text = manual.section("friend_requests")
        self.assertIn("同一条", text)
        self.assertIn("不会每天变成新的", text)
        self.assertIn("忽略", text)
        self.assertIn("7 天", text)

    def test_scene_selects_the_relevant_topics(self):
        topics = manual.scene_topics({"kind": "审核好友申请", "title": "某人的好友申请（同一条）"})
        self.assertEqual(topics[:2], ["friend_requests", "private_messages"])
        self.assertIn("friend_requests", manual.search("好友申请一直显示", limit=2))
        self.assertNotIn("不存在的主题", manual.TOPICS)

    def test_consult_accepts_a_title_or_key_and_marks_unknown(self):
        found = manual.consult("好友申请")
        self.assertEqual(found["topic"], "friend_requests")
        self.assertIn("平台手册", found["text"])
        self.assertEqual(manual.consult("完全未知"), {"topic": "", "title": "", "text": ""})

    def test_block_is_bounded_and_has_a_menu(self):
        text = manual.block({"kind": "群聊"})
        self.assertIn("平台手册", text)
        self.assertIn("平台手册目录", text)
        self.assertLessEqual(len(text), 4200)


class ManualPromptAndActionTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute("DELETE FROM wanderer_action_log")
            conn.execute("DELETE FROM wanderer_episodic_memories")
            conn.commit()

    def _ctx(self, caller):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
        wanderer = {**wanderer, "schedule": {**(wanderer.get("schedule") or {}), "active_hours": [[0, 24]]}}
        from datetime import datetime
        return ActionContext(conn_factory=self.connect, wanderer=wanderer, ai_caller=caller, sleep=ZeroSleep(),
                             now=datetime(2026, 10, 31, 12, 0, 0))

    def test_friend_request_scene_gets_the_mechanism_in_the_prompt(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            prompt = build_system_prompt(conn, wanderer, scene={"kind": "审核好友申请", "title": "某人 的好友申请"},
                                         thread_key="social:friend:student:2")
        self.assertIn("好友申请到底怎么运作", prompt)
        self.assertIn("同一条", prompt)

    def test_read_manual_records_a_located_memory_and_its_daily_limit(self):
        ctx = self._ctx(StubCaller(json.dumps({"topic": "friend_requests", "reason": "我一直以为他每天申请"},
                                              ensure_ascii=False)))
        result = asyncio.run(read_manual(ctx))
        self.assertEqual(result[0]["kind"], "read_manual")
        with self.connect() as conn:
            action = conn.execute("SELECT action_kind, location, target_id FROM wanderer_action_log "
                                  "WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (ctx.wanderer_id,)).fetchone()
            memory = conn.execute("SELECT kind, location, content FROM wanderer_episodic_memories "
                                  "WHERE wanderer_id = ? ORDER BY id DESC LIMIT 1", (ctx.wanderer_id,)).fetchone()
        self.assertEqual((action["action_kind"], action["target_id"]), ("read_manual", "friend_requests"))
        self.assertIn("平台手册", action["location"])
        self.assertEqual(memory["kind"], "knowledge")
        self.assertIn("好友申请", memory["location"])
        self.assertIn("同一条", memory["content"])

        for _ in range(MANUAL_DAILY_LIMIT - 1):
            asyncio.run(read_manual(self._ctx(StubCaller(json.dumps({"topic": "time", "reason": "看看时间"},
                                                                   ensure_ascii=False)))))
        self.assertEqual(asyncio.run(read_manual(self._ctx(StubCaller(json.dumps({"topic": "time", "reason": "x"}))))), [])

    def test_skip_is_logged_without_a_memory(self):
        result = asyncio.run(read_manual(self._ctx(StubCaller('{"action":"skip","reason":"现在不想看"}'))))
        self.assertEqual(result[0]["outcome"], "skipped")
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_episodic_memories").fetchone()[0], 0)
