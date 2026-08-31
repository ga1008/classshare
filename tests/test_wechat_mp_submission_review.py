"""小程序批阅重构：逐题视图聚合 build_submission_review 的纯函数单测。"""

import json
import unittest

from classroom_app.routers.mp.teacher import (
    _flatten_paper_questions,
    _judge_question,
    _normalize_answer_entries,
    build_submission_review,
    parse_question_feedback,
)


def _paper() -> str:
    return json.dumps(
        {
            "grading": {"total_score": 100},
            "pages": [
                {
                    "questions": [
                        {
                            "id": "q1",
                            "type": "radio",
                            "text": "VLANIF 的作用是？",
                            "options": ["A. 三层网关", "B. 二层隔离"],
                            "answer": "A",
                            "points": 40,
                        },
                        {
                            "id": "q2",
                            "type": "checkbox",
                            "text": "正确的有？",
                            "options": ["A. 甲", "B. 乙", "C. 丙"],
                            "answer": ["A", "B"],
                            "points": 30,
                        },
                        {
                            "id": "q3",
                            "type": "textarea",
                            "text": "简述 **ACL** 顺序原则。",
                            "answer": "先精确后宽泛",
                            "points": 30,
                        },
                    ]
                }
            ],
        },
        ensure_ascii=False,
    )


def _answers(with_attachment: bool = True) -> str:
    q3_attachments = (
        [
            {
                "kind": "image",
                "file_name": "acl.png",
                "relative_path": "acl.png",
                "mime_type": "image/png",
                "question_id": "q3",
            }
        ]
        if with_attachment
        else []
    )
    return json.dumps(
        {
            "answers": [
                {"question_id": "q1", "answer": "A. 三层网关", "attachments": []},
                {"question_id": "q2", "answer": "A", "attachments": []},
                {"question_id": "q3", "answer": "见附件", "attachments": q3_attachments},
            ]
        },
        ensure_ascii=False,
    )


_FILE_ROWS = [
    {
        "id": 11,
        "original_filename": "acl.png",
        "relative_path": "acl.png",
        "mime_type": "image/png",
        "file_size": 1024,
    },
    {
        "id": 12,
        "original_filename": "report.docx",
        "relative_path": "report.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "file_size": 2048,
    },
]


class FlattenPaperQuestionsTests(unittest.TestCase):
    def test_flattens_pages_and_normalizes_fields(self):
        questions = _flatten_paper_questions(_paper())
        self.assertEqual([q["id"] for q in questions], ["q1", "q2", "q3"])
        self.assertEqual(questions[0]["points"], 40)
        self.assertEqual(questions[1]["answer_text"], "A、B")
        self.assertEqual(questions[2]["type"], "textarea")

    def test_bad_json_returns_empty(self):
        self.assertEqual(_flatten_paper_questions(None), [])
        self.assertEqual(_flatten_paper_questions("not json"), [])


class VerdictTests(unittest.TestCase):
    _CHECKBOX_Q = {
        "type": "checkbox",
        "options": ["A. 甲", "B. 乙", "C. 丙"],
        "answer_text": "A、B",
        "points": 10,
    }

    def test_fixed_full_and_zero_carry_earned(self):
        self.assertEqual(
            _judge_question({}, {}, {"fixed_score": 40.0, "max_score": 40, "reason": "exact_radio_match"}),
            ("full", 40.0),
        )
        self.assertEqual(
            _judge_question({}, {}, {"fixed_score": 0.0, "max_score": 40, "reason": "wrong_radio_choice"}),
            ("zero", 0.0),
        )
        self.assertEqual(
            _judge_question({}, {}, {"fixed_score": 0.0, "max_score": 30, "reason": "blank_without_attachment"}),
            ("blank", 0.0),
        )

    def test_checkbox_subset_is_partial_and_wrong_pick_is_zero(self):
        partial = _judge_question(self._CHECKBOX_Q, {"answer": ["A. 甲"]}, None)
        self.assertEqual(partial, ("partial", None))
        wrong = _judge_question(self._CHECKBOX_Q, {"answer": ["A. 甲", "C. 丙"]}, None)
        self.assertEqual(wrong, ("zero", None))

    def test_text_mismatch_is_doubt_and_subjective_is_manual(self):
        self.assertEqual(_judge_question({"type": "text"}, {"answer": "别的"}, None), ("doubt", None))
        self.assertEqual(_judge_question({"type": "textarea"}, {"answer": "论述"}, None), ("manual", None))

    def test_normalize_splits_checkbox_separator(self):
        entries = _normalize_answer_entries([{"question_id": "q2", "answer": "A. 甲|||B. 乙"}])
        self.assertEqual(entries[0]["answer"], ["A. 甲", "B. 乙"])


