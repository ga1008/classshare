"""Deterministic balance check for the lifecycle numbers (docs/wanderer-lifecycle-2026-10-30.md §9).

Pure arithmetic: no database, no model, no clock. A wanderer's day is reduced to
`daily_action_budget × study_share` readings of ~500 chars, with the real
lifespan/capacity/target formulas. The goal is the design promise: a large
majority of personas avoid an early death through basic effort, while a
wanderer who never studies still dies at tier 0.
"""
import unittest

from classroom_app.services import wanderer_life_core as core
from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS

STUDY_SHARE = 0.25
CHARS_PER_STUDY = 500
MAX_DAYS = 1200


def simulate(budget, *, insight=1.0, memory=1.0, vitality=1.0, share=STUDY_SHARE):
    """(died, days_lived, final_tier, promotion_days) for one deterministic life."""
    tier, days, knowledge, cultivation, last = 0, 0, 0.0, 0.0, -99
    promotions: list[int] = []
    while tier < core.MAX_TIER and days < MAX_DAYS:
        if days > core.lifespan_weeks_for(tier, vitality) * 7:
            return True, days, tier, promotions
        days += 1
        gained = budget * share * CHARS_PER_STUDY
        capacity = core.knowledge_capacity_for(tier, memory)
        knowledge = min(capacity, knowledge + gained)
        cultivation += gained / 100.0 * insight
        if (knowledge >= core.promotion_ratio(tier) * capacity
                and cultivation >= core.cultivation_target(tier)
                and days - last >= 7):
            tier += 1
            last = days
            promotions.append(days)
    died = days > core.lifespan_weeks_for(tier, vitality) * 7
    return died, days, tier, promotions


class LifeBalanceTest(unittest.TestCase):
    def test_most_personas_survive_basic_effort(self):
        survived_five = survived_ten = 0
        for seed in WANDERER_SEEDS:
            talents = core.default_talents(seed["slug"])
            died, _days, tier, _promotions = simulate(
                int(seed.get("daily_action_budget") or 10), insight=talents["insight"],
                memory=talents["memory"], vitality=talents["vitality"],
            )
            self.assertFalse(died, seed["slug"])
            survived_five += int(tier >= 5)
            survived_ten += int(tier >= 10)
        self.assertGreaterEqual(survived_five, 18)
        self.assertGreaterEqual(survived_ten, 15)

    def test_no_effort_dies_at_tier_zero(self):
        died, days, tier, _promotions = simulate(10, share=0.0)
        self.assertTrue(died)
        self.assertEqual(tier, 0)
        self.assertLessEqual(days, 15)

    def test_higher_tiers_buy_time_and_budget(self):
        low = core.tier_perks(0, 10)
        high = core.tier_perks(10, 10)
        self.assertLess(low["daily_budget"], high["daily_budget"])
        self.assertLess(low["lifespan_weeks"], high["lifespan_weeks"])
        self.assertFalse(low["appeal_unlocked"])
        self.assertTrue(high["resurrect_unlocked"])

    def test_ambition_reaches_the_top_sooner(self):
        lazy = simulate(4, insight=1.0, memory=1.0, vitality=1.0)
        eager = simulate(14, insight=1.2, memory=1.1, vitality=1.1)
        self.assertFalse(lazy[0])
        self.assertFalse(eager[0])
        self.assertEqual(lazy[2], 10)
        self.assertGreater(lazy[1], eager[1])


if __name__ == "__main__":
    unittest.main()
