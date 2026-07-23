import unittest

from classroom_app.services.career_path_service import get_questions
from classroom_app.services.career_seed_data import score_personality_answers


class CareerPathQuizTests(unittest.TestCase):
    def test_quick_mode_is_seven_questions_and_keeps_location(self):
        questions = get_questions(mode="quick", major_key="软件工程")
        self.assertEqual(len(questions), 7)
        self.assertIn("q_loc", [question["id"] for question in questions])
        self.assertIn("q8", [question["id"] for question in questions])

    def test_non_technology_major_gets_general_focus_question(self):
        questions = get_questions(mode="quick", major_key="英语")
        ids = [question["id"] for question in questions]
        self.assertIn("q_focus", ids)
        self.assertNotIn("q8", ids)
        focus = next(question for question in questions if question["id"] == "q_focus")
        labels = [option["label"] for option in focus["options"]]
        self.assertIn("语言 / 跨文化 / 国际业务", labels)

    def test_full_mode_preserves_deeper_questions(self):
        questions = get_questions(mode="full", major_key="工商管理")
        ids = [question["id"] for question in questions]
        self.assertEqual(len(questions), 11)
        self.assertIn("q4", ids)
        self.assertIn("q9", ids)
        self.assertIn("q10", ids)
        self.assertIn("q_focus", ids)

    def test_general_focus_answers_use_existing_riasec_scoring(self):
        result = score_personality_answers([
            {"question_id": "q_focus", "value": ["language_global", "education_service"]},
            {"question_id": "q_loc", "value": "flexible"},
        ])
        self.assertEqual(result["location_pref"], "flexible")
        self.assertIn("S", result["holland_code"])

    def test_public_questions_do_not_expose_scoring_weights(self):
        questions = get_questions(mode="quick", major_key="英语")
        self.assertTrue(all("low_weights" not in question and "high_weights" not in question for question in questions))
        self.assertTrue(all("weights" not in option for question in questions for option in question.get("options", [])))


if __name__ == "__main__":
    unittest.main()
