"""去 AI 味 (docs/wanderer-ai-free-agents-2026-10-25.md §10.5): rules in every prompt, formatting stripped."""
import unittest

from classroom_app.services.wanderer_prompt_service import build_system_prompt
from classroom_app.services.wanderer_text_style import (
    ANTI_AI_STYLE_RULES,
    GROUP_ECHO_RULES,
    PUBLIC_TEXT_RULES,
    VOICE_FIRST_RULES,
    clip_at_sentence_boundary,
    strip_ai_formatting,
    strip_platform_meta,
)
from tests.wanderer_test_support import WandererDbTestCase


class StripAiFormattingTest(unittest.TestCase):
    def test_markdown_markers_are_removed_but_text_kept(self):
        self.assertEqual(strip_ai_formatting("这个**真的**很*有意思*，用 `print` 就行"), "这个真的很有意思，用 print 就行")
        self.assertEqual(strip_ai_formatting("## 我的想法\n> 引用一句\n正文"), "我的想法\n引用一句\n正文")
        self.assertEqual(strip_ai_formatting("看这里 [链接](https://example.test/x) 吧"), "看这里 链接 吧")

    def test_bullets_and_emoji_bullets_become_plain_lines(self):
        text = "- 第一条\n* 第二条\n✅ 完成了\n📌 重点在这"
        self.assertEqual(strip_ai_formatting(text), "第一条\n第二条\n完成了\n重点在这")

    def test_numbered_lines_and_paragraphs_survive(self):
        text = "先说结论。\n\n1. 早睡\n2. 多喝水\n\n就这样"
        self.assertEqual(strip_ai_formatting(text), text)

    def test_fences_rules_tables_and_blank_runs(self):
        text = "开头\n```python\nprint(1)\n```\n---\n| 名字 | 分数 |\n| --- | --- |\n| 小明 | 90 |\n\n\n\n\n结尾"
        self.assertEqual(strip_ai_formatting(text), "开头\nprint(1)\n\n名字，分数\n\n小明，90\n\n结尾")

    def test_plain_chat_and_emoji_replies_are_untouched(self):
        for text in ("哈哈哈哈", "😂😂", "冲就完事了！💪", "2*3=6 吗", "snake_case_name 挺好"):
            self.assertEqual(strip_ai_formatting(text), text)
        self.assertEqual(strip_ai_formatting(None), "")


class PlatformMetaTest(unittest.TestCase):
    def test_public_platform_terms_are_rewritten(self):
        cases = {
            "我是来旁听的散修乐乐～": "我是来旁听的乐乐～",
            "散修嘛，跟校园里的热闹有点隔着。": "我嘛，跟校园里的热闹有点隔着。",
            "这个平台的境界和修为": "这里的水平和本事",
            "天道不公，互动区也没人理我。": "老天不公，这儿也没人理我。",
            "散修之间不用客气": "我们之间不用客气",
            "我是散修。": "我就是我。",
            "我是散修乐乐": "我是乐乐",
        }
        for text, expected in cases.items():
            self.assertEqual(strip_platform_meta(text), expected)

    def test_plain_text_is_untouched(self):
        for text in ("今天天气不错。", "哈哈哈哈", "2*3=6 吗"):
            self.assertEqual(strip_platform_meta(text), text)

    def test_everyday_words_containing_a_term_survive(self):
        for text in ("我最近到了平台期，进步很慢。", "平台化改造还没做完。", "互动区域有点吵。"):
            self.assertEqual(strip_platform_meta(text), text)

    def test_clip_at_sentence_boundary(self):
        self.assertEqual(clip_at_sentence_boundary("第一句。" + "好" * 100, 20), "第一句。")
        self.assertEqual(clip_at_sentence_boundary("好" * 100, 20), "好" * 20)
        self.assertEqual(clip_at_sentence_boundary("第一段。\n\n第二段。" + "好" * 100, 20), "第一段。\n\n第二段。")
        self.assertEqual(clip_at_sentence_boundary("他说：“好。”后面还有很多字", 8), "他说：“好。”")


class PublicTextPromptTest(WandererDbTestCase):
    def test_public_scene_gets_public_text_rules(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            public = build_system_prompt(conn, wanderer, scene={"kind": "博客帖子"}, thread_key="blog_draft")
            internal = build_system_prompt(conn, wanderer, scene={"kind": "每日自省"}, thread_key="reflection")
        self.assertIn(PUBLIC_TEXT_RULES, public)
        self.assertIn(VOICE_FIRST_RULES, public)
        self.assertLess(public.index(ANTI_AI_STYLE_RULES), public.index(VOICE_FIRST_RULES))
        self.assertLess(public.index(VOICE_FIRST_RULES), public.index(PUBLIC_TEXT_RULES))
        self.assertNotIn(PUBLIC_TEXT_RULES, internal)
        self.assertNotIn(VOICE_FIRST_RULES, internal)

    def test_group_scenes_get_echo_rules_but_dm_does_not(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            classroom = build_system_prompt(conn, wanderer, scene={"kind": "课堂互动区（旁听）"},
                                            thread_key="classroom:1")
            group = build_system_prompt(conn, wanderer, scene={"kind": "群聊"}, thread_key="social:group:1")
            dm = build_system_prompt(conn, wanderer, scene={"kind": "好友私聊"}, thread_key="social:friend:2")
        self.assertIn(GROUP_ECHO_RULES, classroom)
        self.assertIn(GROUP_ECHO_RULES, group)
        self.assertNotIn(GROUP_ECHO_RULES, dm)


class AntiAiRulesInPromptTest(WandererDbTestCase):
    def test_rules_follow_the_persona_block(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            prompt = build_system_prompt(conn, wanderer, scene={"kind": "浏览"}, thread_key="blog_post:1")
        self.assertIn(ANTI_AI_STYLE_RULES, prompt)
        self.assertLess(prompt.index("【你的人格】"), prompt.index(ANTI_AI_STYLE_RULES))
        self.assertLess(prompt.index(ANTI_AI_STYLE_RULES), prompt.index("【你的境界】"))
        for phrase in ("不用任何 Markdown", "表达自由", "没有额外的系统禁令", "后果由天道裁定"):
            self.assertIn(phrase, ANTI_AI_STYLE_RULES)

    def test_scene_can_replace_the_output_contract(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            prompt = build_system_prompt(conn, wanderer, scene={"kind": "写", "output_contract": "【合同X】"},
                                         thread_key="blog_draft")
        self.assertTrue(prompt.endswith("【合同X】"))
        self.assertNotIn("skip|emoji|reply|multi", prompt)


if __name__ == "__main__":
    unittest.main()
