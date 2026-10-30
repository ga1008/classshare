import asyncio
import json
import random
import unittest
from datetime import datetime, timedelta

from classroom_app import database
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_actions_knowledge import maybe_extend_life, request_appeal, try_resurrect
from classroom_app.services.wanderer_identity_service import load_wanderer_by_slug, mark_dead
from classroom_app.services.wanderer_life_core import lifespan_weeks_for
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase, ZeroSleep

NEUTRAL = '{"insight":1.0,"memory":1.0,"vitality":1.0,"eloquence":1.0,"charm":1.0,"luck":1.0}'
OFF = {key: 0 for key in ("blog_browse", "blog_like", "blog_comment", "reply", "dm_reply",
                          "classroom_visit", "classroom_message", "knowledge_read", "promotion",
                          "blog_post", "social")}


def _ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def _ctx(wanderer, caller, *, special=True):
    return ActionContext(conn_factory=database.get_db_connection, wanderer=wanderer, ai_caller=caller,
                         sleep=ZeroSleep(), rng=random.Random(7), special=special)


class ExtendLifeActionTest(WandererWorldTestCase):
    def test_desperate_wanderer_burns_cultivation_for_a_week(self):
        xiaoman = self.set_wanderer("xiaoman", born_at=_ago(11), lifespan_weeks=2.0, life_extensions=0,
                                    cultivation_score=40.0, talents_json=NEUTRAL, behaviors_json=OFF)
        caller = StubCaller(json.dumps({"decision": "extend", "reason": "我还想再试试"}, ensure_ascii=False))
        summary = self.tick(caller, [xiaoman["id"]])
        self.assertEqual(summary["failed"], 0, summary)
        actions = self.actions(xiaoman["id"])
        self.assertEqual([item["action_kind"] for item in actions], ["extend_life"])
        self.assertEqual(actions[0]["outcome"], "ok")
        agent = self.rows("SELECT life_extensions, cultivation_score FROM wanderer_agents WHERE id = ?",
                          (xiaoman["id"],))[0]
        self.assertEqual(agent["life_extensions"], 1)
        self.assertLess(agent["cultivation_score"], 40.0)

    def test_refusal_is_logged_without_spending(self):
        xiaoman = self.set_wanderer("xiaoman", born_at=_ago(11), lifespan_weeks=2.0, life_extensions=0,
                                    cultivation_score=40.0, talents_json=NEUTRAL, behaviors_json=OFF)
        caller = StubCaller(json.dumps({"decision": "refuse", "reason": "活够了"}, ensure_ascii=False))
        self.tick(caller, [xiaoman["id"]])
        actions = self.actions(xiaoman["id"])
        self.assertEqual((actions[0]["action_kind"], actions[0]["outcome"]), ("extend_life", "skipped"))
        agent = self.rows("SELECT life_extensions, cultivation_score FROM wanderer_agents WHERE id = ?",
                          (xiaoman["id"],))[0]
        self.assertEqual((agent["life_extensions"], agent["cultivation_score"]), (0, 40.0))


class AppealActionTest(WandererWorldTestCase):
    def test_tier_five_can_contest_a_recent_warning(self):
        lele = self.set_wanderer("lele", tier=5, warnings_count=1, last_warning_at=_ago(1),
                                 last_appeal_at=None, appeal_requested=0, talents_json=NEUTRAL)
        caller = StubCaller(json.dumps({"decision": "appeal", "reason": "那次不是我说的"},
                                       ensure_ascii=False))
        with self.connect() as conn:
            wanderer = load_wanderer_by_slug(conn, "lele")
        done = asyncio.run(request_appeal(_ctx(wanderer, caller)))
        self.assertEqual(done[0]["kind"], "appeal")
        agent = self.rows("SELECT appeal_requested, appeal_note FROM wanderer_agents WHERE id = ?", (lele["id"],))[0]
        self.assertEqual(agent["appeal_requested"], 1)
        self.assertIn("不是我说的", agent["appeal_note"])

    def test_low_tier_cannot_contest(self):
        atai = self.set_wanderer("atai", tier=2, warnings_count=1, last_warning_at=_ago(1),
                                 last_appeal_at=None, talents_json=NEUTRAL)
        with self.connect() as conn:
            wanderer = load_wanderer_by_slug(conn, "atai")
        done = asyncio.run(request_appeal(_ctx(wanderer, StubCaller())))
        self.assertEqual(done, [])
        self.assertEqual(self.rows("SELECT appeal_requested FROM wanderer_agents WHERE id = ?",
                                   (atai["id"],))[0]["appeal_requested"], 0)


class ResurrectActionTest(WandererWorldTestCase):
    def test_tier_ten_can_spend_weeks_to_revive(self):
        with self.connect() as conn:
            target = load_wanderer_by_slug(conn, "xiaoman")
            mark_dead(conn, int(target["id"]), "寿元耗尽（合成测试）")
            reviver = load_wanderer_by_slug(conn, "suqinghe")
            conn.execute("UPDATE wanderer_agents SET tier = 10, lifespan_weeks = ?, life_penalty_weeks = 0, "
                         "born_at = ? WHERE id = ?",
                         (lifespan_weeks_for(10, 1.0), target["created_at"], reviver["id"]))
            conn.commit()
            reviver = load_wanderer_by_slug(conn, "suqinghe")
        caller = StubCaller(json.dumps({"resurrect": target["id"], "reason": "她还能再走一段"},
                                       ensure_ascii=False))
        done = asyncio.run(try_resurrect(_ctx(reviver, caller)))
        self.assertEqual(done[0]["kind"], "resurrect")
        self.assertEqual(done[0]["outcome"], "ok")
        revived = self.rows("SELECT status FROM wanderer_agents WHERE id = ?", (target["id"],))[0]
        self.assertEqual(revived["status"], "active")
        after = self.rows("SELECT life_penalty_weeks FROM wanderer_agents WHERE id = ?", (reviver["id"],))[0]
        self.assertAlmostEqual(after["life_penalty_weeks"], 8.0, places=1)


if __name__ == "__main__":
    unittest.main()