class ParseQuestionFeedbackTests(unittest.TestCase):
    _FEEDBACK = (
        "## 总览评语\n"
        "完成质量良好。\n\n"
        "### 第 1 题\n"
        "- 本题得分：5/5\n"
        "- 扣分点：无\n"
        "- 评价：准确\n\n"
        "### 第 3 题\n"
        "- **本题得分**：14/16\n"
        "- 扣分点：NAT outbound接口书写与实际配置不符\n"
        "- 评价：后续注意核对配置细节\n"
    )

    def test_parses_sections_with_scores(self):
        parsed = parse_question_feedback(self._FEEDBACK)
        self.assertEqual(set(parsed.keys()), {1, 3})
        self.assertEqual(parsed[1]["score"], 5.0)
        self.assertEqual(parsed[1]["max_score"], 5.0)
        self.assertEqual(parsed[3]["score"], 14.0)
        self.assertEqual(parsed[3]["max_score"], 16.0)
        self.assertIn("NAT outbound", parsed[3]["deduction"])
        self.assertEqual(parsed[3]["evaluation"], "后续注意核对配置细节")

    def test_unstructured_feedback_returns_empty(self):
        self.assertEqual(parse_question_feedback("整体不错，继续努力。"), {})
        self.assertEqual(parse_question_feedback(None), {})


class BuildSubmissionReviewTests(unittest.TestCase):
    def test_exam_review_verdicts_and_attachment_attribution(self):
        review = build_submission_review(_paper(), _answers(), _FILE_ROWS)
        questions = review["questions"]
        self.assertEqual(len(questions), 3)

        q1, q2, q3 = questions
        # 单选答对 → 满分 + "40/40" 得分展示；标准答案对教师可见
        self.assertEqual(q1["verdict"], "full")
        self.assertEqual(q1["score_display"], "40/40")
        self.assertEqual(q1["standard_answer"], "A")
        # 多选漏选（只选 A）→ 部分正确，得分未定
        self.assertEqual(q2["verdict"], "partial")
        self.assertEqual(q2["score_display"], "—/30")
        # 问答题 → 人工评判
        self.assertEqual(q3["verdict"], "manual")
        self.assertEqual(q3["score_display"], "—/30")

        # q3 的图片附件按 answers_json 内嵌清单归到本题
        self.assertEqual([f["id"] for f in q3["attachments"]], [11])
        self.assertTrue(q3["attachments"][0]["is_image"])
        # 匹配不上的附件进整卷兜底，绝不丢
        self.assertEqual([f["id"] for f in review["paper_files"]], [12])
        self.assertEqual(review["total_points"], 100)

    def test_checkbox_pipe_joined_full_answer_scores_full(self):
        """线上 bug 回归：多选完整选项文本用 ||| 连接必须判满分。"""
        answers = json.dumps(
            {
                "answers": [
                    {"question_id": "q1", "answer": "A. 三层网关", "attachments": []},
                    {"question_id": "q2", "answer": "A. 甲|||B. 乙", "attachments": []},
                    {"question_id": "q3", "answer": "见附件", "attachments": []},
                ]
            },
            ensure_ascii=False,
        )
        review = build_submission_review(_paper(), answers, [])
        q2 = review["questions"][1]
        self.assertEqual(q2["verdict"], "full")
        self.assertEqual(q2["score_display"], "30/30")
        # 学生答案展示为顿号连接，不能出现 |||
        self.assertNotIn("|||", q2["student_answer"])

    def test_graded_feedback_scores_override_objective_prediction(self):
        """已批改时逐题评语里的实际得分优先：主观题也要有 14/16 式展示。"""
        feedback = (
            "## 总览评语\n好。\n\n"
            "### 第 2 题\n- 本题得分：18/30\n- 扣分点：漏选\n\n"
            "### 第 3 题\n- 本题得分：30/30\n- 扣分点：无\n- 评价：完整\n"
        )
        review = build_submission_review(_paper(), _answers(), _FILE_ROWS, feedback_md=feedback)
        q2, q3 = review["questions"][1], review["questions"][2]
        self.assertEqual(q2["verdict"], "partial")
        self.assertEqual(q2["score_display"], "18/30")
        self.assertEqual(q2["deduction"], "漏选")
        # 主观题拿到实际满分 → full + 30/30 + 评价透出
        self.assertEqual(q3["verdict"], "full")
        self.assertEqual(q3["score_display"], "30/30")
        self.assertEqual(q3["evaluation"], "完整")

    def test_plain_assignment_without_paper(self):
        answers = json.dumps({"answers": [{"question": "作答", "answer": "我的回答"}]})
        review = build_submission_review(None, answers, _FILE_ROWS)
        self.assertEqual(len(review["questions"]), 1)
        entry = review["questions"][0]
        self.assertEqual(entry["verdict"], "manual")
        self.assertEqual(entry["student_answer"], "我的回答")
        # 普通作业附件无按题归属 → 全部进整卷兜底
        self.assertEqual(len(review["paper_files"]), 2)

    def test_blank_answer_marks_blank_verdict(self):
        answers = json.dumps(
            {
                "answers": [
                    {"question_id": "q1", "answer": "", "attachments": []},
                    {"question_id": "q2", "answer": "", "attachments": []},
                    {"question_id": "q3", "answer": "", "attachments": []},
                ]
            }
        )
        review = build_submission_review(_paper(), answers, [])
        self.assertEqual([q["verdict"] for q in review["questions"]], ["blank", "blank", "blank"])

    def test_corrupt_answers_json_degrades_gracefully(self):
        review = build_submission_review(_paper(), "{{{", _FILE_ROWS)
        self.assertEqual(len(review["questions"]), 3)
        # 无作答信息 → 客观题无法判定，退化为人工评判且附件全兜底
        self.assertEqual(len(review["paper_files"]), 2)


if __name__ == "__main__":
    unittest.main()
