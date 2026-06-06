import unittest

from classroom_app.services.learning_progress_service import _ensure_stage_exam_scoring_payload


class LearningProgressStageExamTests(unittest.TestCase):
    def test_stage_exam_scoring_payload_fills_missing_grading_fields(self):
        payload = {
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {
                            "id": "p1_q1",
                            "type": "radio",
                            "text": "Choose one",
                            "options": ["A", "B"],
                            "answer": "A",
                            "explanation": "A is correct.",
                        },
                        {
                            "id": "p1_q2",
                            "type": "textarea",
                            "text": "Explain the process",
                            "answer": "",
                            "explanation": "Reference explanation.",
                        },
                    ],
                }
            ],
        }

        normalized = _ensure_stage_exam_scoring_payload(payload)
        questions = normalized["pages"][0]["questions"]

        self.assertEqual(100, normalized["grading"]["total_score"])
        self.assertEqual(50, questions[0]["points"])
        self.assertEqual(50, questions[1]["points"])
        for question in questions:
            self.assertTrue(question["answer"])
            self.assertTrue(question["grading_guidance"])
            self.assertTrue(question["deduction_points"])
            self.assertEqual(question["points"], question["grading"]["points"])

    def test_stage_exam_scoring_payload_rescales_existing_points_to_100(self):
        payload = {
            "grading": {"total_score": 20, "description": "Existing", "style": "strict"},
            "pages": [
                {
                    "name": "Part 1",
                    "questions": [
                        {
                            "id": "q1",
                            "type": "text",
                            "text": "Q1",
                            "answer": "A1",
                            "points": 5,
                            "grading_guidance": "Guide",
                            "deduction_points": "Deduct",
                        },
                        {
                            "id": "q2",
                            "type": "text",
                            "text": "Q2",
                            "answer": "A2",
                            "points": 15,
                            "grading_guidance": "Guide",
                            "deduction_points": "Deduct",
                        },
                    ],
                }
            ],
        }

        normalized = _ensure_stage_exam_scoring_payload(payload)
        questions = normalized["pages"][0]["questions"]

        self.assertEqual(100, normalized["grading"]["total_score"])
        self.assertEqual(25, questions[0]["points"])
        self.assertEqual(75, questions[1]["points"])


if __name__ == "__main__":
    unittest.main()
