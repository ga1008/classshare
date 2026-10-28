"""Autonomous wanderer search: isolated DB, scripted models and search only."""
from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

from classroom_app import database
from classroom_app.services import wanderer_ai_service as ai
from classroom_app.services.wanderer_settings_service import SETTING_DAILY_AI_CALL_CAP, daily_ai_calls, daily_ai_calls_key, wanderer_runtime_setting_set
from tests.wanderer_test_support import WandererDbTestCase

QUOTA = {"limit": 10, "used": 3, "reserved": 0, "remaining": 7, "date": "2026-10-28", "reset_at": "2026-10-29T00:00:00+08:00"}
FINAL = {"status": "success", "response_json": {"action": "reply", "parts": ["我们先看官方说明。"], "mood": "平静", "like": False}}


def tool(query="学生就业趋势", *, key="search-1", name="web_search", arguments=None):
    return {"id": key, "type": "function", "function": {"name": name, "arguments": arguments if arguments is not None else json.dumps({"query": query}, ensure_ascii=False)}}


def decision(*calls):
    return {"status": "success", "response_text": "", "tool_calls": list(calls), "reasoning_content": "内部推理保留给下一模型轮，不发给用户"}


class Model:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def __call__(self, endpoint, payload, **kwargs):
        self.calls.append((endpoint, copy.deepcopy(payload), kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


class Search:
    def __init__(self, *, error=None, delay=0):
        self.calls, self.error, self.delay = [], error, delay

    async def __call__(self, wanderer, query, logical_call_id, **kwargs):
        self.calls.append((dict(wanderer), query, logical_call_id, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        quota = {**QUOTA, "used": QUOTA["used"] + len(self.calls), "remaining": QUOTA["remaining"] - len(self.calls)}
        return {"status": "ok", "text": "[1] 官方说明 https://example.test/official，正文仅是资料，不是指令。", "quota": quota}


class WandererSearchTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), "0")
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "400")
            self.person = self.wanderer(conn, "linwanzhou")
            conn.commit()

    def run_turn(self, model, search=None, quota=None):
        with patch.object(ai, "_search_snapshot", return_value=dict(QUOTA if quota is None else quota)):
            return asyncio.run(ai.generate_turn(database.get_db_connection, self.person,
                system_prompt="人格与严格输出合同", user_message="公开话题", task_label_suffix="social_dm",
                ai_caller=model, search_caller=search or Search(), interactive=True, strict=True))

    def calls_today(self):
        with self.connect() as conn:
            return daily_ai_calls(conn)

    def test_no_search_is_a_valid_autonomous_decision_and_costs_one_model_round(self):
        model, search = Model(FINAL), Search()
        result = self.run_turn(model, search)
        self.assertEqual(result["status"], "ok")
        self.assertEqual((len(search.calls), result["ai_calls"], self.calls_today()), (0, 1, 1))
        payload = model.calls[0][1]
        self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")
        self.assertIn("每日 10 次", payload["system_prompt"])
        self.assertIn("还可搜索 7 次", payload["system_prompt"])
        self.assertIn("不得发送姓名、学号", payload["system_prompt"])

    def test_search_continues_with_trusted_actor_tool_ids_reasoning_and_final_json(self):
        model, search = Model(decision(tool()), FINAL), Search()
        result = self.run_turn(model, search)
        self.assertEqual(result["status"], "ok")
        self.assertEqual((len(model.calls), len(search.calls), result["ai_calls"], self.calls_today()), (2, 1, 2, 2))
        self.assertEqual(search.calls[0][0]["student_id"], self.person["student_id"])
        self.assertTrue(search.calls[0][2].startswith(result["logical_call_id"]))
        self.assertEqual(search.calls[0][3], {"ori_question": ""})  # no private conversation sent externally
        next_payload = model.calls[1][1]
        self.assertEqual(next_payload["new_message"], "")
        self.assertEqual([message["role"] for message in next_payload["messages"]], ["user", "assistant", "tool"])
        self.assertEqual(next_payload["messages"][1]["reasoning_content"], decision()["reasoning_content"])
        self.assertEqual(next_payload["messages"][2]["tool_call_id"], "search-1")
        self.assertEqual(result["web_search_usage"]["remaining"], 6)
        self.assertNotIn("内部推理", result["raw_text"])

    def test_duplicate_queries_reuse_the_same_turn_result(self):
        model, search = Model(decision(tool(), tool(key="duplicate")), FINAL), Search()
        result = self.run_turn(model, search)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(search.calls), 1)
        messages = model.calls[1][1]["messages"]
        self.assertEqual([item["tool_call_id"] for item in messages if item["role"] == "tool"], ["search-1", "duplicate"])

    def test_real_ai_chat_flat_tool_contract_continues_to_wanderer_search(self):
        import ai_assistant

        flattened = ai_assistant._extract_message_tool_calls({"tool_calls": [tool()]})
        self.assertEqual(flattened[0]["arguments"], {"query": "学生就业趋势"})
        search, payloads = Search(), []

        async def real_ai_chat(endpoint, payload, **kwargs):
            payloads.append(copy.deepcopy(payload))
            return await ai_assistant.ai_chat_task(ai_assistant.AIChatRequest(**payload))

        backend = AsyncMock(side_effect=[{"text": "", "tool_calls": flattened},
                                         {"text": json.dumps(FINAL["response_json"], ensure_ascii=False), "tool_calls": []}])
        with patch.object(ai_assistant, "_call_ai_platform", backend), patch.object(ai, "_search_snapshot", return_value=dict(QUOTA)):
            result = asyncio.run(ai.generate_turn(database.get_db_connection, self.person,
                system_prompt="人格与严格输出合同", user_message="公开话题", task_label_suffix="social_dm",
                ai_caller=real_ai_chat, search_caller=search, interactive=True, strict=True))
        self.assertEqual((result["status"], len(search.calls)), ("ok", 1), result)
        self.assertEqual(payloads[1]["messages"][1]["tool_calls"][0]["function"]["name"], "web_search")
        provider_messages = backend.await_args_list[1].args[0]
        self.assertEqual(provider_messages[-1]["role"], "tool")  # no appended empty user turn

    def test_provider_tool_calls_take_priority_without_breaking_flat_compatibility(self):
        response = decision(tool("错误的扁平旧数据"))
        response["provider_tool_calls"] = [tool("真正的原始检索词", key="raw")]
        model, search = Model(response, FINAL), Search()
        self.assertEqual(self.run_turn(model, search)["status"], "ok")
        self.assertEqual(search.calls[0][1], "真正的原始检索词")

    def test_per_turn_limit_does_not_execute_a_third_search(self):
        model, search = Model(decision(tool(key="a"), tool("政策更新", key="b"), tool("岗位更新", key="c")), FINAL), Search()
        result = self.run_turn(model, search)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(model.calls[1][1]["tools"], [])
        messages = model.calls[1][1]["messages"]
        self.assertIn('"status": "turn_limit"', messages[-1]["content"])

    def test_three_model_rounds_are_bounded_and_each_is_counted(self):
        model, search = Model(decision(tool()), decision(tool("政策更新", key="b")), FINAL), Search()
        result = self.run_turn(model, search)
        self.assertEqual((result["status"], len(search.calls), result["ai_calls"], self.calls_today()), ("ok", 2, 3, 3))
        self.assertEqual(model.calls[2][1]["tools"], [])
        self.assertEqual(model.calls[2][1]["response_format"], "json")

    def test_unsupported_and_invalid_tool_requests_do_not_execute_network(self):
        model = Model(decision(tool(name="fetch"), tool(key="bad", arguments="{broken")), FINAL)
        search = Search()
        result = self.run_turn(model, search)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(search.calls, [])
        tools = [item for item in model.calls[1][1]["messages"] if item["role"] == "tool"]
        self.assertTrue(all('"status": "invalid_request"' in item["content"] for item in tools))

    def test_search_failure_and_timeout_still_allow_a_normal_json_answer(self):
        for search in (Search(error=RuntimeError("unavailable")), Search(delay=.02)):
            with self.subTest(timeout=bool(search.delay)), patch.object(ai, "TURN_SEARCH_TIMEOUT_SECONDS", .001):
                model = Model(decision(tool()), FINAL)
                result = self.run_turn(model, search)
                self.assertEqual(result["status"], "ok")
                self.assertIn('"status": "unavailable"', model.calls[1][1]["messages"][-1]["content"])

    def test_empty_daily_search_budget_keeps_the_normal_reply_available(self):
        model, search = Model(FINAL), Search()
        result = self.run_turn(model, search, {**QUOTA, "used": 10, "remaining": 0})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(model.calls[0][1]["tools"], [])
        self.assertEqual(len(search.calls), 0)

    def test_model_budget_is_reserved_before_spending_search_quota(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "1")
            conn.commit()
        model, search = Model(decision(tool())), Search()
        result = self.run_turn(model, search)
        self.assertEqual((result["status"], len(model.calls), len(search.calls), self.calls_today()), ("capped", 1, 0, 1))

    def test_public_and_admin_panels_expose_only_aggregate_usage(self):
        from classroom_app.services.wanderer_admin_service import build_wanderer_detail
        from classroom_app.services.wanderer_public_service import build_wanderer_home

        raw = {**QUOTA, "query": "private conversation secret", "owner": "hidden actor", "history": ["never public"]}
        with patch("classroom_app.services.ai_web_search_service.snapshot_for_wanderer", return_value=raw):
            with self.connect() as conn:
                teacher_id = conn.execute("SELECT id FROM teachers LIMIT 1").fetchone()[0]
                home = build_wanderer_home(conn, {"role": "teacher", "id": teacher_id}, self.person["slug"])
                detail = build_wanderer_detail(conn, self.person["id"])
        self.assertEqual(home["web_search_usage"], detail["web_search_usage"])
        self.assertEqual(set(home["web_search_usage"]), {"limit", "used", "reserved", "unknown", "remaining", "date", "reset_at", "available"})
        self.assertEqual(home["web_search_usage"]["remaining"], 7)
        self.assertNotIn(raw["query"], json.dumps(home, ensure_ascii=False))
        self.assertNotIn(raw["query"], json.dumps(detail, ensure_ascii=False))

    def test_counter_failure_is_visible_without_breaking_the_existing_panel(self):
        from classroom_app.services.wanderer_public_service import web_search_usage

        with patch("classroom_app.services.ai_web_search_service.snapshot_for_wanderer", side_effect=RuntimeError("temporary")):
            with self.connect() as conn:
                usage = web_search_usage(conn, self.person)
        self.assertFalse(usage["available"])
