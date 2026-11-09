"""散修 write_post (docs/wanderer-ai-free-agents-2026-10-25.md §10.4): stub AI, real create_post path."""
import asyncio
import json
import random
import unittest

from classroom_app.services import wanderer_behavior_service
from classroom_app.services.wanderer_actions_blog import (
    POST_BODY_MAX_CHARS,
    POST_BODY_MIN_CHARS,
    _post_body_limit,
    _post_output_contract,
    post_draft,
    post_eligible,
    write_post,
)
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_actions_knowledge import INTERACTION_KINDS
from classroom_app.services.wanderer_identity_service import log_wanderer_action
from classroom_app.services.wanderer_settings_service import SETTING_BLOG_POSTS_DAILY_CAP, wanderer_runtime_setting_set
from tests.wanderer_behavior_support import ONLY_BLOG, StubCaller, WandererWorldTestCase, ZeroSleep

BODY = ("今天早上跑了五公里，配速比上周快了十秒。\n\n跑到第三公里的时候差点想停，"
        "后来想起教练说的，难受的时候就盯着前面十米。\n\n1. 早点睡\n2. 跑前别吃太饱\n3. 别和别人比，和昨天的自己比")
NOTHING_ELSE = {key: 0 for key in ONLY_BLOG}


def draft(action="reply", title="**晨跑**第五天", parts=None):
    return json.dumps({"action": action, "title": title,
                       "parts": parts if parts is not None else ["## 记录\n" + BODY, "- 明天继续 💪"],
                       "mood": "爽"}, ensure_ascii=False)


