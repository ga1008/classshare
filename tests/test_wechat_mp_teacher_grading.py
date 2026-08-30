"""小程序教师批阅聚合端点的纯函数单测（markdown 评语块 / 作答解析）。"""

import unittest

from classroom_app.routers.mp.teacher import _parse_answers, _parse_feedback_blocks


class ParseFeedbackBlocksTests(unittest.TestCase):
    def test_empty_and_none_return_empty_list(self):
        self.assertEqual(_parse_feedback_blocks(None), [])
        self.assertEqual(_parse_feedback_blocks(""), [])

    def test_ai_grading_markdown_parses_into_typed_blocks(self):
        md = (
            "## 总览评语\n"
            "完成质量优秀，**证据链完整**。\n\n"
            "### 第 1 题\n"
            "- 本题得分：5/5\n"
            "- 扣分点：无\n\n"
            "<!-- group-final -->\n"
            "**综合表现分：100.0**\n"
        )
        blocks = _parse_feedback_blocks(md)
        types = [b["type"] for b in blocks]
        self.assertEqual(types, ["h2", "p", "h3", "li", "li", "strong"])
        self.assertEqual(blocks[0]["text"], "总览评语")
        # 行内加粗标记被剥掉、内容保留
        self.assertEqual(blocks[1]["text"], "完成质量优秀，证据链完整。")
        # HTML 注释整行剔除
        self.assertEqual(blocks[-1]["text"], "综合表现分：100.0")

    def test_horizontal_rules_are_dropped(self):
        self.assertEqual(_parse_feedback_blocks("---\n***\n___"), [])

    def test_deep_headings_clamp_to_h3(self):
        blocks = _parse_feedback_blocks("#### 细目")
        self.assertEqual(blocks[0]["type"], "h3")


class ParseAnswersTests(unittest.TestCase):
    def test_invalid_json_returns_empty(self):
        self.assertEqual(_parse_answers(None), [])
        self.assertEqual(_parse_answers("not json"), [])
        self.assertEqual(_parse_answers('{"answers": "oops"}'), [])

    def test_web_exam_take_shape_roundtrips(self):
        payload = '{"answers": [{"question": "Q1", "answer": "A"}, {"question": "Q2"}]}'
        self.assertEqual(
            _parse_answers(payload),
            [
                {"question": "Q1", "answer": "A"},
                {"question": "Q2", "answer": ""},
            ],
        )


if __name__ == "__main__":
    unittest.main()
