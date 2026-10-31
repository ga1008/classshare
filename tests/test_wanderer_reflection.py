import asyncio
import json
import unittest
from datetime import datetime

from classroom_app.services.wanderer_actions_common import ActionContext, today_action_count
from classroom_app.services.wanderer_reflection_service import (
    reflection_date,
    run_daily_reflections,
    run_reflection,
)
from classroom_app.services.wanderer_settings_service import SETTING_DAILY_AI_CALL_CAP, wanderer_runtime_setting_set
from tests.wanderer_behavior_support import StubCaller, ZeroSleep
from tests.wanderer_test_support import WandererDbTestCase


def _payload():
    return json.dumps({
        "mood": {"label": "松了一点", "valence": 0.4, "energy": 0.6, "reason": "把话说开了"},
        "today": {"summary": "今天和同学聊了跑步，也读了一点材料。", "notable_actions": ["跑步"], "consequences": []},
        "relationships": [{"identity": "student:2", "affinity_delta": 1, "trust_delta": 0.05, "note": "更熟了一点"}],
        "memory": {"new_episodes": ["今天有人记得我膝盖疼。"], "compress": False},
        "self_review": {"did_well": "先听完了", "regret": "有点急", "patterns": ["先听再说"], "values_unchanged": ["真诚"]},
        "tomorrow": {"proactive_multiplier": 1.2, "tone": {"warmth": 0.2, "directness": 0.1},
                     "focus_topics": ["跑步"], "planned_actions": ["主动和一个人说话"]},
        "promotion_intent": False,
    }, ensure_ascii=False)


class ReflectionTest(WandererDbTestCase):
    NOW = datetime(2026, 10, 31, 23, 35, 0)

    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute("DELETE FROM wanderer_daily_reflections")
            conn.execute("DELETE FROM wanderer_action_log")
            conn.execute("UPDATE wanderer_agents SET mood_json = '{}', self_tuning_json = '{}'")
            conn.commit()

    def _ctx(self, slug, caller):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, slug)
        return ActionContext(conn_factory=self.connect, wanderer=wanderer, ai_caller=caller,
                             sleep=ZeroSleep(), now=self.NOW)

    def test_reflection_is_daily_idempotent_and_budget_exempt(self):
        ctx = self._ctx("atai", StubCaller(_payload()))
        date = reflection_date(self.NOW)
        with self.connect() as conn:
            before = today_action_count(conn, ctx.wanderer_id, self.NOW)
        result = asyncio.run(run_reflection(ctx))
        self.assertEqual(result["status"], "applied", result)
        with self.connect() as conn:
            row = conn.execute("SELECT status, output_json, applied_json FROM wanderer_daily_reflections "
                               "WHERE wanderer_id = ? AND reflection_date = ?", (ctx.wanderer_id, date)).fetchone()
            fresh = self.wanderer(conn, "atai")
            after = today_action_count(conn, ctx.wanderer_id, self.NOW)
            kinds = [r[0] for r in conn.execute("SELECT action_kind FROM wanderer_action_log WHERE wanderer_id = ?",
                                                (ctx.wanderer_id,)).fetchall()]
        self.assertEqual(row["status"], "applied")
        self.assertEqual(fresh["mood"]["emotion"], "松了一点")
        self.assertEqual(fresh["self_tuning"]["next_day"]["proactive_multiplier"], 1.2)
        self.assertEqual(next(iter(fresh["self_tuning"]["next_day"]["intentions"]), ""), "主动和一个人说话")
        self.assertEqual(before, after)
        self.assertEqual(kinds, ["daily_reflection"])

        again = asyncio.run(run_reflection(self._ctx("atai", StubCaller(_payload()))))
        self.assertEqual(again["status"], "skipped")

    def test_unparseable_reflection_is_failed_without_mutation(self):
        ctx = self._ctx("momo", StubCaller("这不是 JSON"))
        result = asyncio.run(run_reflection(ctx))
        self.assertEqual(result["status"], "failed")
        with self.connect() as conn:
            row = conn.execute("SELECT status, error FROM wanderer_daily_reflections WHERE wanderer_id = ?",
                               (ctx.wanderer_id,)).fetchone()
            fresh = self.wanderer(conn, "momo")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"])
        self.assertFalse(fresh["mood"])

    def test_daily_batch_processes_only_the_batch_and_is_resumable(self):
        import os
        from unittest.mock import patch
        from tests.wanderer_behavior_support import StubCaller, ZeroSleep

        with patch.dict(os.environ, {"WANDERER_ENABLED": "true"}):
            first = asyncio.run(run_daily_reflections(
                self.connect, ai_caller=StubCaller(_payload()), sleep=ZeroSleep(),
                now=self.NOW, batch_size=2, max_seconds=30))
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["processed"], 2)
        self.assertEqual(first["applied"], 2)
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM wanderer_daily_reflections WHERE reflection_date = ?",
                                 (reflection_date(self.NOW),)).fetchone()[0]
        self.assertEqual(int(count), 2)

    def test_capped_reflection_is_marked_and_retryable(self):
        with self.connect() as conn:
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "0")
            conn.commit()
        ctx = self._ctx("lele", StubCaller(_payload()))
        result = asyncio.run(run_reflection(ctx))
        self.assertEqual(result["status"], "skipped_capped", result)
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM wanderer_daily_reflections WHERE wanderer_id = ?",
                               (ctx.wanderer_id,)).fetchone()
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "4096")
            conn.commit()
        self.assertEqual(row["status"], "skipped_capped")


if __name__ == "__main__":
    unittest.main()
