import asyncio
import json
import unittest
from datetime import datetime, timedelta

from classroom_app import database
from classroom_app.services.heavenly_dao_service import run_dao_review_once
from classroom_app.services.wanderer_identity_service import load_wanderer_by_slug
from classroom_app.services.wanderer_life_core import lifespan_weeks_for
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase

NEUTRAL = '{"insight":1.0,"memory":1.0,"vitality":1.0,"eloquence":1.0,"charm":1.0,"luck":1.0}'
EMPTY = '{"warnings": [], "demotions": [], "penalties": [], "talent_changes": []}'


def _ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def _discipline(penalties=(), talent_changes=()):
    return json.dumps({"warnings": [], "demotions": [], "penalties": list(penalties),
                       "talent_changes": list(talent_changes)}, ensure_ascii=False)


class DaoLifecycleTest(WandererWorldTestCase):
    def review(self, caller, wanderer_id):
        return asyncio.run(run_dao_review_once(database.get_db_connection, wanderer_ids=[wanderer_id],
                                               ai_caller=caller, force=True))

    def agent(self, wanderer_id):
        return self.rows("SELECT * FROM wanderer_agents WHERE id = ?", (wanderer_id,))[0]

    def reviews(self, wanderer_id, kind=None):
        sql, params = "SELECT * FROM wanderer_dao_reviews WHERE wanderer_id = ?", [wanderer_id]
        if kind:
            sql += " AND review_kind = ?"
            params.append(kind)
        return self.rows(sql + " ORDER BY id", tuple(params))

    def test_penalty_deducts_life_and_is_recorded(self):
        atai = self.set_wanderer("atai", tier=2, born_at=_ago(10), lifespan_weeks=lifespan_weeks_for(2, 1.0),
                                 life_penalty_weeks=0, talents_json=NEUTRAL)
        with self.connect() as conn:
            before = load_wanderer_by_slug(conn, "atai")["life"]["remaining_days"]
        summary = self.review(StubCaller(_discipline([{"wanderer_id": atai["id"], "weeks": 2,
                                                       "reason": "屡劝不改"}])), atai["id"])
        after = self.agent(atai["id"])
        self.assertEqual(summary["counts"]["penalties"], 1)
        self.assertAlmostEqual(after["life_penalty_weeks"], 2.0, places=1)
        penalty = self.reviews(atai["id"], "penalty")[0]
        self.assertEqual(penalty["verdict"], "deducted")
        self.assertIn("屡劝不改", penalty["reason"])
        life = self.reviews(atai["id"], "life")[0]
        self.assertIn("剩余", life["reason"])
        self.assertGreater(before, 0)

    def test_penalty_can_kill_and_leaves_a_legacy(self):
        xiaoman = self.set_wanderer("xiaoman", tier=0, born_at=_ago(13), lifespan_weeks=2.0,
                                    life_penalty_weeks=0, talents_json=NEUTRAL)
        summary = self.review(StubCaller(_discipline([{"wanderer_id": xiaoman["id"], "weeks": 2,
                                                       "reason": "天罚"}])), xiaoman["id"])
        agent = self.agent(xiaoman["id"])
        legacy = self.rows("SELECT status FROM wanderer_legacies WHERE wanderer_id = ?", (xiaoman["id"],))
        self.assertEqual(summary["counts"]["penalty_deaths"], 1)
        self.assertEqual(agent["status"], "dead")
        self.assertEqual(legacy[0]["status"], "pending")

    def test_talent_change_is_bounded(self):
        atai = self.set_wanderer("atai", tier=1, talents_json=NEUTRAL)
        summary = self.review(StubCaller(_discipline([], [
            {"wanderer_id": atai["id"], "talent": "insight", "delta": -5, "reason": "长期灌水"}
        ])), atai["id"])
        agent = self.agent(atai["id"])
        talents = json.loads(agent["talents_json"])
        self.assertEqual(summary["counts"]["talent_changes"], 1)
        self.assertEqual(talents["insight"], 0.8)  # −0.2 per review, clamped
        review = self.reviews(atai["id"], "talent")[0]
        self.assertIn("悟性", review["reason"])

    def _seed_warning_and_penalty(self, wanderer_id):
        now = _ago(0)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO wanderer_dao_reviews (wanderer_id, review_kind, verdict, from_tier, to_tier, "
                "reason, evidence_json, created_by, created_at) VALUES (?, 'discipline', 'warning', 5, 5, ?, '{}', "
                "'dao', ?)", (wanderer_id, "课堂刷屏", now),
            )
            conn.execute(
                "INSERT INTO wanderer_dao_reviews (wanderer_id, review_kind, verdict, from_tier, to_tier, "
                "reason, evidence_json, created_by, created_at) VALUES (?, 'penalty', 'deducted', 5, 5, ?, ?, "
                "'dao', ?)", (wanderer_id, "折寿两周", json.dumps({"weeks": 2.0}), now),
            )
            conn.commit()
            return now

    def test_upheld_appeal_clears_a_warning_and_refunds_life(self):
        lele = self.set_wanderer("lele", tier=5, warnings_count=1, life_penalty_weeks=2.0,
                                 life_extensions=0, talents_json=NEUTRAL)
        now = self._seed_warning_and_penalty(lele["id"])
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET last_warning_at = ?, appeal_requested = 1, "
                         "appeal_requested_at = ?, appeal_note = '我没有刷屏', last_appeal_at = ? WHERE id = ?",
                         (now, now, now, lele["id"]))
            conn.commit()
        appeal = json.dumps({"verdict": "upheld", "reason": "证据不足，撤销警告"}, ensure_ascii=False)
        summary = self.review(StubCaller(EMPTY, appeal), lele["id"])
        agent = self.agent(lele["id"])
        self.assertEqual(summary["counts"]["appeals_upheld"], 1)
        self.assertEqual(agent["warnings_count"], 0)
        self.assertAlmostEqual(agent["life_penalty_weeks"], 0.0, places=1)
        self.assertEqual(agent["appeal_requested"], 0)
        self.assertEqual(self.reviews(lele["id"], "appeal")[0]["verdict"], "upheld")

    def test_rejected_appeal_costs_half_a_week(self):
        hanbing = self.set_wanderer("hanbing", tier=5, warnings_count=1, life_penalty_weeks=0,
                                    life_extensions=0, talents_json=NEUTRAL)
        now = self._seed_warning_and_penalty(hanbing["id"])
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET last_warning_at = ?, appeal_requested = 1, "
                         "appeal_requested_at = ?, appeal_note = '我不服', last_appeal_at = ? WHERE id = ?",
                         (now, now, now, hanbing["id"]))
            conn.commit()
        appeal = json.dumps({"verdict": "rejected", "reason": "证据成立"}, ensure_ascii=False)
        summary = self.review(StubCaller(EMPTY, appeal), hanbing["id"])
        agent = self.agent(hanbing["id"])
        self.assertEqual(summary["counts"]["appeals_rejected"], 1)
        self.assertEqual(agent["warnings_count"], 1)
        self.assertAlmostEqual(agent["life_penalty_weeks"], 0.5, places=1)
        self.assertEqual(self.reviews(hanbing["id"], "appeal")[0]["verdict"], "rejected_appeal")

    def test_expiry_is_swept_even_without_discipline(self):
        xiaoman = self.set_wanderer("xiaoman", tier=0, born_at=_ago(30), lifespan_weeks=2.0,
                                    life_penalty_weeks=0, talents_json=NEUTRAL)
        summary = self.review(StubCaller(EMPTY), xiaoman["id"])
        agent = self.agent(xiaoman["id"])
        death = self.reviews(xiaoman["id"], "death")[0]
        self.assertEqual(summary["counts"]["died"], 1)
        self.assertEqual(agent["status"], "dead")
        self.assertIn("寿元耗尽", death["reason"])


if __name__ == "__main__":
    unittest.main()
