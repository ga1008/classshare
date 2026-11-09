"""Deterministic post-generation style guard (audit P1)."""
import asyncio
import unittest

from classroom_app.services.wanderer_style_guard import (
    guard_parts,
    guard_text,
    guard_turn,
    lint,
    repair,
)


class StyleGuardTest(unittest.TestCase):
    def test_lint_detects_high_confidence_patterns(self):
        text = "我来总结一下：这章我读了。补充一点：建议你早点睡。"
        rules = {item.rule for item in lint(text, channel="comment")}
        self.assertTrue({"summary", "teaching", "advice"} <= rules)

    def test_list_and_hedge_and_meta_are_detected(self):
        rules = {item.rule for item in lint("1. 先说结论。\n2. 再说原因。其实这个平台不过如此。")}
        self.assertTrue({"list", "hedge", "meta"} <= rules)

    def test_summary_sentence_is_removed(self):
        text = "这章我读了。我来总结一下：重点是复习。"
        result = guard_text(text, channel="comment")
        self.assertEqual(result.text, "这章我读了。重点是复习。")
        self.assertTrue(result.repaired)

    def test_teaching_prefix_and_list_markers_are_removed(self):
        result = guard_text("补充一点：\n1. 先说结论。\n2. 再说原因。", channel="classroom")
        self.assertNotIn("补充一点", result.text)
        self.assertNotIn("1.", result.text)
        self.assertNotIn("2.", result.text)
        self.assertIn("先说结论", result.text)

    def test_closing_advice_is_removed_but_disagreement_is_kept(self):
        result = guard_text("这章我读了。建议你早点睡。", channel="dm")
        self.assertEqual(result.text, "这章我读了。")
        self.assertEqual(guard_text("我不同意这个做法。", channel="dm").text, "我不同意这个做法。")

    def test_hedge_and_meta_are_reduced(self):
        result = guard_text("其实这个平台我还不熟，不过我会问。", channel="comment")
        self.assertNotIn("其实", result.text)
        self.assertIn("平台", result.text)
        self.assertEqual(result.text,"这个平台我还不熟，不过我会问。")

    def test_plain_text_is_untouched(self):
        result = guard_text("今天好热，我先去吃饭了。", channel="dm")
        self.assertEqual(result.text, "今天好热，我先去吃饭了。")
        self.assertFalse(result.repaired)

    def test_ordinary_words_are_not_treated_as_advice(self):
        for text in ("这章我读了。我记得他当时也在。",
                     "今天很热。祝融台风要来了。",
                     "这章我读了。最好的办法是先备份。"):
            self.assertEqual(guard_text(text, channel="dm").text, text, text)

    def test_platform_period_is_not_a_meta_violation(self):
        text = "我最近到了平台期，进步很慢。"
        result = guard_text(text, channel="comment")
        self.assertEqual(result.text, text)
        self.assertFalse(result.repaired)

    def test_guard_parts_collects_rules(self):
        parts, rules, repaired = guard_parts(["我来总结一下：这章不错。", "我先走了。"], channel="comment")
        self.assertTrue(repaired)
        self.assertIn("summary", rules)
        self.assertEqual(parts[1], "我先走了。")

    def test_guard_turn_can_use_one_rewrite(self):
        async def rewrite(_text, _violations):
            return "这章我读了，先走了。"

        result = asyncio.run(guard_turn("我来总结一下：这章不错。", channel="comment", rewrite=rewrite))
        self.assertTrue(result.rewritten)
        self.assertEqual(result.text, "这章我读了，先走了。")


if __name__ == "__main__":
    unittest.main()
