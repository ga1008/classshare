import unittest

from ai_assistant import _robust_parse_json_value


class RobustJsonParsingTests(unittest.TestCase):
    def test_parses_plain_object(self):
        self.assertEqual(_robust_parse_json_value('{"a": 1}'), {"a": 1})

    def test_strips_closed_reasoning_block(self):
        raw = "<think>let me reason about the answer</think>{\"a\": 1}"
        self.assertEqual(_robust_parse_json_value(raw), {"a": 1})

    def test_extracts_fenced_json_with_leading_prose(self):
        raw = "Here is the result:\n```json\n{\"a\": 2, \"b\": [1, 2]}\n```"
        self.assertEqual(_robust_parse_json_value(raw), {"a": 2, "b": [1, 2]})

    def test_repairs_trailing_commas(self):
        self.assertEqual(_robust_parse_json_value('{"a": 1, "b": [1, 2,],}'), {"a": 1, "b": [1, 2]})

    def test_repairs_single_quotes(self):
        self.assertEqual(_robust_parse_json_value("{'a': 4}"), {"a": 4})

    def test_does_not_drop_json_after_unclosed_reasoning_tag(self):
        # 未闭合的推理标签不应吞掉其后真正的 JSON
        raw = "<think>reasoning continues {\"a\": 5}"
        self.assertEqual(_robust_parse_json_value(raw), {"a": 5})

    def test_raises_on_unparseable(self):
        with self.assertRaises(ValueError):
            _robust_parse_json_value("no json here at all")


if __name__ == "__main__":
    unittest.main()
