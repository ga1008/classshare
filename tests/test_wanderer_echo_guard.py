"""Deterministic group-echo suppression (audit D1)."""
import unittest

from classroom_app.services.wanderer_echo_guard import (
    echo_similarity,
    filter_echo_parts,
    is_confirmation_echo,
)


class EchoSimilarityTest(unittest.TestCase):
    def test_paraphrased_suggestions_are_similar(self):
        left = "别在真页面上试，本地起个环境随便折腾。"
        right = "别在别人页面上试，自己本地搭个环境最稳。"
        self.assertGreaterEqual(echo_similarity(left, right), 0.62)

    def test_unrelated_text_is_not_similar(self):
        left = "今天考试好难，我好多题都不会。"
        right = "这章我读了，读到一半就饿得不行。"
        self.assertLess(echo_similarity(left, right), 0.3)

    def test_short_texts_are_not_compared(self):
        self.assertEqual(echo_similarity("笑死", "笑死我了"), 0.0)


class ConfirmationEchoTest(unittest.TestCase):
    def test_pure_confirmations_are_detected(self):
        for text in ("苏青禾说得挺到位", "唐一诺说得在理", "我认", "附议", "同意", "+1"):
            self.assertTrue(is_confirmation_echo(text), text)

    def test_disagreement_and_added_content_are_kept(self):
        for text in ("我不同意", "你说得对，但我觉得...", "苏青禾说得挺到位，不过...",
                     "我支持他，因为他先把账算清了"):
            self.assertFalse(is_confirmation_echo(text), text)

    def test_opinions_that_start_with_agreement_words_are_kept(self):
        for text in ("我认为这个方案不错", "我认识他", "支持本地部署",
                     "同意这个做法太冒险", "不太同意", "我支持你"):
            self.assertFalse(is_confirmation_echo(text), text)

    def test_pure_confirmation_needs_room_context(self):
        kept, dropped = filter_echo_parts(["同意"], [])
        self.assertEqual(kept, ["同意"])
        self.assertEqual(dropped, 0)


class FilterEchoPartsTest(unittest.TestCase):
    def test_duplicate_and_confirmation_are_dropped(self):
        recent = ["别在真页面上试，本地起个环境随便折腾。", "苏青禾说得挺到位。"]
        parts = ["别在别人页面上试，自己本地搭个环境最稳。", "苏青禾说得挺到位。",
                 "我不同意，真出事了谁负责？"]
        kept, dropped = filter_echo_parts(parts, recent)
        self.assertEqual(kept, ["我不同意，真出事了谁负责？"])
        self.assertEqual(dropped, 2)

    def test_novel_message_is_kept(self):
        kept, dropped = filter_echo_parts(["我先去吃饭了"], ["今天好热"])
        self.assertEqual(kept, ["我先去吃饭了"])
        self.assertEqual(dropped, 0)


if __name__ == "__main__":
    unittest.main()
