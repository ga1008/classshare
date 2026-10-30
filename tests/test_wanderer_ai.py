import asyncio
import os
import unittest
from unittest.mock import patch

from classroom_app import database
from classroom_app.services import wanderer_ai_service
from classroom_app.services.wanderer_settings_service import (
    SETTING_DAILY_AI_CALL_CAP,
    daily_ai_calls,
    daily_ai_calls_key,
    daily_cap,
    increment_daily_ai_calls,
    is_wanderer_runtime_enabled,
    wanderer_runtime_setting_get,
    wanderer_runtime_setting_set,
)
from tests.wanderer_test_support import WandererDbTestCase

TURN_JSON = '{"action": "multi", "parts": ["好耶", "我也想去", "带我一个", "第四条"], "mood": "兴奋", "like": true}'


class StubCaller:
    def __init__(self, *, text=TURN_JSON, error=None, status="success"):
        self.calls = []
        self.text = text
        self.error = error
        self.status = status

    async def __call__(self, endpoint, json_payload, **kwargs):
        self.calls.append((endpoint, json_payload, kwargs))
        if self.error is not None:
            raise self.error
        return {"status": self.status, "response_text": self.text}


class WandererAiTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), "0")
            conn.execute("DELETE FROM wanderer_runtime_settings WHERE key = ?", (SETTING_DAILY_AI_CALL_CAP,))
            conn.commit()

    def _turn(self, slug, caller, suffix="blog_reply"):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, slug)
        return asyncio.run(wanderer_ai_service.generate_turn(
            database.get_db_connection, wanderer, system_prompt="系统", user_message="场景",
            task_label_suffix=suffix, ai_caller=caller,
        )), wanderer

    def _calls_today(self):
        with self.connect() as conn:
            return daily_ai_calls(conn)

    def test_generate_turn_shapes_gateway_call_and_counts(self):
        caller = StubCaller()
        result, wanderer = self._turn("lele", caller)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["turn"]["action"], "multi")
        self.assertEqual(result["turn"]["parts"], ["好耶", "我也想去", "带我一个"])  # lele max_parts = 3
        self.assertTrue(result["turn"]["like"])
        self.assertEqual(self._calls_today(), 1)
        endpoint, payload, kwargs = caller.calls[0]
        self.assertEqual(endpoint, "/api/ai/chat")
        self.assertEqual(payload["task_type"], "fast_text_response")
        self.assertEqual(payload["task_label"], "wanderer:lele:blog_reply")
        self.assertEqual(payload["task_priority"], "background")
        self.assertFalse(payload["web_search_enabled"])
        self.assertEqual(payload["business_context"]["source_feature"], "wanderer")
        self.assertTrue(payload["business_context"]["logical_call_id"].startswith("wanderer:lele:blog_reply:"))
        self.assertTrue(payload["system_prompt"].startswith("系统\n\n"))
        self.assertIn("公开网页搜索额度", payload["system_prompt"])
        self.assertIn("场景", payload["new_message"])
        self.assertEqual(kwargs["priority"], "P2")
        self.assertEqual(kwargs["task_type"], "wanderer_turn")
        self.assertEqual(kwargs["student_id"], wanderer["student_id"])
        self.assertEqual(kwargs["timeout"], 60.0)

    def test_cap_blocks_calls_without_counting(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "1")
            conn.commit()
        caller = StubCaller()
        first, _ = self._turn("atai", caller)
        second, _ = self._turn("atai", caller)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "capped")
        self.assertEqual(second["turn"]["action"], "skip")
        self.assertEqual(second["ai_calls"], 0)
        self.assertEqual(len(caller.calls), 1)
        self.assertEqual(self._calls_today(), 1)

    def test_failures_degrade_to_skip(self):
        for caller in (StubCaller(error=RuntimeError("timeout")), StubCaller(status="error"), StubCaller(text="不是JSON")):
            result, _ = self._turn("momo", caller)
            self.assertEqual(result["turn"]["action"], "skip")
            self.assertIn(result["status"], {"failed", "ok"})
        self.assertEqual(self._calls_today(), 3)

    def test_dao_judge_uses_deep_text_and_ignores_cap(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "0")
            conn.commit()
        caller = StubCaller(text='```json\n{"verdict": "approved", "reason": "知识扎实", "advice": "继续"}\n```')
        result = asyncio.run(wanderer_ai_service.dao_judge(
            database.get_db_connection, system_prompt="天道", user_message="证据", kind="promotion",
            ai_caller=caller, subject_student_id=12,
        ))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["verdict"], "approved")
        _endpoint, payload, kwargs = caller.calls[0]
        self.assertEqual(payload["task_type"], "deep_text_reasoning")
        self.assertEqual(payload["task_label"], "dao:promotion")
        self.assertEqual(kwargs["priority"], "P2")
        self.assertEqual(kwargs["student_id"], 12)
        self.assertEqual(self._calls_today(), 1)

    def test_dao_mention_cap_blocks_only_mentions(self):
        from classroom_app.services.wanderer_settings_service import (
            SETTING_DAO_MENTION_DAILY_CAP,
            dao_mention_calls_key,
        )

        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAO_MENTION_DAILY_CAP, "1")
            wanderer_runtime_setting_set(conn, dao_mention_calls_key(), "0")
            conn.commit()
        caller = StubCaller(text='{"on_topic": true, "reply": "可"}')

        def judge(kind):
            return asyncio.run(wanderer_ai_service.dao_judge(
                database.get_db_connection, system_prompt="天道", user_message="问", kind=kind, ai_caller=caller,
            ))

        self.assertEqual(judge("mention")["status"], "ok")
        capped = judge("mention")
        self.assertEqual((capped["status"], capped["ai_calls"]), ("capped", 0))
        self.assertEqual(len(caller.calls), 1)
        self.assertEqual(judge("discipline")["status"], "ok")  # discipline/promotion stay uncapped
        self.assertEqual(len(caller.calls), 2)

    def test_dao_judge_unparseable_is_failed(self):
        result = asyncio.run(wanderer_ai_service.dao_judge(
            database.get_db_connection, system_prompt="天道", user_message="证据", kind="discipline",
            ai_caller=StubCaller(text="天道不语"),
        ))
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["result"])


