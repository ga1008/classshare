import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ai_assistant
from classroom_app.services.ai_model_policy import (
    AI_TASK_DEEP_TEXT,
    AI_TASK_DOCUMENT_MULTIMODAL,
    AI_TASK_FAST_TEXT,
    AI_TASK_MULTIMODAL_ADJUDICATION,
    AI_TASK_MULTIMODAL_GRADING,
    AI_TASK_VISION_INTERACTIVE,
    AI_TASK_VISION_OCR,
    normalize_ai_task_type,
    provider_order_for_task,
)
from classroom_app.services.deterministic_exam_grading import (
    apply_deterministic_grading_result,
    build_deterministic_grading_evidence,
    format_deterministic_evidence_prompt,
)


class AIMultimodalPolicyTests(unittest.TestCase):
    def test_text_and_multimodal_provider_orders_are_isolated(self):
        env = {"AI_PLATFORM_PRIORITY": "deepseek,volcengine"}
        self.assertEqual(provider_order_for_task(AI_TASK_FAST_TEXT, environ=env), ["deepseek", "volcengine"])
        self.assertEqual(provider_order_for_task(AI_TASK_DEEP_TEXT, environ=env), ["deepseek", "volcengine"])
        self.assertEqual(
            provider_order_for_task(AI_TASK_VISION_OCR, "vision", environ=env),
            ["qwen", "volcengine", "zhipu"],
        )
        self.assertEqual(
            provider_order_for_task(AI_TASK_MULTIMODAL_GRADING, "vision", environ=env),
            ["qwen", "volcengine"],
        )
        self.assertEqual(
            provider_order_for_task(AI_TASK_MULTIMODAL_ADJUDICATION, "vision", environ=env),
            ["volcengine", "qwen"],
        )

    def test_legacy_aliases_converge_on_specific_tasks(self):
        self.assertEqual(normalize_ai_task_type("vision_light", "vision"), AI_TASK_VISION_OCR)
        self.assertEqual(normalize_ai_task_type("document_vision", "vision"), AI_TASK_DOCUMENT_MULTIMODAL)
        self.assertEqual(normalize_ai_task_type(None, "standard"), AI_TASK_FAST_TEXT)

    def test_provider_catalog_keeps_qwen_and_glm_out_of_text_models(self):
        for provider in ("qwen", "zhipu"):
            config = ai_assistant.PLATFORMS_CONFIG[provider]
            self.assertIsNone(config["task_models"].get(AI_TASK_FAST_TEXT))
            self.assertIsNone(config["task_models"].get(AI_TASK_DEEP_TEXT))
            self.assertTrue(config["supports"]["images"])
        self.assertFalse(ai_assistant.PLATFORMS_CONFIG["zhipu"]["supports"]["authoritative_grading"])

    def test_qwen_thinking_is_task_tier_aware(self):
        qwen_config = {"name": "qwen", **ai_assistant.PLATFORMS_CONFIG["qwen"]}
        light = ai_assistant.AIModelRoute(
            "qwen", qwen_config, AI_TASK_VISION_INTERACTIVE, "vision", "qwen3.6-flash"
        )
        deep = ai_assistant.AIModelRoute(
            "qwen", qwen_config, AI_TASK_MULTIMODAL_GRADING, "vision", "qwen3.7-plus"
        )
        light_kwargs = {}
        deep_kwargs = {}
        ai_assistant._apply_openai_provider_options(light_kwargs, light)
        ai_assistant._apply_openai_provider_options(deep_kwargs, deep)
        self.assertFalse(light_kwargs["extra_body"]["enable_thinking"])
        self.assertTrue(deep_kwargs["extra_body"]["enable_thinking"])

    def test_cost_estimate_uses_task_tier(self):
        estimate = ai_assistant._estimate_provider_cost_cny(
            "qwen",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            task_type=AI_TASK_MULTIMODAL_GRADING,
        )
        self.assertEqual(estimate["estimated_cost"], 8.0)
        self.assertEqual(estimate["price_tier"], "deep")

    def test_business_call_sites_use_specific_multimodal_tasks(self):
        expected_markers = {
            "classroom_app/services/gongwen_content_service.py": '"task_type": "vision_ocr"',
            "classroom_app/services/gongwen_integration_service.py": '"task_type": "vision_ocr"',
            "classroom_app/services/assessment_plan_import_service.py": '"document_multimodal_understanding"',
            "classroom_app/services/lesson_plan_import_service.py": '"document_multimodal_understanding"',
            "classroom_app/services/teacher_evaluation_import_service.py": '"document_multimodal_understanding"',
            "classroom_app/services/resume/resume_import_service.py": '"document_multimodal_understanding"',
            "classroom_app/services/material_ai_import_service.py": 'task_type="document_multimodal_understanding"',
            "classroom_app/services/discussion_ai_service.py": '"vision_interactive"',
        }
        for relative_path, marker in expected_markers.items():
            source = Path(relative_path).read_text(encoding="utf-8")
            self.assertIn(marker, source, relative_path)


