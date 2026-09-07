"""Business profile tests use real SDK serialization and an in-memory HTTP transport."""
import asyncio
import contextlib
import dataclasses
import json
import os
import unittest
from unittest import mock

import httpx
import dotenv

# No local .env, production credentials, provider network or background worker.
with mock.patch.object(dotenv, "load_dotenv", return_value=False), mock.patch.dict(os.environ, {"DB_ENGINE": "sqlite", "AI_DURABLE_JOBS_ENABLED": "false"}, clear=True):
    import ai_assistant as ai

from classroom_app.services.ai_model_policy import (
    AIBusinessContext, DOUBAO_LITE_MODEL, DOUBAO_PRO_MODEL,
    AI_TASK_DEEP_TEXT, AI_TASK_DOCUMENT_MULTIMODAL, AI_TASK_MULTIMODAL_GRADING,
    AI_TASK_MULTIMODAL_ADJUDICATION, AI_TASK_VISION_INTERACTIVE,
    AIOutputSizeError, resolve_execution_plan, size_structured_execution_plan,
)


IMAGE_MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "Grade only the supplied evidence."},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
]}]
GRADE = {"score": 80, "summary": "Review", "confidence": 0.9, "needs_review": False,
         "evidence_conflicts": [], "questions": [{"question_no": 1, "score": 80, "max_score": 100}]}


class ProfileResolutionTests(unittest.TestCase):
    def test_structured_question_tiers_and_frozen_caps(self):
        for schema, operation, cases in (
            ("grading_v1", "grading", [(20, 8192), (21, 16384), (40, 16384), (41, 32768), (80, 32768)]),
            ("exam_generation_v1", "generation", [(10, 8192), (11, 16384), (20, 16384), (21, 32768), (40, 32768)]),
        ):
            context = {"operation": operation, "assessment_kind": "homework"}
            base = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", context, environ={})
            for count, cap in cases:
                plan = size_structured_execution_plan(base, schema=schema, question_count=count, environ={})
                self.assertEqual(cap, plan.max_output_tokens_total)
            with self.assertRaises(AIOutputSizeError):
                size_structured_execution_plan(base, schema=schema, question_count=cases[-1][0] + 1, environ={})
        context = {"operation": "grading", "assessment_kind": "homework"}
        base = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", context, environ={})
        plan = size_structured_execution_plan(base, schema="grading_v1", question_count=30,
            environ={"AI_STRUCTURED_OUTPUT_MEDIUM_MAX_TOKENS": "20000"})
        self.assertEqual(20000, plan.max_output_tokens_total)
        restored = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", context, environ={}, execution_snapshot=plan.to_dict())
        self.assertEqual(plan, size_structured_execution_plan(restored, schema="grading_v1", question_count=30,
            execution_snapshot=plan.to_dict(), environ={"AI_STRUCTURED_OUTPUT_MEDIUM_MAX_TOKENS": "22000"}))
        with self.assertRaises(AIOutputSizeError):
            size_structured_execution_plan(base, schema="grading_v1", question_count=30, execution_snapshot=base.to_dict(), environ={})
        with self.assertRaises(AIOutputSizeError):
            size_structured_execution_plan(restored, schema="grading_v1", question_count=31, execution_snapshot=plan.to_dict(), environ={})
        high = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", {"operation": "grading", "assessment_kind": "final"}, environ={})
        self.assertEqual(16384, size_structured_execution_plan(high, schema="grading_v1", question_count=1, environ={}).max_output_tokens_total)
        with self.assertRaises(ValueError):
            AIBusinessContext.from_mapping({"expected_question_count": True})

    def test_business_matrix_and_edge_cannot_promote_itself(self):
        cases = [
            ({"operation": "grading", "assessment_kind": "homework"}, "low", DOUBAO_PRO_MODEL),
            ({"operation": "grading", "assessment_kind": "midterm"}, "high", DOUBAO_PRO_MODEL),
            ({"operation": "grading", "assessment_kind": "final"}, "high", DOUBAO_PRO_MODEL),
            ({"operation": "grading"}, "high", DOUBAO_PRO_MODEL),
            ({"operation": "grading", "source_feature": "personal_stage"}, "high", DOUBAO_PRO_MODEL),
            ({"operation": "generation", "intended_assessment_kind": "midterm"}, "high", DOUBAO_PRO_MODEL),
            ({"operation": "document"}, "low", DOUBAO_PRO_MODEL),
            ({"operation": "chat", "source_feature": "blog", "assessment_kind": "final"}, "low", DOUBAO_LITE_MODEL),
        ]
        for context, effort, model in cases:
            with self.subTest(context=context):
                plan = resolve_execution_plan(AI_TASK_DOCUMENT_MULTIMODAL, "vision", context, environ={})
                self.assertEqual((effort, model), (plan.reasoning_effort, plan.model))
                self.assertEqual("enabled", plan.thinking_type)
        with self.assertRaises(ValueError):
            resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", {"operation": "grading", "source_feature": "blog"}, environ={})

    def test_text_rule_precedes_assessment_and_snapshot_preserves_cap(self):
        context = {"operation": "grading", "assessment_kind": "final"}
        plan = resolve_execution_plan(AI_TASK_DEEP_TEXT, "thinking", context, environ={})
        self.assertEqual("deepseek", plan.provider)
        snapshot = plan.to_dict()
        restored = resolve_execution_plan(AI_TASK_DEEP_TEXT, "thinking", context,
            environ={"AI_PROFILE_TEXT_DEEP_MAX_OUTPUT_TOKENS": "25000"}, execution_snapshot=snapshot)
        self.assertEqual(plan, restored)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.model = "different"
        with self.assertRaises(ValueError):
            AIBusinessContext.from_mapping({"reasoning_effort": "high"})

    def test_model_and_snapshot_cannot_bypass_operation_allowlist(self):
        context = {"operation": "grading", "assessment_kind": "homework"}
        plan = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", context, environ={})
        with self.assertRaises(ValueError):
            resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", context, environ={"AI_VISION_PRO_MODEL": DOUBAO_LITE_MODEL})
        with self.assertRaises(ValueError):
            resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", {**context, "assessment_kind": "final"}, environ={}, execution_snapshot=plan.to_dict())
        high = resolve_execution_plan(AI_TASK_MULTIMODAL_ADJUDICATION, "vision", {**context, "operation": "adjudication"}, environ={})
        self.assertNotEqual(plan.route_id, high.route_id)


class ProfileTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(mock.patch("socket.create_connection", side_effect=AssertionError("Network is forbidden in tests")))
        self.requests = []
        self.events = []
        self.finish = "stop"
        self.include_usage = True
        self.response_status = "completed"
        catalog = {
            "volcengine": {"enabled": True, "api_key": "unit-test-only", "type": "volcengine", "max_concurrency": 2,
                "supports": {"images": True, "authoritative_grading": True}, "can_force_json": {"vision": False}},
            "deepseek": {"enabled": True, "api_key": "unit-test-only", "type": "openai", "base_url": "https://example.invalid/v1", "max_concurrency": 4,
                "can_force_json": {"thinking": True, "standard": True}},
        }
        self.stack.enter_context(mock.patch.object(ai, "PLATFORMS_CONFIG", catalog))
        self.stack.enter_context(mock.patch.object(ai, "ENABLED_PLATFORMS", list(catalog)))
        self.stack.enter_context(mock.patch.object(ai, "ai_model_router", ai.AIModelLoadRouter()))
        self.stack.enter_context(mock.patch.object(ai, "ai_limiter", ai.AIPriorityLimiter(6)))
        self.stack.enter_context(mock.patch.object(ai, "_write_ai_usage_log", side_effect=self.events.append))
        self.stack.enter_context(mock.patch.object(ai, "AI_NONSTREAM_USE_PROVIDER_STREAM", False))
        self.transport = httpx.MockTransport(self.respond)
        self.real_http = httpx.AsyncClient
        self.real_ark = ai.AsyncArk
        self.real_openai = ai.AsyncOpenAI
        self.stack.enter_context(mock.patch.object(ai, "AsyncArk", side_effect=self.ark_client))
        self.stack.enter_context(mock.patch.object(ai, "AsyncOpenAI", side_effect=self.openai_client))

    def ark_client(self, **kwargs):
        return self.real_ark(**kwargs, http_client=self.real_http(transport=self.transport))

    def openai_client(self, **kwargs):
        return self.real_openai(**kwargs, http_client=self.real_http(transport=self.transport))

    def respond(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        usage = {"prompt_tokens": 1000, "completion_tokens": 200,
                 "total_tokens": 1200, "prompt_tokens_details": {"cached_tokens": 250},
                 "completion_tokens_details": {"reasoning_tokens": 100}}
        if request.url.path.endswith("/responses"):
            result = {"id": "resp-test", "status": self.response_status, "output_text": json.dumps(GRADE)}
            if self.include_usage:
                result["usage"] = usage
            return httpx.Response(200, json=result)
        result = {"id": "chat-test", "object": "chat.completion", "created": 1, "model": body["model"],
                  "choices": [{"index": 0, "finish_reason": self.finish, "message": {"role": "assistant", "content": json.dumps(GRADE)}}]}
        if self.include_usage:
            result["usage"] = usage
        if body.get("stream"):
            first = {**result, "object": "chat.completion.chunk", "choices": [{"index": 0, "finish_reason": None, "delta": {"role": "assistant", "content": json.dumps(GRADE)}}]}
            first.pop("usage", None)
            last = {**result, "object": "chat.completion.chunk", "choices": [{"index": 0, "finish_reason": self.finish, "delta": {}}]}
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in (first, last)) + "data: [DONE]\n\n")
        return httpx.Response(200, json=result)

    async def test_nonstream_sends_profile_and_preserves_payload_and_catalog(self):
        before = json.dumps(ai.PLATFORMS_CONFIG, sort_keys=True)
        metadata = {}
        result = await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
            business_context={"operation": "grading", "assessment_kind": "homework"}, require_json_output=True, metadata_out=metadata)
        self.assertEqual(80, result["score"])
        body = self.requests[0]
        self.assertEqual((DOUBAO_PRO_MODEL, "low", 8192), (body["model"], body["reasoning_effort"], body["max_completion_tokens"]))
        self.assertEqual({"type": "enabled"}, body["thinking"])
        self.assertNotIn("max_tokens", body)
        self.assertEqual(IMAGE_MESSAGES, body["messages"])
        self.assertEqual("stop", metadata["finish_reason"])
        self.assertEqual("vision_pro_low", metadata["profile_id"])
        self.assertEqual(before, json.dumps(ai.PLATFORMS_CONFIG, sort_keys=True))
        self.assertAlmostEqual(0.0108, self.events[-1]["cost_estimate"]["estimated_cost"])

    async def test_stream_uses_same_adapter_and_actual_usage(self):
        result = [json.loads(event) async for event in ai._call_ai_platform_chat_stream_events("System", IMAGE_MESSAGES,
            capability="vision", task_type=AI_TASK_VISION_INTERACTIVE, business_context={"operation": "chat", "source_feature": "blog"})]
        body = self.requests[0]
        self.assertEqual({"include_usage": True}, body["stream_options"])
        self.assertEqual((DOUBAO_LITE_MODEL, "low", 4096), (body["model"], body["reasoning_effort"], body["max_completion_tokens"]))
        self.assertTrue(body["stream"])
        done = next(event for event in result if event["event"] == "done")
        self.assertTrue(done["complete"])
        self.assertTrue(done["execution_metadata"]["usage_known"])
        self.assertEqual({}, ai.ai_model_router.snapshot())

    async def test_stream_disconnect_releases_provider_and_global_slots(self):
        stream = ai._call_ai_platform_chat_stream_events("System", IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_VISION_INTERACTIVE)
        while True:
            event = json.loads(await anext(stream))
            if event["event"] == "answer_delta":
                break
        await stream.aclose()
        self.assertEqual({}, ai.ai_model_router.snapshot())
        self.assertEqual(0, ai.ai_limiter._running)
        self.assertEqual("error", self.events[-1]["status"])

    async def test_high_sub_limit_and_aging_respect_provider_total(self):
        context = {"operation": "grading", "assessment_kind": "final"}
        high = ai._build_model_routes("vision", task_type=AI_TASK_MULTIMODAL_GRADING, business_context=context)[0]
        low = ai._build_model_routes("vision", task_type=AI_TASK_MULTIMODAL_GRADING,
            business_context={**context, "assessment_kind": "homework"})[0]
        router = ai.ai_model_router
        selected, reservation = await router.choose_and_reserve([high], task_priority="default")
        self.assertIsNone(router._choose_route_locked([high], task_priority="default"))
        router._interactive_waiters = 1
        self.assertIsNone(router._choose_route_locked([low], task_priority="default"))
        self.assertEqual(low, router._choose_route_locked([low], task_priority="default", waited_seconds=301))
        self.assertEqual(low, router._choose_route_locked([low], task_priority="interactive"))
        await router.release(selected, reservation)
        await router.release(selected, reservation)
        self.assertEqual(0, router._high_reserved)

    async def test_stream_closing_at_done_has_already_released_capacity(self):
        generator = ai._call_ai_platform_chat_stream_events("System", IMAGE_MESSAGES,
            capability="vision", task_type=AI_TASK_VISION_INTERACTIVE,
            business_context={"operation": "chat", "source_feature": "blog"})
        async for event in generator:
            if json.loads(event).get("type") == "done":
                break
        await generator.aclose()
        self.assertEqual(0, ai.ai_model_router._reserved_by_platform.get("volcengine", 0))
        self.assertFalse(ai.ai_model_router._reservations)

    async def test_responses_sends_its_own_fields_and_rejects_incomplete(self):
        payload = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}]
        with mock.patch.object(ai.httpx, "AsyncClient", side_effect=lambda **kwargs: self.real_http(**kwargs, transport=self.transport)):
            result = await ai._call_volcengine_responses_api(model_name=DOUBAO_PRO_MODEL, api_key="unit-test-only", input_payload=payload,
                capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING, business_context={"operation": "grading", "assessment_kind": "final"})
            self.assertEqual(80, result["score"])
            self.assertEqual({"effort": "high"}, self.requests[0]["reasoning"])
            self.assertEqual(16384, self.requests[0]["max_output_tokens"])
            self.assertNotIn("max_completion_tokens", self.requests[0])
            self.response_status = "incomplete"
            with self.assertRaises(ValueError):
                await ai._call_volcengine_responses_api(model_name=DOUBAO_PRO_MODEL, api_key="unit-test-only", input_payload=payload,
                    capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING, business_context={"operation": "grading", "assessment_kind": "final"})

    async def test_plain_text_exam_remains_deepseek_and_no_cross_provider_fallback(self):
        await ai._call_ai_platform([{"role": "user", "content": "Text exam"}], capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
            business_context={"operation": "grading", "assessment_kind": "final"}, require_json_output=True)
        self.assertEqual("deepseek-v4-pro", self.requests[0]["model"])
        self.assertEqual("max", self.requests[0]["reasoning_effort"])
        self.assertEqual(16384, self.requests[0]["max_tokens"])
        with self.assertRaises(ValueError):
            await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", preferred_platform="qwen")

    async def test_length_not_success_and_missing_usage_not_free(self):
        self.finish = "length"
        with self.assertRaises(ai.HTTPException):
            await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
                business_context={"operation": "grading", "assessment_kind": "homework"}, require_json_output=True)
        self.assertEqual(1, len(self.requests))
        self.assertEqual("error", self.events[-1]["status"])
        self.finish = "stop"
        self.include_usage = False
        await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_VISION_INTERACTIVE)
        self.assertFalse(self.events[-1]["cost_known"])
        self.assertNotIn("cost_estimate", self.events[-1])

    async def test_shared_budget_limits_repair_and_adjudication_to_two_generations(self):
        budget = ai.AIExecutionBudget()
        token = ai._active_execution_budget.set(budget)
        try:
            for kind in ("homework", "midterm"):
                await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
                    business_context={"operation": "grading", "assessment_kind": kind})
            with self.assertRaises(Exception):
                await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
                    business_context={"operation": "grading", "assessment_kind": "homework"})
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(2, len(self.requests))
        self.assertEqual(["low", "high"], [a["reasoning_effort"] for a in budget.state["attempts"]])
        self.assertTrue(all(a["cost_known"] for a in budget.state["attempts"]))

    async def test_transport_rejections_allow_three_http_attempts_and_no_fourth(self):
        attempts = []
        def reject(request):
            attempts.append(request)
            return httpx.Response(429, json={"error": {"message": "rate limited", "type": "rate_limit_error"}})
        self.transport = httpx.MockTransport(reject)
        budget = ai.AIExecutionBudget()
        token = ai._active_execution_budget.set(budget)
        try:
            with mock.patch.object(ai, "_provider_retry_delay_seconds", return_value=0):
                with self.assertRaises(Exception):
                    await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
                        business_context={"operation": "grading", "assessment_kind": "homework"})
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(3, len(attempts))
        self.assertEqual(["rejected"] * 3, [a["status"] for a in budget.state["attempts"]])

    async def test_unknown_timeout_survives_resume_and_cannot_reset_budget(self):
        budget = ai.AIExecutionBudget()
        plan = resolve_execution_plan(AI_TASK_MULTIMODAL_GRADING, "vision", {"assessment_kind": "homework"}).to_dict()
        await budget.begin(plan)  # Simulates a process dying after dispatch.
        resumed = ai.AIExecutionBudget(budget.snapshot())
        async def timeout():
            raise httpx.ReadTimeout("unknown delivery")
        with mock.patch.object(ai, "_provider_retry_delay_seconds", return_value=0):
            with self.assertRaises(httpx.ReadTimeout):
                await ai._provider_call_with_retry(timeout, platform_name="volcengine", execution_budget=resumed, execution_plan=plan)
        self.assertEqual(["pending", "unknown"], [a["status"] for a in resumed.state["attempts"]])
        self.assertTrue(resumed.exhausted)
        self.assertTrue(all(a["cost_estimate_cny"] is None for a in resumed.state["attempts"]))

    async def test_failed_persistence_sends_nothing(self):
        async def persist(state, revision):
            raise RuntimeError("lease changed")
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(persist=persist))
        try:
            with self.assertRaises(Exception):
                await ai._call_ai_platform(IMAGE_MESSAGES, capability="vision", task_type=AI_TASK_MULTIMODAL_GRADING,
                    business_context={"operation": "grading", "assessment_kind": "homework"})
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual([], self.requests)

    def review_fixture(self, *, high_error=False, assessment_kind="homework", low_format_error=False):
        import base64
        import sqlite3
        import tempfile
        from pathlib import Path
        from classroom_app.services import ai_usage_budget_service as quota
        from classroom_app.db.schema_ai_jobs import AI_JOB_POSTGRES_RUNTIME_TABLES
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        db_path = directory / "review.db"
        def connect():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return contextlib.closing(conn)
        with connect() as conn:
            for name in ("ai_review_daily_counters", "ai_review_reservations"):
                conn.execute(AI_JOB_POSTGRES_RUNTIME_TABLES[name])
            conn.commit()
        self.stack.enter_context(mock.patch.object(quota, "get_db_connection", side_effect=connect))
        self.stack.enter_context(mock.patch.object(quota, "get_configured_db_engine", return_value="sqlite"))
        self.stack.enter_context(mock.patch.object(ai, "AI_GRADING_ADJUDICATION_ENABLED", True))
        self.stack.enter_context(mock.patch.object(ai, "AI_GRADING_ADJUDICATION_GLOBAL_DAILY_LIMIT", 10))
        self.stack.enter_context(mock.patch.object(ai, "AI_GRADING_ADJUDICATION_OFFERING_DAILY_LIMIT", 3))
        image = directory / "answer.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        job = ai.GradingJob(submission_id=71, rubric_md="按截图证据评分，总分100。",
            files=[ai.GradingFile(stored_path=str(image), original_filename="answer.png", mime_type="image/png")],
            business_context={"operation": "grading", "assessment_kind": assessment_kind,
                "class_offering_id": 4, "logical_call_id": "review-integration"})
        def respond(request):
            body = json.loads(request.content)
            high_review = body.get("reasoning_effort") == "high" and job.business_context["assessment_kind"] == "homework"
            if high_review:
                with connect() as conn:
                    self.assertEqual("sent", conn.execute("SELECT status FROM ai_review_reservations").fetchone()[0])
            if high_review and high_error:
                self.requests.append(body)
                raise httpx.ReadTimeout("unknown generation result", request=request)
            response = self.respond(request)
            raw = response.json()
            result = json.loads(json.dumps(GRADE))
            result["questions"][0].update({"evaluation": "截图中的一项细节仍需完善", "deduction_points": "细节不足"})
            result["confidence"] = 0.95 if high_review else 0.4
            if not high_review and low_format_error:
                result["questions"][0]["evaluation"] = ""
            if high_review:
                result["score"] = result["questions"][0]["score"] = 85
            raw["choices"][0]["message"]["content"] = json.dumps(result)
            return httpx.Response(200, json=raw)
        self.transport = httpx.MockTransport(respond)
        return job, connect, quota

    async def test_combined_high_repair_reserves_once_and_resume_sends_nothing(self):
        job, connect, quota = self.review_fixture(low_format_error=True)
        budget = ai.AIExecutionBudget(logical_call_id="combined-repair")
        token = ai._active_execution_budget.set(budget)
        try:
            result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(85, result["score"])
        self.assertFalse(result["review_required"])
        self.assertEqual(["low", "high"], [r["reasoning_effort"] for r in self.requests])
        self.assertTrue(result["quality_audit"]["adjudication"]["combined_format_repair"])
        self.assertIsNone(result["quality_audit"]["adjudication"]["primary_score"])
        high_prompt = json.dumps(self.requests[1]["messages"], ensure_ascii=False)
        self.assertIn("缺少评价", high_prompt)
        self.assertIn("low_confidence", high_prompt)
        self.assertNotIn("repair_candidate", result["execution_state"])
        self.assertTrue(result["execution_state"]["has_repair_candidate"])
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(budget.snapshot()))
        try:
            replay = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(85, replay["score"])
        self.assertEqual(2, len(self.requests))
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT reserved_count FROM ai_review_daily_counters WHERE scope_type='global'").fetchone()[0])

    async def test_combined_repair_failure_never_accepts_invalid_primary_or_replays(self):
        job, connect, quota = self.review_fixture(low_format_error=True, high_error=True)
        budget = ai.AIExecutionBudget(logical_call_id="combined-failed")
        token = ai._active_execution_budget.set(budget)
        try:
            result = await ai._build_grading_callback_data(job)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual("grading_review_required", result["status"])
        self.assertIsNone(result["score"])
        self.assertEqual(["combined_repair_failed"], result["review_reason_codes"])
        self.assertEqual(2, len(self.requests))
        self.assertEqual("unknown", budget.state["attempts"][-1]["status"])
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(budget.snapshot()))
        try:
            replay = await ai._build_grading_callback_data(job)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertIsNone(replay["score"])
        self.assertEqual(2, len(self.requests))
        self.assertNotIn("primary_result", budget.state)

    async def test_combined_repair_quota_denial_does_not_fall_back_to_low_repair(self):
        job, connect, quota = self.review_fixture(low_format_error=True)
        for n in range(3):
            quota.reserve_grading_review(logical_call_id=f"other:{n}", class_offering_id=4, policy_version="test", reasons=["risk"])
        result = await ai._build_grading_callback_data(job)
        self.assertIsNone(result["score"])
        self.assertEqual("grading_review_required", result["status"])
        self.assertEqual(["combined_repair_offering_daily_limit"], result["review_reason_codes"])
        self.assertEqual(1, len(self.requests))

    async def test_combined_repair_429_retry_shares_three_http_slots_and_one_reservation(self):
        job, connect, quota = self.review_fixture(low_format_error=True)
        original_transport = self.transport
        high_requests = 0
        async def respond(request):
            nonlocal high_requests
            body = json.loads(request.content)
            if body.get("reasoning_effort") == "high":
                high_requests += 1
                if high_requests == 1:
                    self.requests.append(body)
                    return httpx.Response(429, json={"error": {"message": "busy"}})
            return await original_transport.handle_async_request(request)
        self.transport = httpx.MockTransport(respond)
        budget = ai.AIExecutionBudget(logical_call_id="combined-429")
        token = ai._active_execution_budget.set(budget)
        try:
            with mock.patch.object(ai, "_provider_retry_delay_seconds", return_value=0):
                result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(85, result["score"])
        self.assertEqual(["low", "high", "high"], [r["reasoning_effort"] for r in self.requests])
        self.assertEqual(["completed", "rejected", "completed"], [a["status"] for a in budget.state["attempts"]])
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM ai_review_reservations").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT reserved_count FROM ai_review_daily_counters WHERE scope_type='global'").fetchone()[0])

    async def test_question_size_uses_server_scoring_snapshot_and_actual_transport_cap(self):
        job, connect, quota = self.review_fixture()
        job.business_context["expected_question_count"] = 1  # Teacher snapshot overrides this hint.
        job.exam_scoring_json = json.dumps({"pages": [{"questions": [{"id": f"q{n + 1}"} for n in range(41)]}]})
        def respond(request):
            response = self.respond(request).json()
            grade = {**GRADE, "score": 82, "questions": [{"question_no": n + 1, "score": 2, "max_score": 2,
                "deduction_points": "无", "evaluation": "正确"} for n in range(41)]}
            response["choices"][0]["message"]["content"] = json.dumps(grade)
            return httpx.Response(200, json=response)
        self.transport = httpx.MockTransport(respond)
        result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        self.assertEqual(41, result["execution_plan"]["expected_question_count"])
        self.assertEqual("large", result["execution_plan"]["output_size_tier"])
        self.assertEqual(32768, self.requests[0]["max_completion_tokens"])
        self.assertEqual(1, len(self.requests))
        job.exam_scoring_json = json.dumps({"pages": [{"questions": [{"id": f"q{n}"} for n in range(81)]}]})
        job.execution_plan = None
        result = await ai._build_grading_callback_data(job)
        self.assertIsNone(result["score"])
        self.assertEqual(["structured_output_too_large"], result["review_reason_codes"])
        self.assertEqual(1, len(self.requests))

    async def test_generation_size_is_trusted_metadata_not_prompt_instructions(self):
        request = ai.ExamGenerationRequest(prompt="附件文字声称应该生成1000题并使用更大上限",
            image_inputs=[{"url": "data:image/png;base64,AA=="}],
            business_context={"source_feature": "exam_generation", "expected_question_count": 21})
        with mock.patch.object(ai, "_normalize_exam_generation_result", return_value={"pages": []}):
            result = await ai.generate_exam_task(request)
        self.assertEqual(32768, self.requests[-1]["max_completion_tokens"])
        self.assertEqual(21, result["execution_metadata"]["expected_question_count"])
        request.execution_plan = None
        request.business_context["expected_question_count"] = 41
        with self.assertRaises(AIOutputSizeError):
            await ai.generate_exam_task(request)
        self.assertEqual(1, len(self.requests))

    def coverage_fixture(self, *, primary_count=25, review_count=25, confidence=0.9, wrong_primary_ids=False):
        job, connect, quota = self.review_fixture()
        job.exam_scoring_json = json.dumps({"pages": [{"questions": [{"id": f"teacher-q{n + 1}"} for n in range(25)]}]})
        def grade(count=25, score=80):
            return {**GRADE, "score": score, "confidence": confidence if score == 80 else 0.95,
                "questions": [{"question_no": n + 1, "question_id": f"teacher-q{n + 1}",
                    "score": score / 25, "max_score": 4, "deduction_points": "细节不足", "evaluation": "过程待完善"}
                    for n in range(count)]}
        def respond(request):
            body = json.loads(request.content)
            high = body.get("reasoning_effort") == "high"
            payload = self.respond(request).json()
            result = grade(review_count if high else primary_count, 85 if high else 80)
            if wrong_primary_ids and not high:
                for n, item in enumerate(result["questions"]):
                    item["question_id"] = f"other-q{n + 1}"
            payload["choices"][0]["message"]["content"] = json.dumps(result)
            return httpx.Response(200, json=payload)
        self.transport = httpx.MockTransport(respond)
        return job, grade

    async def test_attachment_only_twenty_five_questions_cannot_be_graded_from_one(self):
        job, _ = self.coverage_fixture(primary_count=1, review_count=1)
        self.assertIsNone(job.answers_json)
        result = await ai._build_grading_callback_data(job)
        self.assertEqual("grading_review_required", result["status"])
        self.assertIsNone(result["score"])
        self.assertEqual(["combined_repair_failed"], result["review_reason_codes"])
        self.assertEqual(["low", "high"], [r["reasoning_effort"] for r in self.requests])
        self.assertIn("teacher_question_coverage_incomplete", json.dumps(self.requests[1]["messages"]))

    async def test_full_teacher_question_id_coverage_is_accepted(self):
        job, _ = self.coverage_fixture()
        result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        self.assertEqual(("graded", 80, False), (result["status"], result["score"], result["review_required"]))
        self.assertEqual(1, len(self.requests))
        self.assertEqual(25, result["execution_plan"]["expected_question_count"])

    async def test_matching_count_with_wrong_question_ids_requires_high_repair(self):
        job, _ = self.coverage_fixture(wrong_primary_ids=True)
        result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        self.assertEqual(85, result["score"])
        self.assertTrue(result["quality_audit"]["adjudication"]["combined_format_repair"])
        self.assertEqual(["low", "high"], [r["reasoning_effort"] for r in self.requests])

    async def test_partial_high_review_preserves_complete_primary_on_resume(self):
        job, _ = self.coverage_fixture(confidence=0.4, review_count=1)
        budget = ai.AIExecutionBudget(logical_call_id="coverage-primary-protection")
        token = ai._active_execution_budget.set(budget)
        try:
            result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual((80, True), (result["score"], result["review_required"]))
        self.assertFalse(result["quality_audit"]["adjudication"]["succeeded"])
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(budget.snapshot()))
        try:
            replay = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual((80, True), (replay["score"], replay["review_required"]))
        self.assertEqual(2, len(self.requests))

    async def test_previously_cached_partial_grade_is_rejected_without_more_calls(self):
        job, grade = self.coverage_fixture()
        state = {"version": 1, "revision": 1, "logical_call_id": "old-result", "primary_plan": None,
            "attempts": [{"attempt_id": "already-billed", "status": "completed"}],
            "primary_result": {"result": grade(count=1), "execution_metadata": {}}}
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(state))
        try:
            result = await ai._build_grading_callback_data(job)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual("grading_review_required", result["status"])
        self.assertIsNone(result["score"])
        self.assertEqual(["incomplete_question_coverage"], result["review_reason_codes"])
        self.assertEqual([], self.requests)

    async def test_cached_partial_high_keeps_valid_primary_and_cached_valid_high_can_rescue_old_partial(self):
        job, grade = self.coverage_fixture()
        for primary_count, high_count, score, review in ((25, 1, 80, True), (1, 25, 85, False)):
            with self.subTest(primary_count=primary_count, high_count=high_count):
                state = {"version": 1, "revision": 2, "logical_call_id": "old-results", "primary_plan": None,
                    "attempts": [{"attempt_id": "primary", "status": "completed"}, {"attempt_id": "review", "status": "completed"}],
                    "primary_result": {"result": grade(primary_count), "execution_metadata": {}},
                    "review_result": {"result": grade(high_count, 85), "execution_metadata": {}}}
                token = ai._active_execution_budget.set(ai.AIExecutionBudget(state))
                try:
                    result = await ai._build_grading_callback_data(job, raise_on_failure=True)
                finally:
                    ai._active_execution_budget.reset(token)
                self.assertEqual((score, review), (result["score"], result["review_required"]))
        self.assertEqual([], self.requests)

    async def test_low_risk_uses_one_reserved_high_and_resume_reuses_validated_results(self):
        job, connect, quota = self.review_fixture()
        persisted = []
        async def persist(state, revision):
            persisted.append(state)
        budget = ai.AIExecutionBudget(logical_call_id="review-integration", persist=persist)
        token = ai._active_execution_budget.set(budget)
        try:
            result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(85, result["score"])
        self.assertFalse(result["review_required"])
        self.assertEqual(["low", "high"], [r["reasoning_effort"] for r in self.requests])
        def images(request):
            return [part["image_url"]["url"] for message in request["messages"]
                for part in (message.get("content") if isinstance(message.get("content"), list) else [])
                if part.get("type") == "image_url"]
        self.assertTrue(images(self.requests[0]))
        self.assertEqual(images(self.requests[0]), images(self.requests[1]))
        self.assertTrue(result["quality_audit"]["adjudication"]["succeeded"])
        self.assertNotIn("primary_result", result["execution_state"])
        self.assertNotIn("review_result", result["execution_state"])
        self.assertTrue(result["execution_state"]["has_primary_result"])
        self.assertTrue(all("primary_result" not in event.get("extra", {}).get("execution_state", {}) for event in self.events))
        self.assertTrue(any(s.get("primary_result") and len(s["attempts"]) == 1 for s in persisted))
        with connect() as conn:
            self.assertEqual("completed", conn.execute("SELECT status FROM ai_review_reservations").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT reserved_count FROM ai_review_daily_counters WHERE scope_type='global'").fetchone()[0])
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(budget.snapshot()))
        try:
            replay = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual(85, replay["score"])
        self.assertTrue(replay["quality_audit"]["adjudication"]["succeeded"])
        self.assertEqual(2, len(self.requests))

    async def test_high_failure_keeps_valid_primary_and_unknown_attempt_on_resume(self):
        job, connect, quota = self.review_fixture(high_error=True)
        budget = ai.AIExecutionBudget(logical_call_id="review-integration")
        token = ai._active_execution_budget.set(budget)
        try:
            result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual((80, True), (result["score"], result["review_required"]))
        self.assertEqual(2, len(self.requests))
        self.assertEqual("unknown", budget.state["attempts"][-1]["status"])
        self.assertIsNone(budget.state["attempts"][-1]["cost_estimate_cny"])
        token = ai._active_execution_budget.set(ai.AIExecutionBudget(budget.snapshot()))
        try:
            replay = await ai._build_grading_callback_data(job, raise_on_failure=True)
        finally:
            ai._active_execution_budget.reset(token)
        self.assertEqual((80, True), (replay["score"], replay["review_required"]))
        self.assertEqual(2, len(self.requests))
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT reserved_count FROM ai_review_daily_counters WHERE scope_type='global'").fetchone()[0])

    async def test_quota_denied_keeps_primary_and_high_assessment_does_not_reserve(self):
        job, connect, quota = self.review_fixture()
        for n in range(3):
            quota.reserve_grading_review(logical_call_id=f"other:{n}", class_offering_id=4,
                policy_version="test", reasons=["low_confidence"])
        result = await ai._build_grading_callback_data(job, raise_on_failure=True)
        self.assertEqual((80, True), (result["score"], result["review_required"]))
        self.assertIn("offering_daily_limit", result["review_reason_codes"])
        self.assertEqual(1, len(self.requests))
        job.business_context["assessment_kind"] = "midterm"
        job.business_context["logical_call_id"] = "midterm"
        job.execution_plan = None
        with mock.patch.object(ai, "reserve_grading_review", side_effect=AssertionError("High primary must not reserve")):
            high = await ai._build_grading_callback_data(job, raise_on_failure=True)
        self.assertEqual(2, len(self.requests))
        self.assertEqual("high", self.requests[-1]["reasoning_effort"])
        self.assertTrue(high["review_required"])

    def test_blank_answer_claim_with_valid_image_is_an_explainable_risk(self):
        result = {**GRADE, "confidence": 0.99, "summary": "未提交答案"}
        self.assertIn("blank_answer_claim_with_valid_attachment", ai._grading_adjudication_reasons(
            result, image_count=1, format_repair_required=False, answers_empty=True))
        self.assertNotIn("blank_answer_claim_with_valid_attachment", ai._grading_adjudication_reasons(
            result, image_count=0, format_repair_required=False, answers_empty=True))

    async def test_missing_or_incomplete_grading_documents_never_call_model(self):
        import tempfile
        from pathlib import Path
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        job = ai.GradingJob(submission_id=91, rubric_md="按完整证据批改",
            files=[ai.GradingFile(stored_path=str(directory / "missing.png"))], answers_json='{"answers":[]}')
        result = await ai._build_grading_callback_data(job)
        self.assertEqual("grading_review_required", result["status"])
        self.assertEqual([], self.requests)
        document = directory / "answer.docx"
        document.write_bytes(b"synthetic")
        job.files = [ai.GradingFile(stored_path=str(document))]
        for extracted in (ai._ExtractResult(text="visible", issues=["文档图片超过提取数量上限"]),
                          ai._ExtractResult(text="partial", truncated=True), ai._ExtractResult()):
            with mock.patch.object(ai, "_extract_doc_text", return_value=extracted):
                result = await ai._build_grading_callback_data(job)
            self.assertEqual("grading_review_required", result["status"])
            self.assertIn("incomplete_grading_evidence", result["review_reason_codes"])
        plain_text = directory / "answer.txt"
        plain_text.write_text("full answer requires more than four bytes", encoding="utf-8")
        job.files = [ai.GradingFile(stored_path=str(plain_text))]
        with mock.patch.object(ai, "AI_GRADING_MAX_RAW_TEXT_FILE_BYTES", 4):
            result = await ai._build_grading_callback_data(job)
        self.assertEqual("grading_review_required", result["status"])
        self.assertIn("incomplete_grading_evidence", result["review_reason_codes"])
        self.assertEqual([], self.requests)

    async def test_pdf_page_limit_and_failed_page_never_silently_grade_partial_pages(self):
        import tempfile
        from pathlib import Path
        from ai_assistant_doc_extract import fitz
        if fitz is None:
            self.skipTest("PyMuPDF is unavailable")
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        pdf = directory / "answer.pdf"
        with fitz.open() as doc:
            for _ in range(3):
                doc.new_page()
            doc.save(str(pdf))
        job = ai.GradingJob(submission_id=92, rubric_md="按完整页面批改", files=[ai.GradingFile(stored_path=str(pdf))])
        with mock.patch.object(ai, "AI_GRADING_MAX_RENDERED_PDF_PAGES", 2), mock.patch.object(ai, "_render_pdf_pages") as render:
            result = await ai._build_grading_callback_data(job)
            render.assert_not_called()
        self.assertEqual("grading_review_required", result["status"])
        partial = [{"filename": "page_1.png", "data_url": "data:image/png;base64,AA=="},
                   {"filename": "page_3.png", "data_url": "data:image/png;base64,AA=="}]
        with mock.patch.object(ai, "_render_pdf_pages", return_value=partial) as render:
            result = await ai._build_grading_callback_data(job)
            render.assert_called_once_with(pdf, max_pages=3)
        self.assertEqual("grading_review_required", result["status"])
        self.assertEqual([], self.requests)

    def test_lite_tiers_and_unknown_cache_are_explicit(self):
        estimate = ai._estimate_provider_cost_cny("volcengine", {"prompt_tokens": 32001, "completion_tokens": 1000}, model_name=DOUBAO_LITE_MODEL)
        self.assertEqual("32-128k", estimate["price_tier"])
        self.assertFalse(estimate["cache_usage_reported"])
        self.assertIsNone(estimate["cached_input_tokens"])
        self.assertAlmostEqual(0.0342009, estimate["estimated_cost"])
        self.assertIsNone(ai._estimate_provider_cost_cny("volcengine", {"prompt_tokens": 1}, model_name=DOUBAO_LITE_MODEL))


if __name__ == "__main__":
    unittest.main()
