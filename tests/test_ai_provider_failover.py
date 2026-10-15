"""Cross-vendor failover: peer tiers, breaker-driven ordering and switch/recover timing."""
import contextlib
import json
import os
import unittest
from unittest import mock

import dotenv
import httpx

with mock.patch.object(dotenv, "load_dotenv", return_value=False), mock.patch.dict(os.environ, {"DB_ENGINE": "sqlite", "AI_DURABLE_JOBS_ENABLED": "false"}, clear=True):
    import ai_assistant as ai

from classroom_app.services.ai_model_policy import (
    AI_TASK_DEEP_TEXT, AI_TASK_FAST_TEXT, AI_TASK_MULTIMODAL_ADJUDICATION, AI_TASK_MULTIMODAL_GRADING,
    AI_TASK_VISION_INTERACTIVE, DEEPSEEK_FLASH_MODEL, DOUBAO_LITE_MODEL, DOUBAO_PRO_MODEL, FAILOVER_PROFILE_SUFFIX,
    failover_plan, resolve_execution_plan,
)
from classroom_app.services.ai_provider_health import STATE_CLOSED, STATE_OPEN

GRADE = {"score": 80, "summary": "Review", "confidence": 0.9, "needs_review": False,
         "evidence_conflicts": [], "questions": [{"question_no": 1, "score": 80, "max_score": 100}]}
IMAGE_MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "Grade only the supplied evidence."},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
]}]
TEXT_MESSAGES = [{"role": "user", "content": "hello"}]


class FailoverPlanTests(unittest.TestCase):
    def test_deepseek_tiers_map_to_doubao_equivalents(self):
        fast = resolve_execution_plan(AI_TASK_FAST_TEXT, "standard", {"operation": "chat"}, environ={})
        peer = failover_plan(fast, environ={})
        self.assertEqual(("volcengine", DOUBAO_LITE_MODEL, "text_fast" + FAILOVER_PROFILE_SUFFIX), (peer.provider, peer.model, peer.profile_id))
        self.assertEqual(fast.max_output_tokens_total, peer.max_output_tokens_total)
        deep = resolve_execution_plan(AI_TASK_DEEP_TEXT, "thinking", {"operation": "grading", "assessment_kind": "homework"}, environ={})
        peer = failover_plan(deep, environ={})
        self.assertEqual(("volcengine", DOUBAO_PRO_MODEL, "high", "enabled"), (peer.provider, peer.model, peer.reasoning_effort, peer.thinking_type))
        flash = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", {"operation": "grading", "assessment_kind": "homework"}, environ={})
        peer = failover_plan(flash, environ={})
        self.assertEqual(("volcengine", DOUBAO_PRO_MODEL, "vision", "grading"), (peer.provider, peer.model, peer.capability, peer.operation))
        self.assertEqual((), peer.allowed_fallbacks)
        self.assertIsNone(failover_plan(peer, environ={}))

    def test_doubao_tiers_map_to_deepseek_flash(self):
        final = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", {"operation": "grading", "assessment_kind": "final"}, environ={})
        peer = failover_plan(final, environ={})
        self.assertEqual(("deepseek", DEEPSEEK_FLASH_MODEL, "max", "enabled", "vision"),
                         (peer.provider, peer.model, peer.reasoning_effort, peer.thinking_type, peer.capability))
        edge = resolve_execution_plan(AI_TASK_VISION_INTERACTIVE, "vision", {"operation": "chat", "source_feature": "blog"}, environ={})
        self.assertEqual(("deepseek", "low"), (failover_plan(edge, environ={}).provider, failover_plan(edge, environ={}).reasoning_effort))
        adjudication = resolve_execution_plan(AI_TASK_MULTIMODAL_ADJUDICATION, "vision", {"operation": "adjudication", "assessment_kind": "final"}, environ={})
        self.assertEqual("adjudication", failover_plan(adjudication, environ={}).operation)
        self.assertNotEqual(final.route_id, peer.route_id)


class FailoverTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(mock.patch("socket.create_connection", side_effect=AssertionError("Network is forbidden in tests")))
        catalog = {
            "deepseek": {"enabled": True, "api_key": "unit-test-only", "type": "openai", "base_url": "https://deepseek.invalid/v1",
                "max_concurrency": 4, "supports": {"images": True, "authoritative_grading": True},
                "can_force_json": {"thinking": True, "standard": True, "vision": True}},
            "volcengine": {"enabled": True, "api_key": "unit-test-only", "type": "volcengine", "base_url": "https://ark.invalid/v3",
                "max_concurrency": 2, "supports": {"images": True, "authoritative_grading": True}, "can_force_json": {"vision": False}},
        }
        self.stack.enter_context(mock.patch.object(ai, "PLATFORMS_CONFIG", catalog))
        self.stack.enter_context(mock.patch.object(ai, "ENABLED_PLATFORMS", list(catalog)))
        self.stack.enter_context(mock.patch.object(ai, "ai_model_router", ai.AIModelLoadRouter()))
        self.stack.enter_context(mock.patch.object(ai, "ai_limiter", ai.AIPriorityLimiter(6)))
        self.stack.enter_context(mock.patch.object(ai, "_write_ai_usage_log", lambda event: None))
        self.stack.enter_context(mock.patch.object(ai, "AI_NONSTREAM_USE_PROVIDER_STREAM", False))
        self.stack.enter_context(mock.patch.object(ai, "_provider_retry_delay_seconds", return_value=0))
        self.stack.enter_context(mock.patch.object(ai, "AI_PROVIDER_FAILOVER_ENABLED", True))
        ai.provider_health.reset_all()
        self.addCleanup(ai.provider_health.reset_all)
        self.requests = []
        self.failing = {"deepseek": None}  # provider -> callable(request) raising/returning, or None
        real_http, real_openai, real_ark = httpx.AsyncClient, ai.AsyncOpenAI, ai.AsyncArk
        transport = httpx.MockTransport(self.respond)
        self.stack.enter_context(mock.patch.object(ai, "AsyncOpenAI", side_effect=lambda **kw: real_openai(**kw, http_client=real_http(transport=transport))))
        self.stack.enter_context(mock.patch.object(ai, "AsyncArk", side_effect=lambda **kw: real_ark(**kw, http_client=real_http(transport=transport))))

    def provider_of(self, request):
        return "deepseek" if "deepseek" in request.url.host else "volcengine"

    def respond(self, request):
        body = json.loads(request.content)
        provider = self.provider_of(request)
        self.requests.append((provider, body["model"], body))
        failure = self.failing.get(provider)
        if failure is not None:
            return failure(request)
        content = json.dumps(GRADE)
        return httpx.Response(200, json={"id": "chat", "object": "chat.completion", "created": 1, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    @staticmethod
    def server_error(request):
        return httpx.Response(503, json={"error": {"message": "We were unable to start processing your request within the 900-second timeout limit."}})

    @staticmethod
    def connect_error(request):
        raise httpx.ConnectError("connection refused", request=request)

    async def grade(self, **context):
        metadata = {}
        result = await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
            business_context={"operation": "grading", "assessment_kind": "homework", **context}, require_json_output=True, metadata_out=metadata)
        return result, metadata

    async def test_server_error_switches_vendor_at_once_and_trips_after_threshold(self):
        self.failing["deepseek"] = self.server_error
        result, metadata = await self.grade()
        self.assertEqual(80, result["score"])
        self.assertEqual([("deepseek", DEEPSEEK_FLASH_MODEL), ("volcengine", DOUBAO_PRO_MODEL)], [(p, m) for p, m, _ in self.requests])
        self.assertEqual("deepseek", metadata["failover_from"]["provider"])
        self.assertEqual(STATE_CLOSED, ai.provider_health.state_of("deepseek"))  # one failure: below threshold
        self.requests.clear()
        await self.grade()
        self.assertEqual(STATE_OPEN, ai.provider_health.state_of("deepseek"))
        self.requests.clear()
        # Open breaker: Doubao serves directly, DeepSeek is not even tried.
        result, metadata = await self.grade()
        self.assertEqual([("volcengine", DOUBAO_PRO_MODEL)], [(p, m) for p, m, _ in self.requests])
        self.assertEqual("deepseek", metadata["failover_from"]["provider"])
        snapshot = ai.provider_health.snapshot()["providers"]
        self.assertGreaterEqual(snapshot["deepseek"]["failovers_out"], 3)
        self.assertEqual(snapshot["deepseek"]["failovers_out"], snapshot["volcengine"]["served_as_failover"])

    async def test_recovery_probe_closes_breaker_and_traffic_returns(self):
        self.failing["deepseek"] = self.server_error
        await self.grade()
        await self.grade()
        self.assertEqual(STATE_OPEN, ai.provider_health.state_of("deepseek"))
        self.failing["deepseek"] = None
        ai.provider_health.reset("deepseek")  # stands in for the cooldown + successful probe
        self.requests.clear()
        result, metadata = await self.grade()
        self.assertEqual([("deepseek", DEEPSEEK_FLASH_MODEL)], [(p, m) for p, m, _ in self.requests])
        self.assertNotIn("failover_from", metadata)

    async def test_doubao_outage_sends_final_exam_to_deepseek_flash_max(self):
        self.failing["volcengine"] = self.server_error
        result, metadata = await self.grade(assessment_kind="final")
        self.assertEqual(80, result["score"])
        providers = [(p, m) for p, m, _ in self.requests]
        self.assertEqual(("volcengine", DOUBAO_PRO_MODEL), providers[0])
        self.assertEqual(("deepseek", DEEPSEEK_FLASH_MODEL), providers[-1])
        body = self.requests[-1][2]
        self.assertEqual("max", body.get("reasoning_effort"))
        self.assertEqual("volcengine", metadata["failover_from"]["provider"])

    async def test_both_vendors_unreachable_is_local_network_not_an_outage(self):
        self.failing["deepseek"] = self.connect_error
        self.failing["volcengine"] = self.connect_error
        with self.assertRaises(ai.HTTPException):
            await self.grade()
        with self.assertRaises(ai.HTTPException):
            await self.grade()
        snapshot = ai.provider_health.snapshot()["providers"]
        self.assertEqual(STATE_CLOSED, snapshot["deepseek"]["state"])
        self.assertTrue(snapshot["deepseek"]["network_suspected"] or snapshot["volcengine"]["network_suspected"])

    async def test_format_errors_do_not_cross_vendors_or_count_as_outage(self):
        def bad_json(request):
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": "chat", "object": "chat.completion", "created": 1, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": ""}}]})
        self.failing["deepseek"] = bad_json
        with mock.patch.object(ai, "_bounded_fallback_routes", return_value=[]):
            with self.assertRaises(ai.HTTPException):
                await ai._call_ai_platform(TEXT_MESSAGES, capability="standard", task_type=AI_TASK_FAST_TEXT,
                    business_context={"operation": "chat"})
        self.assertEqual(["deepseek"], [p for p, _, _ in self.requests])
        self.assertEqual(0, ai.provider_health.snapshot()["providers"]["deepseek"]["recent_failures"])

    async def test_failover_disabled_keeps_single_vendor_routes(self):
        with mock.patch.object(ai, "AI_PROVIDER_FAILOVER_ENABLED", False):
            routes = ai._build_model_routes("standard", task_type=AI_TASK_FAST_TEXT, business_context={"operation": "chat"})
        self.assertEqual(["deepseek"], [route.platform_name for route in routes])
        routes = ai._build_model_routes("standard", task_type=AI_TASK_FAST_TEXT, business_context={"operation": "chat"})
        self.assertEqual(["deepseek", "volcengine"], [route.platform_name for route in routes])
        self.assertEqual("failover", routes[1].reason)
        ai.provider_health.trip("deepseek", "test")
        routes = ai._build_model_routes("standard", task_type=AI_TASK_FAST_TEXT, business_context={"operation": "chat"})
        self.assertEqual(["volcengine", "deepseek"], [route.platform_name for route in routes])


if __name__ == "__main__":
    unittest.main()
