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
            "deepseek": {"enabled": True, "api_key": "synthetic-secret-marker", "max_concurrency": 4, "supports": {}},
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
        for name, effort, cap in (("vision_homework", "low", 8192), ("vision_midterm", "high", 16384),
                ("vision_final", "high", 16384), ("vision_personal_stage", "high", 16384),
                ("vision_legacy_unknown", "high", 16384), ("vision_exam_generation", "high", 16384),
                ("vision_adjudication", "high", 16384)):
            self.assertEqual(("doubao-seed-2-1-pro-260628", effort, cap),
                (profiles[name]["model"], profiles[name]["reasoning_effort"], profiles[name]["max_output_tokens_total"]))
        self.assertEqual("doubao-seed-2-0-lite-260428", profiles["vision_edge"]["model"])
        self.assertEqual(("high", "max", "disabled"), (profiles["text_deep"]["reasoning_effort"],
            profiles["text_assessment"]["reasoning_effort"], profiles["text_fast"]["thinking_type"]))
        self.assertEqual("process", policy["capacity"]["scope"])
        self.assertEqual(6, policy["capacity"]["global_max_concurrent"])
        self.assertEqual({}, policy["capacity"]["provider_reserved"])
        self.assertEqual(2, policy["attempt_budget"]["max_possibly_billed_generations"])
        self.assertEqual("database_local_day", policy["review_quota"]["scope"])
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))
        self.sdk.assert_not_called()
        self.db.assert_not_called()

    async def test_effective_caps_follow_current_configuration_not_hardcoded_health_labels(self):
        with mock.patch.dict(os.environ, {"AI_PROFILE_VISION_PRO_LOW_MAX_OUTPUT_TOKENS": "12000",
                "AI_STRUCTURED_OUTPUT_MEDIUM_MAX_TOKENS": "20000", "AI_STRUCTURED_OUTPUT_LARGE_MAX_TOKENS": "30000"}):
            policy = (await ai.internal_health())["model_routing"]["execution_policy"]
        self.assertTrue(policy["ok"])
        homework = next(item for item in policy["profiles"] if item["case"] == "vision_homework")
        self.assertEqual(12000, homework["max_output_tokens_total"])
        self.assertEqual([12000, 20000, 30000],
            [tier["max_output_tokens_total"] for tier in policy["structured_output_tiers"]["grading_v1"]])

    async def test_invalid_profile_or_old_priority_marks_unavailable_without_echoing_value(self):
        with mock.patch.dict(os.environ, {"AI_TEXT_DEEP_PRIORITY": "qwen",
                "AI_VISION_PRO_MODEL": "synthetic-secret-marker"}):
            response = await ai.internal_health()
        policy = response["model_routing"]["execution_policy"]
        self.assertFalse(policy["ok"])
        profiles = {item["case"]: item for item in policy["profiles"]}
        self.assertFalse(profiles["text_assessment"]["available"])
        self.assertEqual("invalid_execution_configuration", profiles["vision_homework"]["error"])
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))

    async def test_database_health_failure_does_not_echo_connection_string(self):
        with mock.patch.object(ai, "AI_DURABLE_JOBS_ENABLED", True), mock.patch.object(ai,
                "ai_durable_job_health_snapshot", side_effect=RuntimeError("postgres://synthetic-secret-marker")):
            response = await ai.internal_health()
        self.assertFalse(response["durable_jobs"]["ok"])
        self.assertEqual("database_health_unavailable", response["durable_jobs"]["error"])
        self.assertNotIn("synthetic-secret-marker", json.dumps(response))
