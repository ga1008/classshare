import unittest

from classroom_app.services.lesson_plan_generation_service import (
    _json_from_ai_chat_payload,
    _loads_ai_json,
    normalize_generation_session_plan,
)


class LessonPlanGenerationJsonTests(unittest.TestCase):
    def test_json_payload_reads_gateway_response_json(self):
        payload = {
            "response_json": {
                "cover": {"course_name": "Dynamic Web Programming"},
                "sessions": [{"chapter": "Intro"}],
            },
            "response_text": "not json",
        }

        parsed = _json_from_ai_chat_payload(payload)

        self.assertEqual(parsed["cover"]["course_name"], "Dynamic Web Programming")
        self.assertEqual(parsed["sessions"][0]["chapter"], "Intro")

    def test_loads_ai_json_extracts_prefaced_json_array(self):
        parsed = _loads_ai_json('Here is the result:\n[{"chapter": "A"}, {"chapter": "B"}]')

        self.assertEqual(parsed, {"sessions": [{"chapter": "A"}, {"chapter": "B"}]})

    def test_generation_session_plan_preserves_manual_count_and_source_materials(self):
        raw = [
            {
                "client_id": "one",
                "source_session_id": 8,
                "chapter": "Vue Components",
                "schedule_text": "week 2 sections 1-2",
                "section_minutes": "80",
                "source_material_ids": ["11", "12"],
                "material_summary": "component slides",
            },
            {
                "client_id": "manual-two",
                "source_type": "manual",
                "chapter": "State Management",
                "manual_outline": "Inserted between component and routing lessons",
                "prompt_hint": "Use the previous and next lesson context.",
            },
        ]

        sessions = normalize_generation_session_plan(raw)

        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["source_session_id"], 8)
        self.assertEqual(sessions[0]["source_material_ids"], ["11", "12"])
        self.assertEqual(sessions[0]["schedule"]["text"], "week 2 sections 1-2")
        self.assertEqual(sessions[1]["source_type"], "manual")
        self.assertEqual(sessions[1]["source_session_id"], 0)
        self.assertTrue(sessions[1]["manual_outline"].startswith("Inserted"))


if __name__ == "__main__":
    unittest.main()
