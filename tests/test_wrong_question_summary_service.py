import json
import unittest

from classroom_app.services.wrong_question_summary_service import (
    _build_question_error_stats,
    _extract_exam_questions,
)


class WrongQuestionSummaryServiceTests(unittest.TestCase):
    def test_counts_choice_and_text_wrong_answers(self):
        questions = _extract_exam_questions(
            {
                "pages": [
                    {
                        "name": "基础题",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "radio",
                                "text": "OSI 模型有几层？",
                                "options": ["A. 5 层", "B. 7 层", "C. 4 层"],
                                "answer": "B",
                            },
                            {
                                "id": "q2",
                                "type": "checkbox",
                                "text": "以下哪些是传输层协议？",
                                "options": ["A. TCP", "B. IP", "C. UDP"],
                                "answer": ["A", "C"],
                            },
                            {
                                "id": "q3",
                                "type": "text",
                                "text": "HTTP 默认端口是？",
                                "answer": "80",
                            },
                        ],
                    }
                ]
            }
        )
        submissions = [
            {
                "id": 1,
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "B. 7 层"},
                            {"question_id": "q2", "answer": "A. TCP|||C. UDP"},
                            {"question_id": "q3", "answer": "80"},
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "id": 2,
                "status": "submitted",
                "answers_json": json.dumps(
                    {
                        "answers": [
                            {"question_id": "q1", "answer": "A. 5 层"},
                            {"question_id": "q2", "answer": "A. TCP"},
                            {"question_id": "q3", "answer": "8080"},
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        stats = _build_question_error_stats(questions, submissions)
        by_id = {item["question"]["id"]: item for item in stats}

        self.assertEqual(by_id["q1"]["wrong_count"], 1)
        self.assertEqual(by_id["q1"]["top_wrong_answers"][0]["label"], "A. 5 层")
        self.assertEqual(by_id["q1"]["question"]["answer_text"], "B. 7 层")
        self.assertEqual(by_id["q2"]["wrong_count"], 1)
        self.assertEqual(by_id["q2"]["top_wrong_answers"][0]["label"], "A. TCP")
        self.assertEqual(by_id["q3"]["wrong_count"], 1)
        self.assertEqual(by_id["q3"]["top_wrong_answers"][0]["label"], "8080")


if __name__ == "__main__":
    unittest.main()
