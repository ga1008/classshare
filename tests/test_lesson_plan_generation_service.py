import unittest

import httpx

from classroom_app.services import lesson_plan_generation_service as svc
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


class LessonPlanGenerationResilienceTests(unittest.IsolatedAsyncioTestCase):
    def test_exception_description_keeps_type_when_message_is_empty(self):
        label = svc._describe_exception(TimeoutError())

        self.assertEqual(label, "TimeoutError: operation timed out")

    async def test_chat_json_retries_timeout_with_standard_model(self):
        class FakeAIClient:
            def __init__(self):
                self.calls = []

            async def post(self, path, *, json, timeout):  # noqa: A002 - mirrors httpx keyword.
                self.calls.append({"path": path, "json": json, "timeout": timeout})
                if len(self.calls) == 1:
                    raise httpx.ReadTimeout("thinking model was slow")
                request = httpx.Request("POST", "http://testserver/api/ai/chat")
                return httpx.Response(
                    200,
                    request=request,
                    json={"response_json": {"objectives": "ok", "process": "ready"}},
                )

        fake = FakeAIClient()
        original = svc.ai_client
        svc.ai_client = fake
        try:
            parsed = await svc._chat_json(
                system_prompt="Return JSON",
                user_message="Generate one session",
                file_texts=[{"name": "long.md", "content": "x" * 20000}],
                label="lesson-plan:test-timeout",
                schema_hint={"objectives": "", "process": ""},
            )
        finally:
            svc.ai_client = original

        self.assertEqual(parsed["objectives"], "ok")
        self.assertEqual(len(fake.calls), 2)
        retry_payload = fake.calls[1]["json"]
        self.assertEqual(retry_payload["model_capability"], "standard")
        self.assertEqual(retry_payload["task_type"], "fast_text_response")
        self.assertLessEqual(len(retry_payload["file_texts"][0]["content"]), svc._RETRY_MATERIAL_BUDGET)

    def test_fallback_session_is_complete_enough_to_save(self):
        session = svc._fallback_session_from_context(
            cover={"course_name": "Dynamic Web Programming"},
            meta={
                "schedule": {"text": "week 8 sections 10-11"},
                "section_minutes": 80,
                "material_summary": "Spring MVC controller and layered service practice",
                "source_material_ids": ["12"],
            },
            chapter="Spring MVC Controller",
            index=8,
            total=16,
            material_text="Controller routes, request validation, service layer calls",
            homework_hint="Finish the controller exercise.",
            neighbor="Previous: REST basics; Next: persistence layer",
            ai_filled=False,
            error=httpx.ReadTimeout("slow"),
        )

        self.assertEqual(session["index"], 8)
        self.assertEqual(session["chapter"], "Spring MVC Controller")
        self.assertTrue(session["ai_fallback"])
        self.assertIn("知识目标", session["objectives"])
        self.assertIn("| 教学环节 |", session["process"])
        self.assertIn("Pro 任务", session["process"])
        self.assertEqual(session["source_material_ids"], ["12"])

    async def test_generation_job_keeps_going_when_one_session_times_out(self):
        statuses = []
        saved = []

        originals = {
            "_set_status": svc._set_status,
            "_save_progress": svc._save_progress,
            "_build_cover": svc._build_cover,
            "_build_classroom_context": svc._build_classroom_context,
            "read_generation_sessions": svc.read_generation_sessions,
            "_offering_homework_hint": svc._offering_homework_hint,
            "_gather_session_material_text": svc._gather_session_material_text,
            "_generate_one_session": svc._generate_one_session,
        }

        def fake_set_status(plan_id, **kwargs):
            statuses.append({"plan_id": plan_id, **kwargs})

        def fake_save_progress(plan_id, cover, sessions, progress):
            saved.append(
                {
                    "plan_id": plan_id,
                    "cover": cover,
                    "sessions": [dict(item) for item in sessions],
                    "progress": dict(progress),
                }
            )

        async def fake_generate_one_session(**kwargs):
            if kwargs["index"] == 1:
                raise httpx.ReadTimeout("slow")
            return {
                "index": kwargs["index"],
                "schedule": kwargs["meta"].get("schedule"),
                "chapter": kwargs["chapter"],
                "objectives": "normal",
                "key_points": "normal",
                "difficulties": "normal",
                "methods": "normal",
                "means": "normal",
                "process": "normal",
                "side_notes": "",
                "post_notes": "",
                "source_material_ids": [],
                "ai_filled": False,
            }

        try:
            svc._set_status = fake_set_status
            svc._save_progress = fake_save_progress
            svc._build_cover = lambda class_offering_id, teacher_id: {"course_name": "Dynamic Web"}
            svc._build_classroom_context = lambda class_offering_id: {}
            svc.read_generation_sessions = lambda class_offering_id, teacher_id: [
                {"title": "Session A", "section_minutes": 80, "schedule": {}, "source_material_ids": []},
                {"title": "Session B", "section_minutes": 80, "schedule": {}, "source_material_ids": []},
            ]
            svc._offering_homework_hint = lambda class_offering_id: {0: "homework"}
            svc._gather_session_material_text = lambda class_offering_id, meta, teacher_id: "material"
            svc._generate_one_session = fake_generate_one_session

            await svc.run_generation_job("plan-1", 10, 20)
        finally:
            for name, value in originals.items():
                setattr(svc, name, value)

        self.assertEqual(len(saved[-1]["sessions"]), 2)
        self.assertTrue(saved[-1]["sessions"][0]["ai_fallback"])
        self.assertEqual(saved[-1]["sessions"][1]["chapter"], "Session B")
        self.assertEqual(statuses[-1]["status"], "ready")
        self.assertEqual(statuses[-1]["ai_gen_status"], "completed_with_fallback")
        self.assertIn("第 1 次课", statuses[-1]["ai_gen_error"])
        self.assertIn("ReadTimeout", statuses[-1]["ai_gen_error"])

    async def test_generation_job_records_exception_class_when_setup_error_message_is_empty(self):
        statuses = []
        originals = {
            "_set_status": svc._set_status,
            "_build_cover": svc._build_cover,
        }
        original_print_exc = svc.traceback.print_exc

        def fake_set_status(plan_id, **kwargs):
            statuses.append({"plan_id": plan_id, **kwargs})

        try:
            svc._set_status = fake_set_status
            svc.traceback.print_exc = lambda: None

            def raise_empty_timeout(class_offering_id, teacher_id):
                raise TimeoutError()

            svc._build_cover = raise_empty_timeout

            await svc.run_generation_job("plan-empty-error", 10, 20)
        finally:
            for name, value in originals.items():
                setattr(svc, name, value)
            svc.traceback.print_exc = original_print_exc

        self.assertEqual(statuses[-1]["status"], "failed")
        self.assertEqual(statuses[-1]["ai_gen_status"], "failed")
        self.assertIn("生成失败：TimeoutError: operation timed out", statuses[-1]["ai_gen_error"])

    async def test_generation_job_uses_minimal_card_when_structured_fallback_fails(self):
        statuses = []
        saved = []

        originals = {
            "_set_status": svc._set_status,
            "_save_progress": svc._save_progress,
            "_build_cover": svc._build_cover,
            "_build_classroom_context": svc._build_classroom_context,
            "read_generation_sessions": svc.read_generation_sessions,
            "_offering_homework_hint": svc._offering_homework_hint,
            "_gather_session_material_text": svc._gather_session_material_text,
            "_generate_one_session": svc._generate_one_session,
            "_fallback_session_from_context": svc._fallback_session_from_context,
        }

        def fake_set_status(plan_id, **kwargs):
            statuses.append({"plan_id": plan_id, **kwargs})

        def fake_save_progress(plan_id, cover, sessions, progress):
            saved.append(
                {
                    "plan_id": plan_id,
                    "cover": cover,
                    "sessions": [dict(item) for item in sessions],
                    "progress": dict(progress),
                }
            )

        async def fail_generate(**kwargs):
            raise RuntimeError("model unavailable")

        def fail_structured_fallback(**kwargs):
            raise AssertionError()

        try:
            svc._set_status = fake_set_status
            svc._save_progress = fake_save_progress
            svc._build_cover = lambda class_offering_id, teacher_id: {"course_name": "Dynamic Web"}
            svc._build_classroom_context = lambda class_offering_id: {}
            svc.read_generation_sessions = lambda class_offering_id, teacher_id: [
                {"title": "Session A", "section_minutes": 80, "schedule": {}, "source_material_ids": ["7"]},
            ]
            svc._offering_homework_hint = lambda class_offering_id: {}
            svc._gather_session_material_text = lambda class_offering_id, meta, teacher_id: "material"
            svc._generate_one_session = fail_generate
            svc._fallback_session_from_context = fail_structured_fallback

            await svc.run_generation_job("plan-minimal-fallback", 10, 20)
        finally:
            for name, value in originals.items():
                setattr(svc, name, value)

        self.assertEqual(len(saved[-1]["sessions"]), 1)
        session = saved[-1]["sessions"][0]
        self.assertEqual(session["chapter"], "Session A")
        self.assertTrue(session["ai_fallback"])
        self.assertIn("AssertionError", session["ai_fallback_reason"])
        self.assertEqual(session["source_material_ids"], ["7"])
        self.assertEqual(statuses[-1]["status"], "ready")
        self.assertEqual(statuses[-1]["ai_gen_status"], "completed_with_fallback")
        self.assertIn("RuntimeError", statuses[-1]["ai_gen_error"])
        self.assertIn("AssertionError", statuses[-1]["ai_gen_error"])


if __name__ == "__main__":
    unittest.main()
