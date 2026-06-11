import contextlib
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DB_ENGINE"] = "sqlite"

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


class ChatPlatformQueryIntentTests(unittest.TestCase):
    def test_local_fallback_infers_assignment_submission_query(self):
        intent = service.infer_platform_query_intent("三班这次作业有多少人没交？")

        self.assertEqual("assignment_submission_status", intent["view"])
        self.assertEqual("三班", intent["params"]["class_keyword"])

    def test_local_fallback_infers_low_score_threshold(self):
        intent = service.infer_platform_query_intent("帮我列出低于 72 分的学生名单")

        self.assertEqual("low_scores", intent["view"])
        self.assertEqual(72.0, intent["params"]["threshold"])

    def test_local_fallback_ignores_non_data_question(self):
        self.assertIsNone(service.infer_platform_query_intent("帮我写一段课堂导入语"))

    def test_detect_intent_uses_local_fallback_when_intent_ai_fails(self):
        with patch("classroom_app.core.ai_client") as client:
            client.post = AsyncMock(side_effect=RuntimeError("intent model down"))
            intent = service.detect_platform_query_intent("三班这次作业有多少人没交？")

        result = self._run(intent)
        self.assertEqual("assignment_submission_status", result["view"])

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
            "classroom_app.services.chat_platform_query_service.detect_platform_query_intent",
            AsyncMock(return_value={"view": "assignment_submission_status", "params": {"class_keyword": "三班"}}),
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
            "classroom_app.services.chat_platform_query_service.detect_platform_query_intent",
            AsyncMock(return_value={"view": "assignment_submission_status", "params": {}}),
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


if __name__ == "__main__":
    unittest.main()