class WandererBlogPostTest(WandererWorldTestCase):
    def ctx(self, slug, caller, **fields):
        fields.setdefault("tier", 3)  # 400-char active cap keeps the long fixture intact
        wanderer = self.set_wanderer(slug, behaviors_json={**ONLY_BLOG, "blog_post": 1}, **fields)
        return ActionContext(conn_factory=self.connect, wanderer=wanderer, ai_caller=caller,
                             sleep=ZeroSleep(), rng=random.Random(3))

    @staticmethod
    def run_action(ctx):
        return asyncio.run(write_post(ctx))

    def posts(self, student_id):
        return self.rows("SELECT * FROM blog_posts WHERE author_identity = ? ORDER BY id", (f"student:{student_id}",))

    def test_write_post_publishes_through_create_post_with_plain_text(self):
        caller = StubCaller(draft())
        ctx = self.ctx("atai", caller)
        done = self.run_action(ctx)
        self.assertEqual([item["kind"] for item in done], ["write_post"])
        self.assertEqual(caller.labels, ["wanderer:atai:write_post"])
        prompt = caller.calls[0][1]["system_prompt"]
        self.assertIn('"title"', prompt)
        self.assertIn("【表达自由】", prompt)
        posts = self.posts(ctx.student_id)
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual((post["title"], post["section_key"], post["status"]), ("晨跑第五天", "general", "published"))
        self.assertNotIn("**", post["content_md"])
        self.assertNotIn("##", post["content_md"])
        self.assertIn("1. 早点睡", post["content_md"])
        self.assertIn("明天继续", post["content_md"])
        self.assertEqual(post["moderation_state"], "pending")  # the normal review plan for a new student post
        logged = [row for row in self.actions(ctx.wanderer_id) if row["action_kind"] == "write_post"]
        self.assertEqual((logged[0]["target_type"], logged[0]["target_id"]), ("blog_post", str(post["id"])))
        threads = self.rows("SELECT thread_key FROM wanderer_memory_threads WHERE wanderer_id = ?", (ctx.wanderer_id,))
        self.assertIn(f"blog_post:{post['id']}", [row["thread_key"] for row in threads])
        self.assertIn("write_post", INTERACTION_KINDS)

    def test_one_post_per_wanderer_per_day_and_global_cap(self):
        ctx = self.ctx("atai", StubCaller(draft()))
        self.run_action(ctx)
        with self.connect() as conn:
            self.assertFalse(post_eligible(ctx, conn))
        second = StubCaller(draft(title="又一篇"))
        ctx.ai_caller = second
        self.assertEqual(self.run_action(ctx), [])
        self.assertEqual(second.calls, [])
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_BLOG_POSTS_DAILY_CAP, "1")
            conn.commit()
        other = self.ctx("momo", StubCaller(draft(title="Momo 的帖子")))
        with self.connect() as conn:
            self.assertFalse(post_eligible(other, conn))  # 1 post today across all wanderers
        self.assertEqual(self.run_action(other), [])

    def test_switch_off_skip_and_too_short_drafts_publish_nothing(self):
        off = self.ctx("atai", StubCaller(draft()))
        off.wanderer = {**off.wanderer, "behaviors": {**off.wanderer["behaviors"], "blog_post": 0}}
        self.assertEqual(self.run_action(off), [])
        for text in (draft(action="skip"), draft(parts=["太短"]), "不是 JSON"):
            ctx = self.ctx("momo", StubCaller(text))
            self.assertEqual([item["outcome"] for item in self.run_action(ctx)], ["skipped"])
        self.assertEqual(self.posts(ctx.student_id), [])

    def test_repeated_own_phrase_is_not_published_as_post(self):
        repeated = ("今天早上跑了五公里，配速比上周快了十秒。跑到第三公里的时候差点想停，后来还是慢慢跑完了。"
                    "回来喝了点水，洗了个澡，坐在窗边发了会儿呆，觉得今天状态还不错。明天还想再跑一次，看看能不能快一点。")
        ctx = self.ctx("atai", StubCaller(draft()))
        with self.connect() as conn:
            log_wanderer_action(conn, ctx.wanderer_id, "comment", content_preview=repeated)
            conn.commit()
        ctx.ai_caller = StubCaller(draft(parts=[repeated]))
        self.assertEqual([item["outcome"] for item in self.run_action(ctx)], ["skipped"])
        self.assertEqual(self.posts(ctx.student_id), [])
        skips = [row for row in self.actions(ctx.wanderer_id) if row["action_kind"] == "skip"]
        self.assertEqual(json.loads(skips[-1]["detail_json"])["reason"], "repetition")

    def test_post_style_guard_removes_summary_and_advice(self):
        body = ("总的来说，今天早上跑了五公里，配速比上周快了十秒。跑到第三公里的时候差点想停，后来还是慢慢跑完了。"
                "回来喝了点水，洗了个澡，坐在窗边发了会儿呆，觉得今天状态还不错。明天还想再跑一次，看看能不能快一点。"
                "建议你早点睡。")
        ctx = self.ctx("atai", StubCaller(draft(parts=[body])))
        self.run_action(ctx)
        posts = self.posts(ctx.student_id)
        self.assertEqual(len(posts), 1)
        text = posts[0]["content_md"]
        self.assertNotIn("总的来说", text)
        self.assertNotIn("建议你", text)
        self.assertIn("今天早上跑了五公里", text)
        logged = [row for row in self.actions(ctx.wanderer_id) if row["action_kind"] == "write_post"]
        style = json.loads(logged[0]["detail_json"])["style"]
        self.assertIn("summary", style)
        self.assertIn("advice", style)

    def test_post_body_limits_follow_p0(self):
        self.assertEqual(POST_BODY_MIN_CHARS, 80)
        self.assertEqual(POST_BODY_MAX_CHARS, 260)
        self.assertEqual(_post_body_limit({"tier": 0}), 100)
        self.assertEqual(_post_body_limit({"tier": 1}), 200)
        self.assertEqual(_post_body_limit({"tier": 2}), 260)
        self.assertEqual(_post_body_limit({"tier": 6}), 260)

    def test_tick_chooses_post_only_when_the_roll_passes(self):
        atai = self.set_wanderer("atai", behaviors_json={**NOTHING_ELSE, "blog_post": 1}, activity_level=1,
                                 reply_policy_json={"post_prob": 1.0, "max_parts": 2})
        caller = StubCaller(draft())
        self.tick(caller, [atai["id"]])
        self.assertEqual(caller.labels, ["wanderer:atai:write_post"])
        momo = self.set_wanderer("momo", behaviors_json={**NOTHING_ELSE, "blog_post": 1}, activity_level=1,
                                 reply_policy_json={"post_prob": 0.0, "max_parts": 2})
        never = StubCaller(draft())
        self.tick(never, [momo["id"]])
        self.assertEqual(never.calls, [])
        self.assertIn("post", wanderer_behavior_service.ACTIONS)

    def test_post_draft_parser(self):
        action, title, body, mood = post_draft(draft(title="# 标题" + "长" * 40))
        self.assertEqual((action, mood), ("reply", "爽"))
        self.assertEqual(len(title), 30)
        self.assertFalse(title.startswith("#"))
        self.assertTrue(body.startswith("记录"))

    def test_post_draft_preserves_world_identity_and_trims_to_sentence(self):
        raw = json.dumps({"action": "reply", "title": "散修日记",
                          "parts": ["我是来旁听的散修乐乐。今天在平台的互动区坐了一会儿。"],
                          "mood": "平静"}, ensure_ascii=False)
        action, title, body, mood = post_draft(raw, max_body=100)
        self.assertEqual((action, title, mood), ("reply", "散修日记", "平静"))
        self.assertEqual(body, "我是来旁听的散修乐乐。今天在平台的互动区坐了一会儿。")

    def test_post_draft_uses_sentence_boundary_when_clipping(self):
        raw = json.dumps({"action": "reply", "title": "标题",
                          "parts": ["第一句。" + "好" * 100]}, ensure_ascii=False)
        _, _, body, _ = post_draft(raw, max_body=20)
        self.assertEqual(body, "第一句。")

    def test_post_contract_discourages_aphorism_ending(self):
        contract = _post_output_contract({"tier": 0})
        self.assertIn("不要升华", contract)
        self.assertIn("不要祝福", contract)
        self.assertIn("停在事实", contract)

    def test_tier_zero_post_body_is_capped_at_one_hundred_chars(self):
        ctx = self.ctx("momo", StubCaller(draft(parts=["好" * 150])), tier=0)
        self.run_action(ctx)
        posts = self.posts(ctx.student_id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(posts[0]["content_md"]), 100)


if __name__ == "__main__":
    unittest.main()
