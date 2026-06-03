import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from classroom_app.services.wrong_question_summary_service import (
    _attach_text_answer_clusters,
    _build_ai_status,
    _build_question_error_stats,
    _build_score_based_hard_questions,
    _extract_exam_questions,
    _score_based_difficulty_summary,
)


def _feedback(*scores: tuple[int, float, float]) -> str:
    sections = ["## 逐题反馈"]
    for question_no, score, max_score in scores:
        sections.extend(
            [
                "",
                f"### 第{question_no}题",
                f"- 本题得分：{score}/{max_score}",
                "- 扣分点：测试扣分点",
                "- 评价：测试评价",
            ]
        )
    return "\n".join(sections)


class WrongQuestionSummaryServiceTests(unittest.TestCase):
    def test_counts_only_non_full_scores_and_builds_choice_option_bars(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "How many layers are in the OSI model?",
                                "options": ["A. 5 layers", "B. 7 layers", "C. 4 layers"],
                                "answer": "B",
                                "points": 1,
                            },
                            {
                                "id": "q2",
                                "type": "checkbox",
                                "text": "Which are transport-layer protocols?",
                                "options": ["A. TCP", "B. IP", "C. UDP"],
                                "answer": ["A", "C"],
                                "points": 2,
                            },
                            {
                                "id": "q3",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            },
                            {
                                "id": "q4",
                                "type": "textarea",
                                "text": "Explain what ARP resolves.",
                                "answer": "ARP resolves an IP address to a MAC address on the local network.",
                                "points": 5,
                            },
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "A. 5 layers"},
                            {"question_id": "q2", "answer": "A. TCP|||C. UDP"},
                            {"question_id": "q3", "answer": "8080"},
                            {"question_id": "q4", "answer": "ARP gets MAC addresses."},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 1, 1), (2, 2, 2), (3, 1, 1), (4, 5, 5)),
            },
            {
                "id": 2,
                "student_name": "Student B",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "A. 5 layers"},
                            {"question_id": "q2", "answer": "A. TCP"},
                            {"question_id": "q3", "answer": "8080"},
                            {"question_id": "q4", "answer": "ARP broadcasts to find an IP address."},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1), (2, 1, 2), (3, 0, 1), (4, 3, 5)),
            },
            {
                "id": 3,
                "student_name": "Student C",
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "C. 4 layers"},
                            {"question_id": "q2", "answer": "B. IP"},
                            {"question_id": "q3", "answer": "9999"},
                            {"question_id": "q4", "answer": "IP to MAC."},
                        ]
                    },
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1), (2, 2, 2), (3, 1, 1), (4, 5, 5)),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        by_id = {item["question"]["id"]: item for item in stats}

        self.assertEqual(by_id["q1"]["wrong_count"], 2)
        self.assertEqual(by_id["q1"]["correct_count"], 1)
        self.assertEqual(by_id["q1"]["option_total_count"], 3)
        q1_bars = {item["label"]: item for item in by_id["q1"]["option_bars"]}
        self.assertEqual(q1_bars["A. 5 layers"]["count"], 2)
        self.assertEqual(q1_bars["A. 5 layers"]["percent"], 67)
        self.assertEqual(q1_bars["A. 5 layers"]["tone"], "wrong")
        self.assertEqual(q1_bars["B. 7 layers"]["count"], 0)
        self.assertEqual(q1_bars["B. 7 layers"]["percent"], 0)
        self.assertEqual(q1_bars["B. 7 layers"]["tone"], "correct")
        self.assertEqual(q1_bars["C. 4 layers"]["count"], 1)
        self.assertEqual(q1_bars["C. 4 layers"]["percent"], 33)

        self.assertEqual(by_id["q2"]["wrong_count"], 1)
        q2_bars = {item["label"]: item for item in by_id["q2"]["option_bars"]}
        self.assertEqual(q2_bars["A. TCP"]["count"], 2)
        self.assertEqual(q2_bars["A. TCP"]["percent"], 67)
        self.assertEqual(q2_bars["A. TCP"]["tone"], "correct")
        self.assertEqual(q2_bars["B. IP"]["count"], 1)
        self.assertEqual(q2_bars["B. IP"]["percent"], 33)
        self.assertEqual(q2_bars["B. IP"]["tone"], "wrong")
        self.assertEqual(q2_bars["C. UDP"]["count"], 1)
        self.assertEqual(q2_bars["C. UDP"]["percent"], 33)
        self.assertEqual(q2_bars["C. UDP"]["tone"], "correct")

        self.assertEqual(by_id["q3"]["wrong_count"], 1)
        self.assertEqual(by_id["q3"]["top_wrong_answers"][0]["label"], "8080")
        self.assertEqual(by_id["q3"]["top_wrong_answers"][0]["details"][0]["student_name"], "Student B")
        self.assertEqual(by_id["q4"]["wrong_count"], 1)

        hard_questions = _build_score_based_hard_questions(stats)
        self.assertEqual(hard_questions[0]["question"]["id"], "q1")
        self.assertIn("未满分率", hard_questions[0]["difficulty_reason"])

    def test_missing_question_score_is_not_counted_as_wrong(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps({"answers": [{"question_id": "q1", "answer": "8080"}]}),
                "feedback_md": "",
            }
        ]

        stats = _build_question_error_stats(questions, submissions)
        self.assertEqual(stats[0]["wrong_count"], 0)
        self.assertEqual(stats[0]["scored_count"], 0)

    def test_cached_mode_marks_subjective_ai_work_pending_without_sync_generation(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "Basics",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "text",
                                "text": "Default HTTP port?",
                                "answer": "80",
                                "points": 1,
                            }
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "student_name": "Student A",
                "status": "submitted",
                "answers_json": json.dumps(
                    {"answers": [{"question_id": "q1", "answer": "8080"}]},
                    ensure_ascii=False,
                ),
                "feedback_md": _feedback((1, 0, 1)),
            }
        ]
        stats = _build_question_error_stats(questions, submissions)

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_text_cluster_cache",
            return_value=None,
        ), patch(
            "classroom_app.services.wrong_question_summary_service._generate_text_wrong_clusters",
            new=AsyncMock(),
        ) as generate_text:
            asyncio.run(_attach_text_answer_clusters("assignment-1", stats, allow_generate=False))

        self.assertEqual(stats[0]["text_cluster_status"], "pending")
        generate_text.assert_not_awaited()

        with patch(
            "classroom_app.services.wrong_question_summary_service._load_wrong_summary_job",
            return_value=None,
        ):
            ai_status = _build_ai_status(
                {
                    "assignment": {"id": "assignment-1"},
                    "questions_signature": "signature-1",
                },
                stats,
                _score_based_difficulty_summary(_build_score_based_hard_questions(stats)),
            )

        self.assertTrue(ai_status["needs_ai"])
        self.assertTrue(ai_status["is_active"])
        self.assertEqual(ai_status["job_status"], "queued")
        self.assertEqual(ai_status["pending_difficulty"], 0)


if __name__ == "__main__":
    unittest.main()