class WandererSettingsTest(WandererDbTestCase):
    seed_on_setup = False

    def test_cap_resolution_order(self):
        with self.connect() as conn:
            with patch.dict(os.environ, {"WANDERER_DAILY_AI_CALL_CAP": "7"}):
                self.assertEqual(daily_cap(conn), 7)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WANDERER_DAILY_AI_CALL_CAP", None)
                self.assertEqual(daily_cap(conn), 4096)
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "55")
            self.assertEqual(daily_cap(conn), 55)
            conn.rollback()

    def test_counter_increments_atomically(self):
        with self.connect() as conn:
            self.assertEqual(increment_daily_ai_calls(conn), 1)
            self.assertEqual(increment_daily_ai_calls(conn, 3), 4)
            self.assertEqual(wanderer_runtime_setting_get(conn, daily_ai_calls_key()), "4")
            conn.rollback()

    def test_runtime_enabled_needs_env_and_setting(self):
        with self.connect() as conn:
            with patch.dict(os.environ, {"WANDERER_ENABLED": "false"}):
                self.assertFalse(is_wanderer_runtime_enabled(conn))
            with patch.dict(os.environ, {"WANDERER_ENABLED": "true"}):
                self.assertTrue(is_wanderer_runtime_enabled(conn))
                wanderer_runtime_setting_set(conn, "enabled", "0")
                self.assertFalse(is_wanderer_runtime_enabled(conn))
            conn.rollback()


if __name__ == "__main__":
    unittest.main()


class StructuredResponseCaller(StubCaller):
    """The AI service answers ``response_format: json`` with ``response_json`` and no text."""

    async def __call__(self, endpoint, json_payload, **kwargs):
        self.calls.append((endpoint, json_payload, kwargs))
        return {"status": "success", "response_json": {"action": "reply", "parts": ["在的"], "mood": "淡定", "like": True}}


class WandererStructuredResponseTest(WandererDbTestCase):
    def test_generate_turn_accepts_response_json_shape(self):
        import asyncio
        from classroom_app.services.wanderer_ai_service import generate_turn
        from classroom_app.services.wanderer_identity_service import load_wanderer_by_slug

        with self.connect() as conn:
            wanderer = load_wanderer_by_slug(conn, "lele")
        result = asyncio.run(generate_turn(self.connect, wanderer, system_prompt="系统", user_message="场景",
                                           task_label_suffix="blog_reply", ai_caller=StructuredResponseCaller()))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["turn"]["action"], "reply")
        self.assertEqual(result["turn"]["parts"], ["在的"])
        self.assertTrue(result["turn"]["like"])
