import contextlib
import json
import os
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DB_ENGINE"] = "sqlite"

from classroom_app.db.schema_assignments import ensure_assignment_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.routers import ai as ai_router
from classroom_app.services import chat_platform_query_service as service


def _ai_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


async def _collect_platform_events(*, role="teacher", teacher_id=7, message="三班有多少人没交作业", payload=None):
    events = []
    payload = payload if payload is not None else {"system_prompt": "base prompt"}
    async for line in ai_router._platform_data_retrieval_events(role, teacher_id, message, payload):
        events.append(json.loads(line))
    return events, payload


async def _collect_gongwen_events(*, role="teacher", teacher_id=7, message="查一下最近的公文", payload=None):
    events = []
    payload = payload if payload is not None else {"system_prompt": "base prompt"}
    async for line in ai_router._gongwen_retrieval_events(role, teacher_id, message, payload):
        events.append(json.loads(line))
    return events, payload


def _open_platform_query_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_foundation_schema(conn)
    ensure_assignment_schema(conn)
    ensure_classroom_activity_schema(conn)
    conn.execute(
        """
        INSERT INTO teachers (id, name, email, hashed_password, school_code, school_name, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (7, "Teacher", "teacher7@example.test", "hashed", "gxufl", "广西外国语学院", 1),
    )
    conn.execute("INSERT INTO classes (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (1, "三班", 7))
    conn.execute("INSERT INTO courses (id, name, created_by_teacher_id) VALUES (?, ?, ?)", (1, "综合英语", 7))
    conn.execute(
        "INSERT INTO class_offerings (id, class_id, course_id, teacher_id) VALUES (?, ?, ?, ?)",
        (1, 1, 1, 7),
    )
    conn.executemany(
        "INSERT INTO students (id, student_id_number, name, class_id) VALUES (?, ?, ?, ?)",
        [
            (1, "S001", "Alice", 1),
            (2, "S002", "Bob", 1),
            (3, "S003", "Carol", 1),
        ],
    )
    conn.execute(
        """
        INSERT INTO assignments (id, course_id, class_offering_id, title, requirements_md, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, 1, "Unit 1 reflection", "Write a reflection", "published", "2026-01-03T08:00:00+00:00"),
    )
    conn.executemany(
        """
        INSERT INTO submissions (id, assignment_id, student_pk_id, student_name, submitted_at, score)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "1", 1, "Alice", "2026-01-04T09:00:00+00:00", 95),
            (2, "1", 2, "Bob", "2026-01-04T10:00:00+00:00", 58),
        ],
    )
    starts_at = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO teacher_calendar_events (
            teacher_id, source_type, source_key, title, starts_at, location, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (7, "exam", "exam:1", "三班期末考试监考", starts_at, "A101", "active"),
    )
    session_date = (datetime.now() + timedelta(days=2)).date().isoformat()
    conn.execute(
        """
        INSERT INTO class_offering_sessions (
            id, class_offering_id, order_index, title, session_date, weekday
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (1, 1, 2, "Unit 2 speaking lab", session_date, 2),
    )
    conn.commit()
    return conn


class ChatPlatformQueryIntentTests(unittest.TestCase):
    def test_local_fallback_infers_assignment_submission_query(self):
        intent = service.infer_platform_query_intent("三班这次作业有多少人没交？")

        self.assertEqual("assignment_submission_status", intent["view"])
        self.assertEqual("三班", intent["params"]["class_keyword"])

    def test_local_fallback_infers_low_score_threshold(self):
        intent = service.infer_platform_query_intent("帮我列出低于 72 分的学生名单")

        self.assertEqual("low_scores", intent["view"])
        self.assertEqual(72.0, intent["params"]["threshold"])

    def test_local_fallback_plans_multiple_light_tool_calls(self):
        plan = service.infer_platform_query_tool_calls("三班人数和这次作业没交名单一起查一下")

        self.assertTrue(plan["related"])
        self.assertGreaterEqual(len(plan["tool_calls"]), 2)
        self.assertEqual(
            {"assignment_submission_status", "class_roster"},
            {call["view"] for call in plan["tool_calls"][:2]},
        )

    def test_local_fallback_ignores_non_data_question(self):
        self.assertIsNone(service.infer_platform_query_intent("帮我写一段课堂导入语"))

    def test_detect_intent_uses_local_fallback_when_intent_ai_fails(self):
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=RuntimeError("intent model down"))
            intent = service.detect_platform_query_intent("三班这次作业有多少人没交？")

        result = self._run(intent)
        self.assertEqual("assignment_submission_status", result["view"])

    def test_detect_tool_calls_prefers_provider_native_platform_query(self):
        captured_payloads = []

        async def fake_post(url, json, timeout):
            captured_payloads.append(json)
            return _ai_response(
                {
                    "tool_calls": [
                        {
                            "name": "platform_query",
                            "arguments": {
                                "view": "class_roster",
                                "params": {"class_keyword": "三班"},
                            },
                        }
                    ]
                }
            )

        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=fake_post)
            plan = self._run(service.detect_platform_query_tool_calls("三班有多少学生？"))

        self.assertEqual(1, client.post.await_count)
        self.assertIn("tools", captured_payloads[0])
        self.assertEqual("platform_query", captured_payloads[0]["tools"][0]["function"]["name"])
        self.assertEqual([{"view": "class_roster", "params": {"class_keyword": "三班"}}], plan["tool_calls"])
        self.assertEqual("provider_tool_call", plan["planner_source"])

    def test_detect_tool_calls_guardrails_provider_weak_classroom_view(self):
        async def fake_post(url, json, timeout):
            return _ai_response(
                {
                    "tool_calls": [
                        {
                            "name": "platform_query",
                            "arguments": {"view": "my_classrooms", "params": {}},
                        }
                    ]
                }
            )

        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=fake_post)
            plan = self._run(
                service.detect_platform_query_tool_calls(
                    "\u4e09\u73ed\u6210\u7ee9\u4f4e\u4e8e 60 \u5206\u7684\u662f\u8c01\uff1f"
                )
            )

        self.assertEqual(1, client.post.await_count)
        self.assertEqual("low_scores", plan["tool_calls"][0]["view"])
        self.assertTrue(plan["guardrail_applied"])
        self.assertEqual("provider_tool_call", plan["planner_source"])

    def test_detect_tool_calls_falls_back_to_json_plan_when_provider_returns_no_tool(self):
        responses = [
            _ai_response({"tool_calls": []}),
            _ai_response(
                {
                    "response_json": {
                        "related": True,
                        "tool_calls": [
                            {
                                "view": "assignment_submission_status",
                                "params": {"class_keyword": "三班"},
                            }
                        ],
                        "needs_agent": False,
                    }
                }
            ),
        ]

        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=responses)
            plan = self._run(service.detect_platform_query_tool_calls("三班这次作业有多少人没交？"))

        self.assertEqual(2, client.post.await_count)
        self.assertEqual("assignment_submission_status", plan["tool_calls"][0]["view"])
        self.assertEqual("json_plan", plan["planner_source"])

    @staticmethod
    def _run(coro):
        import asyncio

        return asyncio.run(coro)


class ChatPlatformQueryEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_teacher_platform_query_emits_tool_status_and_injects_result_block(self):
        result = {
            "title": "作业提交情况",
            "note": "班级总人数 2，已提交 1，未交 1 人。",
            "rows": [{"未交学生": "Alice"}],
        }
        with patch(
            "classroom_app.services.gongwen_ai_search_service.message_may_mention_gongwen",
            return_value=False,
        ), patch(
            "classroom_app.services.chat_platform_query_service.message_may_need_platform_data",
            return_value=True,
        ), patch(
            "classroom_app.services.chat_platform_query_service.detect_platform_query_tool_calls",
            AsyncMock(
                return_value={
                    "related": True,
                    "tool_calls": [{"view": "assignment_submission_status", "params": {"class_keyword": "三班"}}],
                    "needs_agent": False,
                }
            ),
        ), patch(
            "classroom_app.services.chat_platform_query_service.run_platform_view",
            return_value=result,
        ) as run_view, patch(
            "classroom_app.routers.ai.get_db_connection",
            return_value=contextlib.nullcontext(object()),
        ):
            events, payload = await _collect_platform_events()

        tool_events = [event for event in events if event["event"] == "tool_status"]
        self.assertEqual(["detecting", "running", "done"], [event["stage"] for event in tool_events])
        self.assertEqual("platform_query", tool_events[0]["tool"])
        self.assertEqual("assignment_submission_status", tool_events[-1]["view"])
        self.assertEqual(1, tool_events[-1]["row_count"])
        self.assertIn("平台实时数据", payload["system_prompt"])
        self.assertIn("Alice", payload["system_prompt"])
        run_view.assert_called_once()

    async def test_teacher_platform_query_runs_two_tool_rounds_and_suggests_agent(self):
        roster_result = {"title": "班级名册", "rows": [{"姓名": "Alice"}, {"姓名": "Bob"}]}
        submission_result = {
            "title": "作业提交情况",
            "note": "班级总人数 2，已提交 1，未交 1 人。",
            "rows": [{"未交学生": "Bob"}],
        }
        with patch(
            "classroom_app.services.gongwen_ai_search_service.message_may_mention_gongwen",
            return_value=False,
        ), patch(
            "classroom_app.services.chat_platform_query_service.message_may_need_platform_data",
            return_value=True,
        ), patch(
            "classroom_app.services.chat_platform_query_service.detect_platform_query_tool_calls",
            AsyncMock(
                return_value={
                    "related": True,
                    "tool_calls": [
                        {"view": "class_roster", "params": {"class_keyword": "三班"}},
                        {"view": "assignment_submission_status", "params": {"class_keyword": "三班"}},
                        {"view": "low_scores", "params": {"threshold": 60}},
                    ],
                    "needs_agent": True,
                    "agent_reason": "需要更多交叉分析",
                }
            ),
        ), patch(
            "classroom_app.services.chat_platform_query_service.run_platform_view",
            side_effect=[roster_result, submission_result],
        ) as run_view, patch(
            "classroom_app.routers.ai.get_db_connection",
            return_value=contextlib.nullcontext(object()),
        ):
            events, payload = await _collect_platform_events(message="三班人数、未交和低分情况一起分析")

        tool_events = [event for event in events if event["event"] == "tool_status"]
        self.assertEqual(["detecting", "running", "done", "running", "done"], [event["stage"] for event in tool_events])
        self.assertEqual([1, 1, 2, 2], [event["round"] for event in tool_events if "round" in event])
        self.assertEqual(2, run_view.call_count)
        handoff_events = [event for event in events if event["event"] == "agent_handoff_suggested"]
        self.assertEqual(1, len(handoff_events))
        self.assertIn("转为 Agent 任务", payload["system_prompt"])
        self.assertIn("Alice", payload["system_prompt"])
        self.assertIn("Bob", payload["system_prompt"])

    async def test_student_platform_query_does_not_trigger_tools(self):
        with patch(
            "classroom_app.services.chat_platform_query_service.message_may_need_platform_data",
        ) as may_need:
            events, payload = await _collect_platform_events(role="student", teacher_id=22)

        self.assertEqual([], events)
        self.assertEqual("base prompt", payload["system_prompt"])
        may_need.assert_not_called()

    async def test_platform_query_failure_emits_tool_status_and_prompt_guardrail(self):
        with patch(
            "classroom_app.services.gongwen_ai_search_service.message_may_mention_gongwen",
            return_value=False,
        ), patch(
            "classroom_app.services.chat_platform_query_service.message_may_need_platform_data",
            return_value=True,
        ), patch(
            "classroom_app.services.chat_platform_query_service.detect_platform_query_tool_calls",
            AsyncMock(
                return_value={
                    "related": True,
                    "tool_calls": [{"view": "assignment_submission_status", "params": {}}],
                    "needs_agent": False,
                }
            ),
        ), patch(
            "classroom_app.services.chat_platform_query_service.run_platform_view",
            side_effect=RuntimeError("bad SQL"),
        ), patch(
            "classroom_app.routers.ai.get_db_connection",
            return_value=contextlib.nullcontext(object()),
        ):
            events, payload = await _collect_platform_events()

        tool_events = [event for event in events if event["event"] == "tool_status"]
        self.assertEqual("failed", tool_events[-1]["stage"])
        self.assertIn("平台数据查询暂时不可用", tool_events[-1]["message"])
        self.assertIn("不要编造课堂、学生、作业、成绩或日程数字", payload["system_prompt"])
        self.assertIn("转为 Agent 任务", payload["system_prompt"])

    async def test_gongwen_retrieval_emits_tool_status(self):
        result = {
            "doc_count": 1,
            "documents": [{"title": "教学安排通知", "url": "/manage/gongwen?document_id=1"}],
            "context_block": "--- 校园公文检索结果 ---\n教学安排通知",
        }
        with patch(
            "classroom_app.services.gongwen_ai_search_service.message_may_mention_gongwen",
            return_value=True,
        ), patch(
            "classroom_app.services.gongwen_ai_search_service.detect_gongwen_intent",
            AsyncMock(return_value={"related": True, "keywords": ["教学安排"]}),
        ), patch(
            "classroom_app.services.gongwen_ai_search_service.run_gongwen_retrieval",
            AsyncMock(return_value=result),
        ), patch(
            "classroom_app.services.gongwen_ai_search_service.summarize_retrieval_for_log",
            return_value="doc_count=1",
        ):
            events, payload = await _collect_gongwen_events()

        tool_events = [event for event in events if event["event"] == "tool_status"]
        self.assertEqual(["detecting", "running", "done"], [event["stage"] for event in tool_events])
        self.assertEqual("gongwen_search", tool_events[0]["tool"])
        self.assertEqual("校园公文检索", tool_events[0]["label"])
        self.assertEqual(1, tool_events[-1]["doc_count"])
        self.assertIn("教学安排通知", payload["system_prompt"])


