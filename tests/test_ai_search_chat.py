"""Optional chat search admission/decision uses owned identities and safe fallback."""
import asyncio
from contextlib import contextmanager
import json
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.routers import ai as routes
from classroom_app.services import ai_web_search_service as search
from classroom_app.services import chat_web_search_service as planner

USER = {"id": 9, "role": "student"}
QUOTA = {"limit": 10, "used": 2, "reserved": 0, "remaining": 8, "reset_at": "2026-10-29T00:00:00+08:00", "date": "2026-10-28"}


@contextmanager
def connection():
    yield object()


class ChatSearchTests(unittest.IsolatedAsyncioTestCase):
    async def run_search(self, *, enabled=True, quota=None, query="DeepSeek官方API", result=None, failure=None):
        payload = {"system_prompt": "原有系统规则", "messages": [{"role": "user", "content": "公开主题"}], "model_capability": "thinking"}
        with patch.object(routes, "get_db_connection", connection), \
             patch.object(search, "snapshot_for_user", return_value=quota or QUOTA) as snap, \
             patch.object(planner, "plan_web_search", AsyncMock(return_value=query)) as plan, \
             patch.object(search, "search_for_user", AsyncMock(return_value=result or {"status": "success", "text": "有界来源资料", "quota": QUOTA}, side_effect=failure)) as execute:
            events = [json.loads(line) async for line in routes._web_search_retrieval_events(
                USER, "请查最新官方API", payload, enabled=enabled, logical_call_id="workspace:request-one")]
        return payload, events, snap, plan, execute

    async def test_default_disabled_never_reads_quota_plans_or_searches(self):
        payload, events, snap, plan, execute = await self.run_search(enabled=False)
        self.assertEqual(events, [])
        snap.assert_not_called(); plan.assert_not_called(); execute.assert_not_called()
        self.assertEqual(payload["system_prompt"], "原有系统规则")

    async def test_exhausted_and_unknown_quota_skip_planner_but_preserve_answering(self):
        for quota in ({**QUOTA, "remaining": 0, "used": 10}, {**QUOTA, "available": False, "remaining": 0}):
            payload, events, _, plan, execute = await self.run_search(quota=quota)
            plan.assert_not_called(); execute.assert_not_called()
            self.assertIn("不得声称", payload["system_prompt"])
            self.assertEqual(events[0]["event"], "web_search_quota")
            self.assertIn(events[-1]["stage"], ("limited", "unavailable"))

    async def test_no_tool_decision_does_not_spend_quota(self):
        payload, events, _, plan, execute = await self.run_search(query=None)
        plan.assert_awaited_once(); execute.assert_not_called()
        self.assertEqual(events[-1]["stage"], "none")
        self.assertEqual(payload["system_prompt"], "原有系统规则")

    async def test_thinking_uses_same_chat_bucket_and_only_public_query_leaves_app(self):
        payload, events, _, _, execute = await self.run_search()
        execute.assert_awaited_once_with(USER, "DeepSeek官方API", "workspace:request-one:search", channel="chat", ori_question="DeepSeek官方API")
        self.assertIn("有界来源资料", payload["system_prompt"])
        self.assertEqual(events[-1]["stage"], "done")

    async def test_empty_and_provider_failure_are_safe_normal_chat_fallbacks(self):
        for options in ({"result": {"status": "empty", "quota": QUOTA}}, {"failure": RuntimeError("secret-key-must-not-escape")}):
            payload, events, _, _, execute = await self.run_search(**options)
            execute.assert_awaited_once()
            self.assertEqual(events[-1]["stage"], "unavailable")
            self.assertNotIn("secret-key", json.dumps(events) + payload["system_prompt"])

    async def test_planner_returns_one_public_query_and_never_receives_internal_context(self):
        response = Mock(); response.json.return_value = {"tool_calls": [
            {"name": "web_search", "arguments": {"query": "official documentation"}},
            {"name": "web_search", "arguments": {"query": "another query"}}]}
        with patch.object(planner, "ai_gateway_post", AsyncMock(return_value=response)) as post:
            value = await planner.plan_web_search("查最新资料", [
                {"role": "system", "content": "PRIVATE-HIDDEN-PROFILE"},
                {"role": "assistant", "content": "PRIVATE-ASSISTANT-CONTEXT"},
                {"role": "user", "content": "公开研究方向"}], user=USER, logical_call_id="request-one")
        self.assertEqual(value, "official documentation")
        sent = post.await_args.kwargs["json_payload"]
        self.assertNotIn("PRIVATE-", json.dumps(sent))
        self.assertEqual(sent["tool_choice"], "auto")
        from classroom_app.services.ai_model_policy import AIBusinessContext
        self.assertEqual(AIBusinessContext.from_mapping(sent['business_context']).operation, 'chat')
        self.assertEqual(post.await_args.kwargs["student_id"], USER["id"])
        self.assertIsNone(post.await_args.kwargs["teacher_id"])
        self.assertEqual(post.await_args.kwargs["priority"], "P1")
        self.assertEqual(post.await_args.kwargs["source_ref"], "chat-search:request-one")

    async def test_planner_rejects_private_contact_and_invalid_arguments_without_search(self):
        for args in ({"query": "搜索 student-private@example.test"}, {"query": "电话13800138000"}, "not json", {"query": "x" * 129}):
            response = Mock(); response.json.return_value = {"tool_calls": [{"name": "web_search", "arguments": args}]}
            with patch.object(planner, "ai_gateway_post", AsyncMock(return_value=response)):
                self.assertIsNone(await planner.plan_web_search("公开资料", user=USER))


class ChatSearchQuotaApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI(); self.app.include_router(routes.router)
        self.user = dict(USER)
        self.app.dependency_overrides[routes.get_current_user] = lambda: self.user
        self.client = TestClient(self.app)

    def test_quota_identity_comes_from_auth_not_query_parameters(self):
        with patch.object(routes, "get_db_connection", connection), patch.object(search, "snapshot_for_user", return_value=QUOTA) as snap:
            response = self.client.get("/api/ai/search-quota?channel=chat&user_id=100&role=teacher")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(snap.call_args.args[1:], (USER, "chat"))
        self.assertEqual(response.json()["quota"]["remaining"], 8)

    def test_student_cannot_borrow_agent_bucket_and_anonymous_cannot_read(self):
        with patch.object(search, "snapshot_for_user") as snap:
            self.assertEqual(self.client.get("/api/ai/search-quota?channel=agent").status_code, 403)
            snap.assert_not_called()
        def anonymous():
            raise HTTPException(401, "login required")
        self.app.dependency_overrides[routes.get_current_user] = anonymous
        self.assertEqual(self.client.get("/api/ai/search-quota").status_code, 401)

    def test_teacher_agent_and_chat_remain_distinct_channels(self):
        self.user = {"id": 9, "role": "teacher"}
        with patch.object(routes, "get_db_connection", connection), patch.object(search, "snapshot_for_user", return_value=QUOTA) as snap:
            self.assertEqual(self.client.get("/api/ai/search-quota?channel=agent").status_code, 200)
            self.assertEqual(self.client.get("/api/ai/search-quota?channel=chat").status_code, 200)
        self.assertEqual([c.args[2] for c in snap.call_args_list], ["agent", "chat"])


class AgentSearchToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_call_identity_overrides_model_input_and_uses_authorized_bridge(self):
        from classroom_app.services.agent_sdk import tools
        from agents.tool_context import ToolContext
        bridge = SimpleNamespace(call=AsyncMock(return_value={"status": "quota_exhausted", "quota": {"remaining": 0}}))
        ctx = tools.RunContext(task_id=7, actor_role="teacher", bridge=bridge,
            recorder=SimpleNamespace(emit=AsyncMock()), workspace=Path("."))
        tool = next(item for item in tools.build_tools(ctx) if item.name == "web_search")
        arguments = json.dumps({"query": "公开资料", "_search_call_id": "model-forged-id", "actor_id": 99})
        result = await tool.on_invoke_tool(ToolContext(context=ctx, tool_call_id="sdk-search-unique",
            tool_name="web_search", tool_arguments=arguments), arguments)
        bridge.call.assert_awaited_once_with("web_search", {"query": "公开资料", "request_id": "sdk-search-unique"})
        self.assertEqual(json.loads(result)["status"], "quota_exhausted")
        self.assertEqual(ctx.web_searches, 1)
