"""Safe internal health contract: pure profile resolution, no model or DB traffic."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import dotenv

with mock.patch.object(dotenv, "load_dotenv", return_value=False), mock.patch.dict(os.environ,
        {"DB_ENGINE": "sqlite", "AI_DURABLE_JOBS_ENABLED": "false"}, clear=True), contextlib.redirect_stdout(io.StringIO()):
    import ai_assistant as ai


class AIExecutionHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ,
            {"DB_ENGINE": "sqlite", "AI_DURABLE_JOBS_ENABLED": "false"}, clear=True))
        self.stack.enter_context(mock.patch.object(ai, "AI_DURABLE_JOBS_ENABLED", False))
        self.stack.enter_context(mock.patch.object(ai, "ENABLED_PLATFORMS", ["deepseek", "volcengine"]))
        self.stack.enter_context(mock.patch.object(ai, "PLATFORMS_CONFIG", {
            "deepseek": {"enabled": True, "api_key": "synthetic-secret-marker", "max_concurrency": 4,
                "supports": {"images": True, "authoritative_grading": True}},
            "volcengine": {"enabled": True, "api_key": "synthetic-secret-marker", "max_concurrency": 2,
                "base_url": "https://synthetic-secret-marker.invalid", "supports": {"images": True, "authoritative_grading": True}},
        }))
        self.stack.enter_context(mock.patch.object(ai, "ai_model_router", ai.AIModelLoadRouter()))
        self.stack.enter_context(mock.patch.object(ai, "ai_limiter", ai.AIPriorityLimiter(6)))
        self.sdk = self.stack.enter_context(mock.patch.object(ai, "AsyncArk", side_effect=AssertionError("No SDK call")))
        self.db = self.stack.enter_context(mock.patch.object(ai, "ai_durable_job_health_snapshot", side_effect=AssertionError("No DB call")))

    async def test_health_reports_resolved_business_profiles_and_capacity_without_secrets(self):
        response = await ai.internal_health()
        policy = response["model_routing"]["execution_policy"]
        self.assertTrue(policy["ok"])
        self.assertEqual(ai.AI_EXECUTION_POLICY_VERSION, policy["policy_version"])
        profiles = {item["case"]: item for item in policy["profiles"]}
        for name, effort, cap in (("vision_midterm", "high", 16384), ("vision_final", "high", 16384),
                ("vision_exam_generation", "high", 16384), ("vision_adjudication", "high", 16384)):
            self.assertEqual(("doubao-seed-2-1-pro-260628", "volcengine", effort, cap),
                (profiles[name]["model"], profiles[name]["provider"], profiles[name]["reasoning_effort"], profiles[name]["max_output_tokens_total"]))
        for name in ("vision_homework", "vision_personal_stage", "vision_legacy_unknown"):
            self.assertEqual(("deepseek-flash", "deepseek", "vision_grading_flash", "max", 32768, ["volcengine"]),
                (profiles[name]["model"], profiles[name]["provider"], profiles[name]["profile_id"], profiles[name]["reasoning_effort"],
                 profiles[name]["max_output_tokens_total"], profiles[name]["allowed_fallbacks"]))
        self.assertEqual("doubao-seed-2-0-lite-260428", profiles["vision_edge"]["model"])
        self.assertEqual(("doubao-seed-2-1-pro-260628", "volcengine", "high"),
            (profiles["text_assessment"]["model"], profiles["text_assessment"]["provider"], profiles["text_assessment"]["reasoning_effort"]))
        self.assertEqual(("deepseek-flash", "max"), (profiles["text_homework"]["model"], profiles["text_homework"]["reasoning_effort"]))
        self.assertEqual(("high", "deepseek-flash", "disabled"), (profiles["text_deep"]["reasoning_effort"],
            profiles["text_deep"]["model"], profiles["text_fast"]["thinking_type"]))
        self.assertEqual("process", policy["capacity"]["scope"])
        self.assertEqual(6, policy["capacity"]["global_max_concurrent"])
        self.assertEqual({}, policy["capacity"]["provider_reserved"])
        self.assertEqual(2, policy["attempt_budget"]["max_possibly_billed_generations"])
        self.assertEqual("database_local_day", policy["review_quota"]["scope"])
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))
        self.sdk.assert_not_called()
        self.db.assert_not_called()

    async def test_effective_caps_follow_current_configuration_not_hardcoded_health_labels(self):
        with mock.patch.dict(os.environ, {"AI_PROFILE_VISION_GRADING_FLASH_MAX_OUTPUT_TOKENS": "12000",
                "AI_STRUCTURED_OUTPUT_MEDIUM_MAX_TOKENS": "20000", "AI_STRUCTURED_OUTPUT_LARGE_MAX_TOKENS": "30000"}):
            policy = (await ai.internal_health())["model_routing"]["execution_policy"]
        self.assertTrue(policy["ok"])
        homework = next(item for item in policy["profiles"] if item["case"] == "vision_homework")
        self.assertEqual(12000, homework["max_output_tokens_total"])
        self.assertEqual([12000, 20000, 30000],
            [tier["max_output_tokens_total"] for tier in policy["structured_output_tiers"]["grading_v1"]])

    async def test_rollback_switch_restores_doubao_for_standard_grading(self):
        with mock.patch.dict(os.environ, {"AI_GRADING_STANDARD_PROVIDER": "volcengine", "AI_TEXT_ASSESSMENT_PROVIDER": "deepseek"}):
            policy = (await ai.internal_health())["model_routing"]["execution_policy"]
        self.assertTrue(policy["ok"])
        profiles = {item["case"]: item for item in policy["profiles"]}
        self.assertEqual(("doubao-seed-2-1-pro-260628", "vision_pro_low", "low"),
            (profiles["vision_homework"]["model"], profiles["vision_homework"]["profile_id"], profiles["vision_homework"]["reasoning_effort"]))
        self.assertEqual(("deepseek", "max"), (profiles["text_assessment"]["provider"], profiles["text_assessment"]["reasoning_effort"]))

    async def test_invalid_profile_or_old_priority_marks_unavailable_without_echoing_value(self):
        with mock.patch.dict(os.environ, {"AI_GRADING_STANDARD_MODEL": "synthetic-secret-marker",
                "AI_VISION_PRO_MODEL": "synthetic-secret-marker"}):
            response = await ai.internal_health()
        policy = response["model_routing"]["execution_policy"]
        self.assertFalse(policy["ok"])
        profiles = {item["case"]: item for item in policy["profiles"]}
        self.assertFalse(profiles["text_assessment"]["available"])
        self.assertEqual("invalid_execution_configuration", profiles["vision_homework"]["error"])
        self.assertEqual("invalid_execution_configuration", profiles["vision_final"]["error"])
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))

    async def test_database_health_failure_does_not_echo_connection_string(self):
        with mock.patch.object(ai, "AI_DURABLE_JOBS_ENABLED", True), mock.patch.object(ai,
                "ai_durable_job_health_snapshot", side_effect=RuntimeError("postgres://synthetic-secret-marker")):
            response = await ai.internal_health()
        self.assertFalse(response["durable_jobs"]["ok"])
        self.assertEqual("database_health_unavailable", response["durable_jobs"]["error"])
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))
