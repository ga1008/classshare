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
    resolve_execution_plan,
)
from classroom_app.services.deterministic_exam_grading import (
    apply_deterministic_grading_result,
    build_deterministic_grading_evidence,
    format_deterministic_evidence_prompt,
)


class AIMultimodalPolicyTests(unittest.TestCase):
    def test_text_and_multimodal_provider_orders_are_isolated(self):
        env = {"AI_PLATFORM_PRIORITY": "deepseek,volcengine"}
        self.assertEqual(provider_order_for_task(AI_TASK_FAST_TEXT, environ=env), ["deepseek"])
        self.assertEqual(provider_order_for_task(AI_TASK_DEEP_TEXT, environ=env), ["deepseek"])
        self.assertEqual(
            provider_order_for_task(AI_TASK_VISION_OCR, "vision", environ=env),
            ["volcengine"],
        )
        self.assertEqual(
            provider_order_for_task(AI_TASK_MULTIMODAL_GRADING, "vision", environ=env),
            ["volcengine"],
        )
        self.assertEqual(
            provider_order_for_task(AI_TASK_MULTIMODAL_ADJUDICATION, "vision", environ=env),
            ["volcengine"],
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
        # A bare needs_review flag is surfaced to the teacher but no longer buys a paid adjudication.
        self.assertNotIn("model_requested_review", reasons)
        self.assertIn("evidence_conflict", reasons)
        self.assertIn("score_consistency_delta=15", reasons)
        self.assertEqual([], ai_assistant._grading_adjudication_reasons(
            {"confidence": 0.9, "needs_review": True, "evidence_conflicts": []}, image_count=3, format_repair_required=False))
        self.assertIn("empty_text_with_attachments", ai_assistant._grading_adjudication_reasons(
            {"confidence": 0.9, "needs_review": False, "evidence_conflicts": [], "summary": "截图完整"},
            image_count=3, format_repair_required=False, blank_answers_with_attachments=True))
        blank = json.dumps({"answers": [{"question_id": "q9", "type": "textarea", "answer": "  ",
            "attachments": [{"file_name": "1.png"}]}]})
        self.assertTrue(ai_assistant._has_blank_answers_with_attachments(blank))
        self.assertFalse(ai_assistant._has_blank_answers_with_attachments(
            json.dumps({"answers": [{"question_id": "q9", "answer": "172.10.8.1", "attachments": [{"file_name": "1.png"}]}]})))
        self.assertFalse(ai_assistant._has_blank_answers_with_attachments(None))

    def test_soft_format_trimming_keeps_substantive_validation_strict(self):
        job = ai_assistant.GradingJob(submission_id=1, rubric_md="r", answers_json=json.dumps(
            {"answers": [{"question_id": "q1", "answer": "A"}, {"question_id": "q2", "answer": "B"}]}))
        raw = {"score": 90, "summary": "第一句总评。" * 30, "confidence": 0.9, "needs_review": False, "evidence_conflicts": [],
            "questions": [
                {"question_no": 1, "question_id": "q1", "score": 50, "max_score": 50, "deduction_points": "无", "evaluation": ""},
                {"question_no": 2, "question_id": "q2", "score": 40, "max_score": 50, "deduction_points": "扣分" * 60,
                 "evaluation": "这是一条明显超过二十个字上限的评价文字需要被截断处理"},
            ]}
        result = ai_assistant._validate_grading_result_for_job(raw, job)
        self.assertLessEqual(len(result["summary"]), 120)
        self.assertTrue(result["summary"].endswith("。"))
        self.assertEqual("达标", result["questions"][0]["evaluation"])
        self.assertLessEqual(len(result["questions"][1]["evaluation"]), 20)
        self.assertLessEqual(len(result["questions"][1]["deduction_points"]), 80)
        with self.assertRaises(ValueError):
            ai_assistant._validate_grading_result_for_job({**raw, "questions": [
                {**raw["questions"][0], "score": 0, "deduction_points": "无", "evaluation": "x"}, raw["questions"][1]]}, job)
        with self.assertRaises(ValueError):
            ai_assistant._validate_grading_result_for_job({**raw, "summary": ""}, job)

    def test_low_confidence_adjudication_result_requests_teacher_review(self):
        required, reasons, confidence = ai_assistant._grading_review_metadata(
            {"confidence": 0.6, "needs_review": True}
        )
        self.assertTrue(required)
        self.assertEqual(confidence, 0.6)
        self.assertEqual(reasons, ["model_requested_review", "low_confidence"])


class StreamingFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_does_not_bypass_provider_allowlist_before_first_token(self):
        class FailingCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("provider unavailable")

        class FailingOpenAI:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=FailingCompletions())

            async def close(self):
                pass

        volcengine = {**ai_assistant.PLATFORMS_CONFIG["volcengine"], "enabled": True, "api_key": "test-only"}
        with (
            mock.patch.object(ai_assistant, "AsyncOpenAI", FailingOpenAI),
            mock.patch.object(ai_assistant, "_write_ai_usage_log", lambda event: None),
            mock.patch.object(ai_assistant, "ENABLED_PLATFORMS", ["volcengine"]),
            mock.patch.dict(ai_assistant.PLATFORMS_CONFIG, {"volcengine": volcengine}),
            mock.patch.object(ai_assistant, "ai_model_router", ai_assistant.AIModelLoadRouter()),
            mock.patch.object(ai_assistant, "ai_limiter", ai_assistant.AIPriorityLimiter(2)),
        ):
            events = [json.loads(event) async for event in ai_assistant._call_ai_platform_chat_stream_events(
                "system", [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}],
                capability="vision", task_type=AI_TASK_VISION_INTERACTIVE,
            )]
        self.assertEqual([event["platform"] for event in events if event["event"] == "meta"], ["volcengine"])
        self.assertFalse(any(event["event"] == "answer_delta" for event in events))
        self.assertTrue(any(event["event"] == "error" for event in events))
        self.assertEqual(sum(event["event"] == "done" for event in events), 1)


class GradingPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_multimodal_grading_uses_business_profile_and_fixed_exam_score(self):
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
                business_context={"operation": "grading", "assessment_kind": "homework"},
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
                "platform_name": "volcengine",
                "platform_config": {"name": "volcengine", "type": "volcengine"},
                "execution_plan": resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", job.business_context, environ={}).to_dict(),
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
        self.assertEqual(calls[0]["preferred_platform"], "volcengine")
        self.assertEqual(calls[0]["business_context"]["assessment_kind"], "homework")
        self.assertEqual(callback_payloads[0]["status"], "graded")
        self.assertEqual(callback_payloads[0]["score"], 0)
        self.assertTrue(callback_payloads[0]["review_required"])
        self.assertIn("automatic_review_disabled", callback_payloads[0]["review_reason_codes"])
        self.assertIn("客观题答案不正确", callback_payloads[0]["feedback_md"])


if __name__ == "__main__":
    unittest.main()
