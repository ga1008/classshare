"""去 AI 味 (docs/wanderer-ai-free-agents-2026-10-25.md §10.5): rules in every prompt, formatting stripped."""
import unittest

from classroom_app.services.wanderer_prompt_service import build_system_prompt
from classroom_app.services.wanderer_text_style import ANTI_AI_STYLE_RULES, strip_ai_formatting
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


class AntiAiRulesInPromptTest(WandererDbTestCase):
    def test_rules_follow_the_persona_block(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            prompt = build_system_prompt(conn, wanderer, scene={"kind": "浏览"}, thread_key="blog_post:1")
        self.assertIn(ANTI_AI_STYLE_RULES, prompt)
        self.assertLess(prompt.index("【你的人格】"), prompt.index(ANTI_AI_STYLE_RULES))
        self.assertLess(prompt.index(ANTI_AI_STYLE_RULES), prompt.index("【你的境界】"))
        for phrase in ("不用任何 Markdown", "表达自由", "对真实用户的谨慎", "交给天道"):
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