class DeterministicExamGradingTests(unittest.TestCase):
    def setUp(self):
        self.exam = {
            "grading": {"total_score": 100},
            "pages": [
                {
                    "name": "试卷",
                    "questions": [
                        {"id": "q1", "type": "radio", "options": ["A. 甲", "B. 乙"], "answer": "A", "points": 20},
                        {"id": "q2", "type": "checkbox", "options": ["A. 甲", "B. 乙", "C. 丙"], "answer": ["A", "C"], "points": 20},
                        {"id": "q3", "type": "text", "answer": "57", "points": 20},
                        {"id": "q4", "type": "textarea", "answer": "说明过程", "points": 40},
                    ],
                }
            ],
        }

    def test_evidence_fixes_only_indisputable_scores(self):
        answers = {
            "answers": [
                {"question_id": "q1", "type": "radio", "answer": "A"},
                {"question_id": "q2", "type": "checkbox", "answer": "A"},
                {"question_id": "q3", "type": "text", "answer": "57.0"},
                {"question_id": "q4", "type": "textarea", "answer": ""},
            ]
        }
        evidence = build_deterministic_grading_evidence(self.exam, answers)
        fixed = evidence["fixed_scores"]
        self.assertEqual(fixed["q1"]["fixed_score"], 20)
        self.assertNotIn("q2", fixed)
        self.assertEqual(fixed["q3"]["fixed_score"], 20)
        self.assertEqual(fixed["q4"]["fixed_score"], 0)
        prompt = format_deterministic_evidence_prompt(evidence)
        self.assertIn("固定得分 20/20", prompt)
        self.assertIn("partial_or_wrong_checkbox_requires_rubric", prompt)

    def test_result_applies_fixed_scores_and_recomputes_total(self):
        answers = {
            "answers": [
                {"question_id": "q1", "type": "radio", "answer": "B"},
                {"question_id": "q2", "type": "checkbox", "answer": ["A", "C"]},
                {"question_id": "q3", "type": "text", "answer": "57"},
                {"question_id": "q4", "type": "textarea", "answer": "有效过程"},
            ]
        }
        evidence = build_deterministic_grading_evidence(json.dumps(self.exam), json.dumps(answers))
        result = {
            "score": 100,
            "questions": [
                {"question_no": 1, "question_id": "q1", "score": 20, "max_score": 20, "deduction_points": "无"},
                {"question_no": 2, "question_id": "q2", "score": 20, "max_score": 20, "deduction_points": "无"},
                {"question_no": 3, "question_id": "q3", "score": 20, "max_score": 20, "deduction_points": "无"},
                {"question_no": 4, "question_id": "q4", "score": 30, "max_score": 40, "deduction_points": "过程略少"},
            ],
        }
        applied = apply_deterministic_grading_result(result, evidence)
        self.assertEqual(applied["questions"][0]["score"], 0)
        self.assertEqual(applied["score"], 70)
        self.assertEqual(applied["_quality_audit"]["score_sum_delta"], 30)

    def test_adjudication_reasons_cover_conflict_and_score_delta(self):
        reasons = ai_assistant._grading_adjudication_reasons(
            {
                "confidence": 0.5,
                "needs_review": True,
                "evidence_conflicts": ["S 值冲突"],
                "_quality_audit": {"score_sum_delta": 15},
            },
            image_count=10,
            format_repair_required=False,
        )
        self.assertTrue(any(reason.startswith("low_confidence=") for reason in reasons))
        self.assertIn("model_requested_review", reasons)
        self.assertIn("evidence_conflict", reasons)
        self.assertIn("score_consistency_delta=15", reasons)

    def test_low_confidence_adjudication_result_requests_teacher_review(self):
        required, reasons, confidence = ai_assistant._grading_review_metadata(
            {"confidence": 0.6, "needs_review": True}
        )
        self.assertTrue(required)
        self.assertEqual(confidence, 0.6)
        self.assertEqual(reasons, ["model_requested_review", "low_confidence"])


class StreamingFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_falls_back_only_before_first_content_token(self):
        class FailingCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("qwen unavailable")

        class FailingOpenAI:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=FailingCompletions())

        class SuccessfulStream:
            def __aiter__(self):
                self._sent = False
                return self

            async def __anext__(self):
                if self._sent:
                    raise StopAsyncIteration
                self._sent = True
                delta = SimpleNamespace(content="豆包回退成功", reasoning_content=None)
                return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)

        class SuccessfulCompletions:
            async def create(self, **kwargs):
                return SuccessfulStream()

        class SuccessfulArk:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=SuccessfulCompletions())

        qwen = {**ai_assistant.PLATFORMS_CONFIG["qwen"], "enabled": True, "api_key": "test-qwen"}
        volcengine = {
            **ai_assistant.PLATFORMS_CONFIG["volcengine"],
            "enabled": True,
            "api_key": "test-volcengine",
        }
        with (
            mock.patch.object(ai_assistant, "AsyncOpenAI", FailingOpenAI),
            mock.patch.object(ai_assistant, "AsyncArk", SuccessfulArk),
            mock.patch.object(ai_assistant, "_write_ai_usage_log", lambda event: None),
            mock.patch.object(ai_assistant, "ENABLED_PLATFORMS", ["qwen", "volcengine"]),
            mock.patch.dict(
                ai_assistant.PLATFORMS_CONFIG,
                {"qwen": qwen, "volcengine": volcengine},
                clear=False,
            ),
        ):
            events = []
            async for raw_event in ai_assistant._call_ai_platform_chat_stream_events(
                "system",
                [{"role": "user", "content": [{"type": "text", "text": "看图"}]}],
                capability="vision",
                task_type=AI_TASK_VISION_INTERACTIVE,
            ):
                events.append(json.loads(raw_event))

        self.assertEqual([item["platform"] for item in events if item["event"] == "meta"], ["qwen", "volcengine"])
        self.assertEqual(
            "".join(item.get("delta", "") for item in events if item["event"] == "answer_delta"),
            "豆包回退成功",
        )
        self.assertFalse(any(item["event"] == "error" for item in events))
        self.assertEqual(sum(item["event"] == "done" for item in events), 1)


class GradingPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_multimodal_grading_uses_qwen_route_and_fixed_exam_score(self):
        calls = []
        callback_payloads = []

        async def fake_call(messages, **kwargs):
            calls.append(kwargs)
            return {
                "score": 100,
                "summary": "作答完整。",
                "confidence": 0.9,
                "needs_review": False,
                "evidence_conflicts": [],
                "questions": [
                    {
                        "question_no": 1,
                        "question_id": "q1",
                        "score": 100,
                        "max_score": 100,
                        "deduction_points": "无",
                        "evaluation": "继续保持",
                    }
                ],
            }

        async def fake_callback(payload, submission_id):
            callback_payloads.append(dict(payload))

        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "answer.png"
            image_path.write_bytes(tiny_png)
            job = ai_assistant.GradingJob(
                submission_id=42,
                rubric_md="第1题 100分，选 A 得满分。",
                requirements_md="完成单选题并提交截图。",
                files=[
                    ai_assistant.GradingFile(
                        stored_path=str(image_path),
                        original_filename="answer.png",
                        mime_type="image/png",
                        file_size=len(tiny_png),
                    )
                ],
                answers_json=json.dumps(
                    {"answers": [{"question_id": "q1", "type": "radio", "answer": "B"}]},
                    ensure_ascii=False,
                ),
                exam_scoring_json=json.dumps(
                    {
                        "grading": {"total_score": 100},
                        "pages": [
                            {
                                "questions": [
                                    {
                                        "id": "q1",
                                        "type": "radio",
                                        "options": ["A. 正确", "B. 错误"],
                                        "answer": "A",
                                        "points": 100,
                                    }
                                ]
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                submission_fingerprint="fingerprint",
            )
            execution = {
                "platform_name": "qwen",
                "platform_config": {"name": "qwen", "type": "openai"},
                "capability": "vision",
                "task_type": AI_TASK_MULTIMODAL_GRADING,
                "mode": "vision_messages",
            }
            with (
                mock.patch.object(ai_assistant, "_select_grading_execution", return_value=execution),
                mock.patch.object(ai_assistant, "_call_ai_platform", side_effect=fake_call),
                mock.patch.object(ai_assistant, "_post_grading_callback_with_retry", side_effect=fake_callback),
                mock.patch.object(ai_assistant, "MAIN_APP_CALLBACK_URL", "http://callback.invalid"),
                mock.patch.object(ai_assistant, "AI_GRADING_ADJUDICATION_ENABLED", False),
            ):
                await ai_assistant.run_grading_job(job)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["task_type"], AI_TASK_MULTIMODAL_GRADING)
        self.assertEqual(calls[0]["preferred_platform"], "qwen")
        self.assertEqual(callback_payloads[0]["status"], "graded")
        self.assertEqual(callback_payloads[0]["score"], 0)
        self.assertFalse(callback_payloads[0]["review_required"])
        self.assertIn("客观题答案不正确", callback_payloads[0]["feedback_md"])


if __name__ == "__main__":
    unittest.main()
