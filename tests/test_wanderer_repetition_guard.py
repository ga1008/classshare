"""Deterministic self-repetition suppression (audit D4)."""
import unittest

from classroom_app.services.wanderer_repetition_guard import (
    filter_repetition_parts,
    opening_repeated,
    repeated_span,
)


class RepetitionGuardTest(unittest.TestCase):
    def test_repeated_span_detects_eight_char_run(self):
        recent = ["这章我读了，读到一半就饿得不行。"]
        self.assertTrue(repeated_span("这章我读了，读到一半就饿得不行。", recent))
        self.assertFalse(repeated_span("今天天气不错。", recent))

    def test_short_texts_are_not_flagged(self):
        self.assertFalse(repeated_span("好吧", ["好吧"]))

    def test_third_same_opening_is_flagged(self):
        recent = ["今天早上我去操场跑步了。", "今天早上我去操场走了一圈。"]
        self.assertTrue(opening_repeated("今天早上我去操场又跑了。", recent))
        self.assertFalse(opening_repeated("今天早上我去操场又跑了。", recent[:1]))

    def test_filter_drops_duplicate_and_keeps_novel(self):
        recent = ["今天早上我去操场跑步了。"]
        parts = ["今天早上我去操场跑步了。", "我先去吃饭了。"]
        kept, dropped = filter_repetition_parts(parts, recent)
        self.assertEqual(kept, ["我先去吃饭了。"])
        self.assertEqual(dropped, 1)

    def test_unrelated_message_is_kept(self):
        kept, dropped = filter_repetition_parts(["我先去吃饭了。"], ["今天好热。"])
        self.assertEqual(kept, ["我先去吃饭了。"])
        self.assertEqual(dropped, 0)

    def test_opening_check_accepts_a_generator(self):
        recent = (item for item in ["今天早上我去操场跑步了。", "今天早上我去操场走了一圈。"])
        self.assertTrue(opening_repeated("今天早上我去操场又跑了。", recent))


if __name__ == "__main__":
    unittest.main()
