"""End-to-end tests for the Agents-SDK runtime without any paid model call.

The model is a scripted DeepSeek-style streaming endpoint (reasoning_content,
tool calls) served by ``httpx.MockTransport``; platform tools go through the
real FastAPI bridge in-process (ASGI) against an isolated SQLite database.
"""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

os.environ.setdefault("DB_ENGINE", "sqlite")

from tests.sqlite_database_fixture import isolated_sqlite_database  # noqa: E402


def _sse(chunks):
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def _chunk(delta=None, finish=None, usage=None):
    body = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 0, "model": "deepseek-flash",
            "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return body


def _turn(*, reasoning="", content="", tools=()):
    chunks = [_chunk({"role": "assistant", "content": ""})]
    if reasoning:
        chunks.append(_chunk({"reasoning_content": reasoning}))
    if content:
        chunks.append(_chunk({"content": content}))
    for index, (name, arguments) in enumerate(tools):
        chunks.append(_chunk({"tool_calls": [{"index": index, "id": f"call_{name}_{index}", "type": "function",
                                              "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}))
    chunks.append(_chunk({}, finish="tool_calls" if tools else "stop"))
    chunks.append(_chunk(None, usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}))
    return _sse(chunks)


def _deepseek_order_error(messages):
    for index, message in enumerate(messages):
        ids = [call["id"] for call in message.get("tool_calls") or []]
        if message.get("role") == "assistant" and ids:
            following = [item.get("tool_call_id") for item in messages[index + 1:index + 1 + len(ids)] if item.get("role") == "tool"]
            if sorted(following) != sorted(ids):
                return "An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'."
    return ""


class ScriptedModel:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        error = _deepseek_order_error(payload["messages"])
        if error:  # the live API answers exactly like this (2026-09-25 contract check)
            return httpx.Response(400, json={"error": {"message": error, "type": "invalid_request_error"}})
        body = self.turns.pop(0) if self.turns else _turn(content="# 完成\n没有更多步骤。")
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


class AgentSdkRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        cls.workspace = Path(tempfile.mkdtemp(prefix="agent-sdk-ws-"))
        from classroom_app.app import app

        cls.app = app

    def setUp(self):
        from classroom_app.database import get_db_connection
        from classroom_app.services import agent_task_service

        self.patches = [patch.object(agent_task_service, "AGENT_TASK_WORKSPACE_ROOT", self.workspace),
                        patch.object(agent_task_service, "_notify_task_finished")]
        for item in self.patches:
            item.start()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM agent_tasks")
            conn.execute("DELETE FROM agent_task_events")
            teacher = conn.execute("SELECT id FROM teachers WHERE email = ?", ("agent-t@example.test",)).fetchone()
            if not teacher:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(teachers)")}
                values = {"name": "测试老师", "email": "agent-t@example.test", "hashed_password": "x", "password_hash": "x",
                          "is_active": 1, "is_super_admin": 0}
                values = {key: value for key, value in values.items() if key in columns}
                conn.execute(f"INSERT INTO teachers ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                             tuple(values.values()))
                teacher = conn.execute("SELECT id FROM teachers WHERE email = ?", ("agent-t@example.test",)).fetchone()
            self.teacher_id = int(teacher["id"])
            conn.execute("DELETE FROM user_sessions WHERE session_user_key = ?", (f"teacher:{self.teacher_id}",))
            conn.execute("INSERT INTO user_sessions (session_user_key, session_id, user_id, role, name, expires_at) "
                         "VALUES (?, ?, ?, 'teacher', '测试老师', '2099-01-01T00:00:00+00:00')",
                         (f"teacher:{self.teacher_id}", "sess-agent-1", str(self.teacher_id)))
            conn.commit()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    # ------------------------------------------------------------------ helpers
    def _user(self):
        return {"id": self.teacher_id, "role": "teacher", "name": "测试老师", "session_id": "sess-agent-1"}

    def _create_task(self, instruction="帮我整理本周作业提交情况"):
        from classroom_app.database import get_db_connection
        from classroom_app.services.agent_task_service import create_agent_task

        with get_db_connection() as conn:
            task = create_agent_task(conn, self._user(), {"instruction": instruction, "task_type": "general_teaching_task"},
                                     source_session_id="sess-agent-1")
        return int(task["id"])

    def _claim(self):
        from classroom_app.database import get_db_connection
        from classroom_app.services.agent_task_service import claim_next_agent_task

        with get_db_connection() as conn:
            return claim_next_agent_task(conn, worker_id="test-worker")

    def _run(self, task, model):
        from openai import AsyncOpenAI
        from agents import OpenAIChatCompletionsModel
        from classroom_app.services.agent_sdk import runner
        from classroom_app.services.agent_sdk.bridge import BridgeClient
        from classroom_app.services.agent_sdk.model import ModelTarget

        def fake_build_model(target):
            client = AsyncOpenAI(api_key="test", base_url="https://api.deepseek.test",
                                 http_client=httpx.AsyncClient(transport=httpx.MockTransport(model.handler)))
            return OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client), client

        def bridge_factory(base_url, token):
            return BridgeClient("http://lanshare.test", token, transport=httpx.ASGITransport(app=self.app))

        with patch.object(runner, "build_model", fake_build_model), \
                patch.object(runner, "resolve_model_target",
                             lambda conn: ModelTarget("deepseek-flash", "https://api.deepseek.test", "k", "env")), \
                patch.object(runner, "BridgeClient", bridge_factory):
            asyncio.run(runner.run_agent_task(task))

    def _task_row(self, task_id):
        from classroom_app.database import get_db_connection

        with get_db_connection() as conn:
            return dict(conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())

    def _events(self, task_id):
        from classroom_app.database import get_db_connection

        with get_db_connection() as conn:
            return [(row["event_type"], json.loads(row["detail_json"] or "{}"))
                    for row in conn.execute("SELECT * FROM agent_task_events WHERE task_id = ? ORDER BY id", (task_id,))]

    # ------------------------------------------------------------------ tests
    def test_full_loop_decision_tool_question_answer_resume_result(self):
        from classroom_app.database import get_db_connection
        from classroom_app.services.agent_sdk.state import answer_parked_question, load_run_state

        task_id = self._create_task()
        model = ScriptedModel([
            _turn(reasoning="先确认身份，再决定查询范围。", tools=[
                ("record_decision", {"decision": "先读取平台概览", "rationale": "确认身份和权限", "next_steps": ["读取概览", "确认范围"]}),
                ("platform_overview", {})]),
            _turn(reasoning="范围不明确，需要问用户。", tools=[
                ("ask_user", {"title": "统计哪个范围？", "questions": [
                    {"question": "统计哪些课堂？", "options": [{"label": "全部课堂", "description": "推荐"}, {"label": "仅本周有课的课堂"}]}]})]),
        ])
        claimed = self._claim()
        self.assertEqual(task_id, claimed["id"])
        self._run(claimed, model)

        row = self._task_row(task_id)
        self.assertEqual(("queued", "waiting_input"), (row["status"], row["runtime_status"]), row.get("error_message"))
        events = self._events(task_id)
        types = [event for event, _detail in events]
        for expected in ("thinking", "decision", "tool_call", "tool_result", "question_requested"):
            self.assertIn(expected, types)
        decision = next(detail for event, detail in events if event == "decision")
        self.assertEqual("先读取平台概览", decision["decision"])
        overview_result = [detail for event, detail in events if event == "tool_result"]
        self.assertTrue(overview_result and overview_result[0]["ok"], overview_result)

        # Parked tasks hold no slot: another task can be claimed meanwhile.
        other_id = self._create_task("另外帮我汇总本学期的考勤异常情况")
        self.assertEqual(other_id, self._claim()["id"])
        with get_db_connection() as conn:
            conn.execute("UPDATE agent_tasks SET status='canceled' WHERE id=?", (other_id,))
            conn.commit()
            question = load_run_state(conn, task_id)["pending_question"]
            self.assertEqual("统计哪些课堂？", question["questions"][0]["question"])
            answer_parked_question(conn, task_id, user=self._user(), question_id=question["id"],
                                   answers=[{"id": "q1", "selected": ["全部课堂"], "custom": ""}])
        self.assertEqual("resume_pending", self._task_row(task_id)["runtime_status"])

        resumed = self._claim()
        self.assertEqual(task_id, resumed["id"])
        model.turns = [_turn(content="# 已完成统计\n\n- 全部课堂共 0 份待批改提交。")]
        self._run(resumed, model)

        row = self._task_row(task_id)
        self.assertEqual("completed", row["status"], row.get("error_message"))
        self.assertEqual("已完成统计", row["result_summary"])
        detail = json.loads(row["result_detail_json"])
        self.assertIn("全部课堂共 0 份", detail["deliverable_markdown"])
        self.assertEqual("openai-agents", detail["provider"])
        # The resumed request carries the answer and replays DeepSeek reasoning_content.
        last_messages = model.requests[-1]["messages"]
        self.assertTrue(any("全部课堂" in str(message.get("content")) for message in last_messages if message["role"] == "user"))
        self.assertTrue(any(message.get("reasoning_content") for message in last_messages if message["role"] == "assistant"))
        self.assertEqual({"enabled"}, {req.get("thinking", {}).get("type") for req in model.requests})

    def test_pause_request_parks_after_turn_and_resume_continues(self):
        from classroom_app.database import get_db_connection
        from classroom_app.services.agent_queue_control_service import pause_task, resume_task

        task_id = self._create_task("帮我准备一份本学期教学情况总结报告")
        claimed = self._claim()
        with get_db_connection() as conn:
            pause_task(conn, task_id, user=self._user())
        model = ScriptedModel([_turn(tools=[("record_decision", {"decision": "开始准备报告"})]) for _ in range(6)])
        self._run(claimed, model)
        row = self._task_row(task_id)
        self.assertEqual(("queued", "paused"), (row["status"], row["runtime_status"]), row.get("error_message"))
        with get_db_connection() as conn:
            resume_task(conn, task_id, user=self._user())
        self.assertEqual("resume_pending", self._task_row(task_id)["runtime_status"])
        resumed = self._claim()
        self._run(resumed, ScriptedModel([_turn(content="报告已完成")]))
        self.assertEqual("completed", self._task_row(task_id)["status"])

    def test_turn_budget_auto_continues_with_a_nudge_before_parking(self):
        from classroom_app.services.agent_sdk import runner

        # Production task 19: ~45 discovery calls exhausted the 60-turn budget and
        # the task was stranded on "继续". Now the runner nudges and continues.
        task_id = self._create_task("帮我配置本学期python程序设计课程的AI助手")
        claimed = self._claim()
        model = ScriptedModel([_turn(tools=[("record_decision", {"decision": f"继续检索 {index}"})]) for index in range(20)])
        with patch.object(runner, "AGENT_TASK_MAX_TURNS", 3), patch.object(runner, "AGENT_TASK_AUTO_CONTINUE_LIMIT", 2):
            self._run(claimed, model)
        row = self._task_row(task_id)
        self.assertEqual(("queued", "paused"), (row["status"], row["runtime_status"]), row.get("error_message"))
        # Three segments of three turns each before the task is finally parked.
        self.assertEqual(9, len(model.requests))
        continues = [detail["auto_continue"] for event, detail in self._events(task_id) if event == "decision" and detail.get("auto_continue")]
        self.assertEqual([1, 2], continues)
        self.assertTrue(any("本段步数已用尽" in str(message.get("content"))
                            for message in model.requests[-1]["messages"] if message["role"] == "user"))

        # A model that commits after the nudge finishes without any human click.
        second_id = self._create_task("帮我配置另一门课程的AI助手")
        second = self._claim()
        self.assertEqual(second_id, second["id"])
        model = ScriptedModel([_turn(tools=[("record_decision", {"decision": f"继续检索 {index}"})]) for index in range(3)]
                              + [_turn(content="# 已完成配置\n\n提示词与大纲已保存。")])
        with patch.object(runner, "AGENT_TASK_MAX_TURNS", 3), patch.object(runner, "AGENT_TASK_AUTO_CONTINUE_LIMIT", 2):
            self._run(second, model)
        self.assertEqual("completed", self._task_row(second_id)["status"])
        self.assertEqual(4, len(model.requests))

    def test_cancel_before_run_does_not_execute(self):
        from classroom_app.database import get_db_connection
        from classroom_app.services.agent_task_service import cancel_agent_task

        task_id = self._create_task("帮我统计本周各课堂的作业提交率")
        claimed = self._claim()
        with get_db_connection() as conn:
            cancel_agent_task(conn, task_id, teacher_id=self.teacher_id)
        model = ScriptedModel([_turn(content="不应执行")])
        self._run(claimed, model)
        self.assertEqual([], model.requests)
        self.assertIn(self._task_row(task_id)["status"], {"canceled", "failed"})

    def test_queue_pause_blocks_claims_and_clear_cancels_unstarted(self):
        from classroom_app.database import get_db_connection
        from classroom_app.services.agent_queue_control_service import clear_queue, set_queue_paused

        admin = {**self._user(), "name": "超管"}
        first = self._create_task("帮我整理第一课堂的学生名单")
        second = self._create_task("帮我整理第二课堂的学生名单")
        with get_db_connection() as conn:
            set_queue_paused(conn, paused=True, admin=admin)
        self.assertIsNone(self._claim())
        with get_db_connection() as conn:
            set_queue_paused(conn, paused=False, admin=admin)
            result = clear_queue(conn, admin=admin)
        self.assertEqual({first, second}, set(result["task_ids"]))
        self.assertEqual("canceled", self._task_row(first)["status"])
        self.assertIsNone(self._claim())


class DangerGuardTests(unittest.TestCase):
    def test_every_destructive_write_action_is_blocked_or_explicitly_reviewed(self):
        """platform_write bypasses the route guard, so new destructive actions must be classified here."""
        import re
        from classroom_app.services.agent_danger_guard import (
            HARD_BLOCKED_WRITE_ACTIONS, REVIEWED_DESTRUCTIVE_WRITE_ACTIONS, raise_if_write_action_blocked)
        from classroom_app.services.agent_platform_write_service import TRANSACTIONAL_ACTIONS
        from fastapi import HTTPException

        destructive = {action for action in TRANSACTIONAL_ACTIONS
                       if re.search(r"delete|remove|deactivate|grant|revoke|withdraw|unbind|publish|reset|clear|purge", action)}
        self.assertEqual(set(), destructive - HARD_BLOCKED_WRITE_ACTIONS - REVIEWED_DESTRUCTIVE_WRITE_ACTIONS)
        for action in ("delete_organization_school", "grant_teacher_super_admin", "deactivate_teacher_account"):
            with self.assertRaises(HTTPException) as blocked:
                raise_if_write_action_blocked(action)
            self.assertEqual(403, blocked.exception.status_code)
        raise_if_write_action_blocked("publish_blog_post")

    def test_credential_and_permission_routes_are_hard_blocked(self):
        from classroom_app.services.agent_danger_guard import assess_route

        for path in ("/api/manage/system/teachers/{teacher_id}/reset-password", "/api/manage/system/academic-credentials",
                     "/api/manage/teachers/{teacher_id}/permissions"):
            self.assertTrue(assess_route("POST", path).hard_blocked, path)

    def test_hard_blocks_account_wipes_and_super_admin_changes(self):
        from classroom_app.services.agent_danger_guard import assess_route

        for method, path in (("DELETE", "/api/manage/system/teachers/{teacher_id}"),
                             ("DELETE", "/api/manage/students/{student_id}"),
                             ("POST", "/api/manage/system/teachers/{teacher_id}/super-admin/grant"),
                             ("POST", "/api/manage/data/clear-all"),
                             ("POST", "/api/manage/system/repair-submission-files")):
            self.assertTrue(assess_route(method, path).hard_blocked, path)
        self.assertFalse(assess_route("GET", "/api/manage/students/{student_id}").hard_blocked)
        self.assertFalse(assess_route("DELETE", "/api/assignments/{assignment_id}").hard_blocked)
        self.assertTrue(assess_route("DELETE", "/api/assignments/{assignment_id}").destructive)

    def test_self_check_rules(self):
        from fastapi import HTTPException
        from classroom_app.services.agent_danger_guard import assess_route, evaluate_safety_check, normalize_safety_check

        single = assess_route("DELETE", "/api/assignments/{assignment_id}")
        with self.assertRaises(HTTPException) as missing:
            evaluate_safety_check(single, None, prior_destructive_count=0, confirmation_valid=False)
        self.assertEqual(428, missing.exception.status_code)
        ok = normalize_safety_check({"user_requested": True, "data_state": "user_specified", "reason": "用户点名删除这份草稿作业", "target_count": 1})
        self.assertTrue(evaluate_safety_check(single, ok, prior_destructive_count=0, confirmation_valid=False)["accepted"])
        not_requested = normalize_safety_check({"user_requested": False, "data_state": "user_specified", "reason": "我觉得没用了", "target_count": 1})
        with self.assertRaises(HTTPException):
            evaluate_safety_check(single, not_requested, prior_destructive_count=0, confirmation_valid=False)
        bulk = normalize_safety_check({"user_requested": True, "data_state": "user_specified", "reason": "用户要求清理这些提交", "target_count": 12})
        with self.assertRaises(HTTPException) as bulk_error:
            evaluate_safety_check(single, bulk, prior_destructive_count=0, confirmation_valid=False)
        self.assertEqual(409, bulk_error.exception.status_code)
        stale = normalize_safety_check({"user_requested": True, "data_state": "expired", "reason": "上学期已过期的草稿", "target_count": 12})
        self.assertTrue(evaluate_safety_check(single, stale, prior_destructive_count=0, confirmation_valid=False)["bulk"])
        confirmed = normalize_safety_check({"user_requested": True, "data_state": "user_confirmed", "reason": "用户在选项里确认删除", "target_count": 12})
        with self.assertRaises(HTTPException):
            evaluate_safety_check(single, confirmed, prior_destructive_count=0, confirmation_valid=False)
        self.assertTrue(evaluate_safety_check(single, confirmed, prior_destructive_count=0, confirmation_valid=True)["accepted"])
        self.assertEqual("", confirmed["question_id"])
        bound = normalize_safety_check({"user_requested": True, "data_state": "user_confirmed", "reason": "用户在选项里确认删除",
                                        "target_count": 12, "question_id": "q-abc"})
        self.assertEqual("q-abc", bound["question_id"])
        with self.assertRaises(HTTPException) as budget:
            evaluate_safety_check(single, stale, prior_destructive_count=40, confirmation_valid=True)
        self.assertEqual(429, budget.exception.status_code)


if __name__ == "__main__":
    unittest.main()