class ChatPlatformQueryViewTests(unittest.TestCase):
    def test_five_light_query_views_return_teacher_scoped_facts(self):
        conn = _open_platform_query_conn()
        try:
            classrooms = service.run_platform_view(conn, teacher_id=7, view="my_classrooms", params={})
            roster = service.run_platform_view(
                conn,
                teacher_id=7,
                view="class_roster",
                params={"class_keyword": "三班"},
            )
            submission = service.run_platform_view(
                conn,
                teacher_id=7,
                view="assignment_submission_status",
                params={"assignment_keyword": "Unit 1", "class_keyword": "三班"},
            )
            low_scores = service.run_platform_view(
                conn,
                teacher_id=7,
                view="low_scores",
                params={"threshold": 60, "class_keyword": "三班"},
            )
            schedule = service.run_platform_view(conn, teacher_id=7, view="my_schedule", params={})

            self.assertEqual("三班", classrooms["rows"][0]["班级"])
            self.assertEqual(3, classrooms["rows"][0]["学生数"])
            self.assertEqual(["Alice", "Bob", "Carol"], [row["姓名"] for row in roster["rows"]])
            self.assertIn("班级总人数 3，已提交 2，未交 1 人", submission["note"])
            self.assertEqual(["Carol"], [row["未交学生"] for row in submission["rows"]])
            self.assertEqual(["Bob"], [row["学生"] for row in low_scores["rows"]])
            self.assertEqual("三班期末考试监考", schedule["rows"][0]["事项"])
            self.assertTrue(
                any(
                    row["类型"] == "class_session" and "Unit 2 speaking lab" in row["事项"]
                    for row in schedule["rows"]
                )
            )
        finally:
            conn.close()

    def test_schedule_view_includes_class_sessions_when_calendar_is_empty(self):
        conn = _open_platform_query_conn()
        try:
            conn.execute("DELETE FROM teacher_calendar_events")
            schedule = service.run_platform_view(conn, teacher_id=7, view="my_schedule", params={})

            self.assertEqual(1, len(schedule["rows"]))
            self.assertEqual("class_session", schedule["rows"][0]["类型"])
            self.assertIn("综合英语", schedule["rows"][0]["事项"])
            self.assertIn("三班", schedule["rows"][0]["事项"])
            self.assertIn("Unit 2 speaking lab", schedule["rows"][0]["事项"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
