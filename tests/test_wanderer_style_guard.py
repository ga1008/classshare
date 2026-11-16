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

    def test_fixed_opening_is_removed_but_inline_word_is_kept(self):
        result = guard_text("家人们！在在在～一大早就在肝作业也太猛了吧🥳", channel="dm")
        self.assertNotIn("家人们", result.text)
        self.assertTrue(result.text.startswith("在在在"))
        self.assertTrue(result.repaired)
        # The same word mid-sentence is not an opening and must survive.
        inline = guard_text("他喊了句家人们就跑了。", channel="dm")
        self.assertEqual(inline.text, "他喊了句家人们就跑了。")
        self.assertFalse(inline.repaired)

    def test_exclamation_marks_are_collapsed_and_capped(self):
        result = guard_text("太离谱了！！！我真的会谢！！这也！太！强！", channel="dm")
        self.assertNotIn("！！", result.text)
        self.assertLessEqual(result.text.count("！"), 3)
        self.assertIn("太离谱了", result.text)

    def test_opening_words_in_normal_subjects_are_kept(self):
        for text in ("家人们的意见很重要。", "宝子细胞正在分裂。", "老铁匠正在开门。"):
            self.assertEqual(guard_text(text, channel="dm").text, text)

    def test_opening_only_parts_become_silence_and_other_parts_survive(self):
        self.assertEqual(guard_text("家人们！", channel="dm").text, "")
        parts, rules, repaired = guard_parts(["家人们！！！", "普通内容。", "🥳"], channel="dm")
        self.assertEqual(parts, ["普通内容。", "🥳"])
        self.assertIn("opening", rules)
        self.assertTrue(repaired)
        self.assertEqual(guard_parts(["老铁"], channel="dm")[0], [])

    def test_one_exclamation_run_is_normalized_without_other_violations(self):
        self.assertEqual(guard_text("太好了！！！", channel="dm").text, "太好了！")
        self.assertEqual(guard_text("真的!!", channel="dm").text, "真的！")

    def test_natural_only_preserves_requested_summary_and_advice(self):
        text = "我来总结一下：家人们的意见很重要。建议你先看原文。🥳"
        self.assertEqual(guard_text(text, channel="dm", natural_only=True).text, text)

    def test_natural_only_preserves_code_and_meaningful_spacing(self):
        for text in ('说明：\n```python\n    if ready:\n        print("ok!!")\n```\n太好了！！！',
                     '使用 `print("ok!!")`，太好了！！！', '    print("ok!!")\n太好了！！！',
                     '        print("ok!!")', '\t\tprint("ok!!")'):
            self.assertEqual(guard_text(text, channel="dm", natural_only=True).text, text)
            self.assertEqual(guard_text(text, channel="group").text, text)
        text = "第一列    第二列！！！"
        self.assertEqual(guard_text(text, channel="dm", natural_only=True).text, "第一列    第二列！")

    def test_code_cannot_be_changed_by_direct_repair_or_rewrite(self):
        code = '代码：\n    if ready:\n        print("ok!!")\n太好了！！！'
        self.assertEqual(lint(code, channel="group"), [])
        self.assertEqual(repair(code, lint("甲！！乙！！", channel="group")), code)
        async def unexpected_rewrite(*args):
            raise AssertionError("code must not be sent for style rewriting")
        self.assertEqual(asyncio.run(guard_turn(code, channel="group", rewrite=unexpected_rewrite)).text, code)

    def test_emoji_density_is_reduced_but_pure_emoji_reply_survives(self):
        result = guard_text("今天好开心啊🥳✨💖🎉一起去吃饭吧", channel="dm")
        self.assertEqual(result.text.count("🥳"), 1)
        self.assertNotIn("✨", result.text)
        pure = guard_text("🥳🥳🥳🥳", channel="dm")
        self.assertEqual(pure.text, "🥳🥳🥳🥳")
        self.assertFalse(pure.repaired)

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
