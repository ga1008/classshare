import asyncio
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services import wanderer_life_core as core
from classroom_app.services.wanderer_identity_service import (
    current_life,
    death_reason,
    settle_natural_expiry,
    load_wanderer_by_slug,
    mark_dead,
)
from classroom_app.services.wanderer_life_service import (
    apply_life_penalty,
    apply_talent_change,
    award_interaction_cultivation,
    extend_life,
    life_payload,
    promotion_requirements,
)
from classroom_app.services.wanderer_settings_service import SETTING_LIFECYCLE_ENABLED, wanderer_runtime_setting_set
from tests.wanderer_test_support import WandererDbTestCase

TALENTS_NEUTRAL = '{"insight":1.0,"memory":1.0,"vitality":1.0,"eloquence":1.0,"charm":1.0,"luck":1.0}'


def _ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def _value(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


class LifeCoreTest(unittest.TestCase):
    def test_lifespan_table_spans_six_to_140_weeks(self):
        self.assertEqual(core.BASE_LIFESPAN_WEEKS[0], 6.0)
        self.assertEqual(core.BASE_LIFESPAN_WEEKS[10], 140.0)
        self.assertEqual(len(core.BASE_LIFESPAN_WEEKS), 11)
        self.assertEqual(list(core.BASE_LIFESPAN_WEEKS), sorted(core.BASE_LIFESPAN_WEEKS))

    def test_active_speech_limit_is_one_hundred_per_tier_step(self):
        self.assertEqual([core.active_speech_limit(t) for t in (-1, 0, 1, 2, 5, 10)],
                         [100, 100, 200, 300, 600, 1100])
        perks = core.tier_perks(0, 10)
        self.assertEqual((perks["active_speech_limit"], perks["reply_char_limit"]), (100, 100))
        self.assertTrue(perks["passive_speech_unlimited"])
        self.assertEqual(core.tier_perks(3, 10)["active_speech_limit"], 400)

    def test_ai_wake_limit_scales_with_tier_and_keeps_zero_disabled(self):
        caps = (3, 5, 8, 10, 12, 16, 22, 32, 46, 68, 100)
        self.assertEqual(core.wake_quota_table(), caps)
        for tier, cap in enumerate(caps):
            self.assertEqual(core.ai_wake_limit(tier, 1000), cap)
            self.assertEqual(core.ai_wake_limit(tier, 0), 0)
            self.assertEqual(core.ai_wake_limit(tier, None), 0)
            self.assertEqual(core.tier_perks(tier, 10, 1000)["ai_wake_limit"], cap)
        self.assertEqual(core.ai_wake_limit(0, 1), 1)

    def test_high_quality_interactions_are_worth_more(self):
        bare = core.interaction_gain("social_message")
        long_reply = core.interaction_gain("social_message", chars=150, answered=True)
        self.assertEqual(core.interaction_quality(0, False), 0.0)
        self.assertEqual(core.interaction_quality(150, False), 0.5)
        self.assertEqual(core.interaction_quality(150, True), 1.0)
        self.assertEqual(core.interaction_quality(5000, True), 1.0)
        self.assertAlmostEqual(long_reply, bare * 2, places=2)
        self.assertLess(core.interaction_gain("social_message", chars=50),
                        core.interaction_gain("social_message", chars=150))

    def test_vitality_scales_lifespan(self):
        self.assertEqual(core.lifespan_weeks_for(0, 1.0), 6.0)
        self.assertEqual(core.lifespan_weeks_for(0, 1.4), 8.4)
        self.assertEqual(core.lifespan_weeks_for(10, 1.0), 140.0)
        self.assertEqual(core.lifespan_weeks_for(-2, 1.0), 6.0)

    def test_life_state_remaining_urgent_and_expired(self):
        state = core.life_state(born_at=_ago(5), tier=0, lifespan_weeks=2.0)
        self.assertEqual(state["remaining_days"], 9)
        self.assertTrue(state["urgent"])
        self.assertFalse(state["critical"])
        self.assertFalse(state["expired"])
        dead = core.life_state(born_at=_ago(20), tier=0, lifespan_weeks=2.0)
        self.assertTrue(dead["expired"])
        self.assertEqual(dead["remaining_days"], 0)
        critical = core.life_state(born_at=_ago(9), tier=0, lifespan_weeks=2.0)
        self.assertTrue(critical["critical"])

    def test_penalty_and_extensions_shift_expiry(self):
        base = core.life_state(born_at=_ago(10), tier=0, lifespan_weeks=2.0)
        extended = core.life_state(born_at=_ago(10), tier=0, lifespan_weeks=2.0, extensions=1)
        self.assertEqual(extended["remaining_days"], base["remaining_days"] + 7)
        penalised = core.life_state(born_at=_ago(10), tier=0, lifespan_weeks=2.0, penalty_weeks=1)
        self.assertEqual(penalised["remaining_days"], max(0, base["remaining_days"] - 7))

    def test_talents_are_stable_and_bounded(self):
        first = core.default_talents("linwanzhou")
        self.assertEqual(first, core.default_talents("linwanzhou"))
        self.assertEqual(set(first), set(core.TALENT_ORDER))
        for key, value in first.items():
            low, high = core.talent_bounds(key)
            self.assertGreaterEqual(value, low)
            self.assertLessEqual(value, high)
        normalized = core.normalize_talents({"insight": 9, "luck": 0.01, "nope": 3}, slug="x")
        self.assertEqual(normalized["insight"], 1.8)
        self.assertEqual(normalized["luck"], 0.6)

    def test_capacity_memory_and_requirements(self):
        self.assertEqual(core.knowledge_capacity_for(0, 1.0), 4000)
        self.assertEqual(core.knowledge_capacity_for(0, 1.5), 6000)
        self.assertEqual(core.knowledge_capacity_for(10, 1.0), 200_000)
        self.assertEqual(core.cultivation_target(0), 10)
        self.assertEqual(core.cultivation_target(1), 24)
        self.assertTrue(all(a < b for a, b in zip(core.TIER_CULTIVATION_TARGETS,
                                                  core.TIER_CULTIVATION_TARGETS[1:])))
        self.assertEqual(core.interaction_requirement(0, 1.0), 3)
        self.assertEqual(core.interaction_requirement(0, 1.8), 2)
        self.assertEqual(core.interaction_requirement(0, 0.6), 5)

    def test_knowledge_gain_uses_insight_and_luck(self):
        self.assertEqual(core.knowledge_gain(200, 1.5, 1.0, lambda: 1.0), (3.0, False))
        self.assertEqual(core.knowledge_gain(200, 1.5, 1.0, lambda: 0.0), (6.0, True))
        self.assertEqual(core.knowledge_gain(0, 1.5, 1.0, lambda: 0.0), (0.0, True))

    def test_penalty_weeks_are_mitigated_by_luck(self):
        self.assertEqual(core.effective_penalty_weeks(2, 1.0), 2.0)
        self.assertEqual(core.effective_penalty_weeks(2, 2.0), 1.0)
        self.assertEqual(core.effective_penalty_weeks(0.5, 0.6), 0.5)


class WandererLifeDbTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute("DELETE FROM wanderer_legacies")
            conn.execute("DELETE FROM wanderer_relationships")
            conn.execute("DELETE FROM wanderer_action_log")
            conn.execute(
                "UPDATE wanderer_agents SET status = 'active', born_at = created_at, lifespan_weeks = 0, lifespan_policy_version=2, "
                "life_gift_in_weeks=0, life_gift_out_weeks=0, life_bonus_seconds=0, "
                "life_penalty_weeks = 0, life_extensions = 0, talents_json = '{}', legacy_id = NULL, "
                "promotion_requested = 0, promotion_requested_at = NULL, appeal_requested = 0, "
                "last_extend_at = NULL, last_appeal_at = NULL, last_resurrect_at = NULL, stats_json = '{}', "
                "cultivation_score = 0"
            )
            conn.commit()

    def _update(self, conn, slug, **fields):
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE wanderer_agents SET {assignments} WHERE slug = ?",
                     (*[_value(value) for value in fields.values()], slug))
        conn.commit()
        return load_wanderer_by_slug(conn, slug)

    def test_hydration_has_talents_and_life(self):
        with self.connect() as conn:
            wanderer = load_wanderer_by_slug(conn, "xiaoman")
        self.assertEqual(set(wanderer["talents"]), set(core.TALENT_ORDER))
        self.assertEqual(wanderer["life"]["lifespan_weeks"],
                         core.lifespan_weeks_for(0, wanderer["talents"]["vitality"]))
        self.assertEqual(wanderer["life"]["born_at"][:10], wanderer["created_at"][:10])

    def test_legacy_lifespan_is_projected_once_without_rewriting_history(self):
        with self.connect() as conn:
            legacy = self._update(conn, "xiaoman", lifespan_policy_version=1, tier=0,
                                  born_at=_ago(15), lifespan_weeks=2.4, life_extensions=1,
                                  life_penalty_weeks=0.5, life_gift_in_weeks=0.25,
                                  life_gift_out_weeks=0.1, life_bonus_seconds=600,
                                  talents_json={**json.loads(TALENTS_NEUTRAL), "vitality": 1.2})
            for status in ("active", "paused"):
                updated = self._update(conn, "xiaoman", status=status)
                raw = dict(conn.execute("SELECT * FROM wanderer_agents WHERE id=?", (legacy["id"],)).fetchone())
                self.assertEqual(raw["lifespan_weeks"], 2.4)
                moment = datetime.now()
                self.assertEqual(current_life(raw, moment), current_life(updated, moment))
                self.assertEqual(current_life(raw)["lifespan_weeks"], 7.2)
                self.assertAlmostEqual(current_life(raw)["granted_weeks"], 7.2 + 1 + .25 + 600 / 604800)
            dead = self._update(conn, "xiaoman", status="dead")
            self.assertEqual(current_life(dead)["lifespan_weeks"], 2.4)
            custom = self._update(conn, "xiaoman", status="active", lifespan_weeks=18)
            self.assertEqual(current_life(custom)["lifespan_weeks"], 18)
            live = self._update(conn, "xiaoman", lifespan_weeks=2.4)
            mark_dead(conn, live["id"], "合成裁决")
            ended = load_wanderer_by_slug(conn, "xiaoman")
            self.assertEqual((ended["lifespan_weeks"], ended["lifespan_policy_version"]), (7.2, 2))

    def test_valid_application_survives_natural_expiry_until_daily_review(self):
        from classroom_app.services.heavenly_dao_service import review_promotion
        from classroom_app.services.wanderer_bond_service import execute_command
        filed = datetime.fromisoformat(_ago(45))
        with self.connect() as conn:
            w = self._update(conn, "xiaoman", tier=0, talents_json=TALENTS_NEUTRAL,
                             born_at=_ago(85), lifespan_weeks=6, cultivation_score=10)
            wid = w["id"]
            conn.execute("INSERT INTO wanderer_knowledge_entries(wanderer_id,source_kind,source_ref,"
                         "digest,chars,created_at,updated_at) VALUES(?,'world','application',?,600,?,?)",
                         (wid, "学"*600, _ago(49), _ago(49)))
            for _ in range(3):
                conn.execute("INSERT INTO wanderer_action_log (wanderer_id,action_kind,outcome,created_at) "
                             "VALUES (?,'reply','ok',?)", (wid, _ago(49)))
            with patch("classroom_app.services.wanderer_life_service.local_now", return_value=filed):
                request = execute_command(conn, w, {"capability": "life.promotion", "args": {"reason": "已实践"}},
                                          operation_id="apply", turn_id="synthetic")
            self.assertTrue(request["submitted"])
            conn.commit()
            pending = load_wanderer_by_slug(conn, "xiaoman")
            self.assertTrue(current_life(pending)["expired"])
            self.assertFalse(promotion_requirements(conn, pending)["ready"])
            self.assertIsNone(settle_natural_expiry(conn, pending))

        async def approve(_endpoint, payload, **kwargs):
            self.assertIn('"qualification_at_submission"', payload["new_message"])
            return {"status": "success", "response_json": {"verdict": "approved", "reason": "已核验"}}

        self.assertEqual(asyncio.run(review_promotion(self.connect, pending, {}, ai_caller=approve)), "approved")
        with self.connect() as conn:
            fresh = load_wanderer_by_slug(conn, "xiaoman")
            self.assertEqual((fresh["tier"], fresh["promotion_requested"], fresh["status"]), (1, 0, "active"))
            self.assertEqual(fresh["lifespan_weeks"], core.lifespan_weeks_for(1, 1.0))
            self.assertFalse(current_life(fresh)["expired"])
            credits = conn.execute("SELECT delta_seconds FROM wanderer_life_ledger WHERE owner_id=? "
                                   "AND kind='promotion_wait'", (wid,)).fetchall()
            self.assertEqual(len(credits), 1)
            self.assertGreaterEqual(credits[0]["delta_seconds"], 42 * 86400)

    def test_late_or_missing_application_cannot_defer_death(self):
        with self.connect() as conn:
            w = self._update(conn, "xiaoman", tier=0, born_at=_ago(45), lifespan_weeks=6,
                             promotion_requested=1, promotion_requested_at=_ago(2))
            self.assertTrue(settle_natural_expiry(conn, w))
            self.assertEqual(load_wanderer_by_slug(conn, "xiaoman")["status"], "dead")

    def test_expiry_is_a_death_reason_unless_lifecycle_is_off(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", born_at=_ago(30), lifespan_weeks=2.0,
                                    talents_json=TALENTS_NEUTRAL)
            reason = death_reason(conn, wanderer)
            wanderer_runtime_setting_set(conn, SETTING_LIFECYCLE_ENABLED, "0")
            conn.commit()
            paused = death_reason(conn, wanderer)
            wanderer_runtime_setting_set(conn, SETTING_LIFECYCLE_ENABLED, "1")
            conn.commit()
        self.assertIn("寿元耗尽", reason)
        self.assertEqual(paused, "")

    def test_extend_life_burns_cultivation_and_is_capped(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", born_at=_ago(11), lifespan_weeks=2.0,
                                    cultivation_score=40.0, life_extensions=0, talents_json=TALENTS_NEUTRAL)
            first = extend_life(conn, wanderer)
            conn.commit()
            self.assertTrue(first["ok"])
            self.assertEqual(first["cost"], 10.0)
            after = load_wanderer_by_slug(conn, "xiaoman")
            self.assertEqual(after["life_extensions"], 1)
            self.assertGreater(after["life"]["remaining_days"], wanderer["life"]["remaining_days"])
            self._update(conn, "xiaoman", born_at=_ago(13), life_extensions=2)
            capped = extend_life(conn, load_wanderer_by_slug(conn, "xiaoman"))
        self.assertFalse(capped["ok"])
        self.assertIn("次数", capped["reason"])

    def test_promotion_requirements_are_transparent(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", tier=0, knowledge_chars=2400, cultivation_score=30.0,
                                    promotion_requested=0, promotion_requested_at=None, talents_json=TALENTS_NEUTRAL)
            conn.execute("INSERT INTO wanderer_knowledge_entries(wanderer_id,source_kind,source_ref,title,digest,chars,status,created_at,updated_at) VALUES(?,'world','fixture','实际学习',?,2400,'active',?,?)",(wanderer['id'],'学习'*1200,_ago(1),_ago(1)))
            for _index in range(5):
                conn.execute(
                    "INSERT INTO wanderer_action_log (wanderer_id, action_kind, outcome, detail_json, created_at) "
                    "VALUES (?, 'comment', 'ok', '{}', ?)", (wanderer["id"], _ago(1)),
                )
            conn.commit()
            requirements = promotion_requirements(conn, load_wanderer_by_slug(conn, "xiaoman"))
            self.assertTrue(requirements["ready"], requirements)
            self.assertEqual(requirements["knowledge_target"], 600)
            self.assertEqual(requirements["cultivation_target"], 10)
            self.assertEqual(requirements["interactions_needed"], 3)
            self._update(conn, "xiaoman", promotion_requested_at=datetime.now().isoformat(timespec="seconds"))
            blocked = promotion_requirements(conn, load_wanderer_by_slug(conn, "xiaoman"))
        self.assertFalse(blocked["ready"])
        self.assertGreater(blocked["cooldown_days_left"], 0)

    def test_interaction_cultivation_has_a_daily_cap(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", cultivation_score=0.0, talents_json=TALENTS_NEUTRAL,
                                    stats_json="{}")
            granted = sum(award_interaction_cultivation(conn, wanderer, ["social_message"]) for _ in range(30))
            conn.commit()
            self.assertAlmostEqual(granted, 9.0, places=2)
            conn.execute("UPDATE wanderer_agents SET stats_json = ? WHERE id = ?",
                         ('{"cult_day":"2000-01-01","cult_today":0}', wanderer["id"]))
            conn.commit()
            next_day = award_interaction_cultivation(conn, load_wanderer_by_slug(conn, "xiaoman"), ["social_message"])
            score = load_wanderer_by_slug(conn, "xiaoman")["cultivation_score"]
        self.assertAlmostEqual(next_day, 0.3, places=2)
        self.assertAlmostEqual(score, 9.3, places=2)

    def test_penalty_can_end_a_life_and_keeps_a_legacy(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", born_at=_ago(13), lifespan_weeks=2.0,
                                    life_penalty_weeks=0.0, talents_json=TALENTS_NEUTRAL)
            result = apply_life_penalty(conn, wanderer, 1, reason="屡劝不改")
            conn.commit()
            dead = load_wanderer_by_slug(conn, "xiaoman")
            legacy = conn.execute("SELECT status, death_reason FROM wanderer_legacies WHERE wanderer_id = ?",
                                  (wanderer["id"],)).fetchone()
        self.assertTrue(result["died"])
        self.assertEqual(dead["status"], "dead")
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy["status"], "pending")
        self.assertIn("天罚", legacy["death_reason"])

    def test_talent_change_is_clamped_and_recomputed(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", tier=0, lifespan_weeks=2.0, talents_json=TALENTS_NEUTRAL)
            result = apply_talent_change(conn, wanderer, "vitality", 0.5, reason="天道赐福")
            conn.commit()
            fresh = load_wanderer_by_slug(conn, "xiaoman")
            payload = life_payload(fresh)
        self.assertTrue(result["changed"])
        self.assertEqual(result["to"], 1.4)
        self.assertEqual(fresh["talents"]["vitality"], 1.4)
        self.assertAlmostEqual(payload["lifespan_weeks"], 1.4 * core.BASE_LIFESPAN_WEEKS[0])

    def test_mark_dead_records_expiry_and_legacy(self):
        with self.connect() as conn:
            wanderer = self._update(conn, "xiaoman", born_at=_ago(30), lifespan_weeks=2.0,
                                    talents_json=TALENTS_NEUTRAL)
            review_id = mark_dead(conn, wanderer["id"], death_reason(conn, wanderer))
            conn.commit()
            review = conn.execute("SELECT review_kind, verdict FROM wanderer_dao_reviews WHERE id = ?",
                                  (review_id,)).fetchone()
        self.assertEqual((review["review_kind"], review["verdict"]), ("death", "died"))


if __name__ == "__main__":
    unittest.main()
