"""Budget-limited life simulation with actual tier, income, cooldown and lifespan rules."""
import unittest

from classroom_app.services import wanderer_life_core as core
from classroom_app.services.wanderer_persona_seeds import WANDERER_SEEDS

CHARS_PER_STUDY = 240


def simulate(daily_share, *, insight=1.0, memory=1.0, vitality=1.0, eloquence=1.0,
             studies=True, chars=CHARS_PER_STUDY):
    """No bonds, critical hits or extensions. Pay for reads, writes, replies and applications."""
    tier = day = knowledge = 0
    score = 0.0
    replies = []
    promotions = []
    pending = False
    while tier < core.MAX_TIER:
        if day >= core.lifespan_weeks_for(tier, vitality) * 7:
            return True, day, tier, promotions
        day += 1
        if pending:
            tier += 1
            promotions.append(day)
            pending = False
            if tier == core.MAX_TIER:
                break
        credits = min(daily_share, core.ai_wake_limit(tier))
        interactions = core.interaction_requirement(tier, eloquence)
        replies = [d for d in replies if day - d < 7]
        if len(replies) < interactions and credits:
            replies.append(day)
            score += core.interaction_gain("social_message", eloquence, source_tier=tier)
            credits -= 1
        capacity = core.knowledge_capacity_for(tier, memory)
        target = round(core.promotion_ratio(tier) * capacity)
        if (knowledge >= target and score >= core.cultivation_target(tier)
                and len(replies) >= interactions and credits
                and (not promotions or day + 1 - promotions[-1] >= core.PROMOTION_COOLDOWN_DAYS)):
            pending = True
            credits -= 1
        if studies:
            for _ in range(credits // 2):
                knowledge = min(capacity, knowledge + chars)
                gain, _crit = core.knowledge_gain(chars, insight, 1, lambda: 1.0, tier=tier)
                score += gain
    return False, day, tier, promotions


class LifeBalanceTest(unittest.TestCase):
    def test_most_personas_survive_basic_effort(self):
        survived_five = survived_ten = 0
        for seed in WANDERER_SEEDS:
            talents = core.default_talents(seed["slug"])
            died, _days, tier, _promotions = simulate(
                3, eloquence=talents["eloquence"], insight=talents["insight"],
                memory=talents["memory"], vitality=talents["vitality"],
            )
            self.assertFalse(died, seed["slug"])
            survived_five += int(tier >= 5)
            survived_ten += int(tier >= 10)
        self.assertGreaterEqual(survived_five, 18)
        self.assertGreaterEqual(survived_ten, 15)

    def test_every_tier_is_reachable_at_three_readings_a_day(self):
        for tier in range(core.MAX_TIER):
            plan = core.promotion_feasibility(tier, studies_per_day=3.0)
            self.assertTrue(plan["fits"], plan)
            self.assertLessEqual(plan["days_needed"], plan["days_available"])

    def test_requirements_rise_while_income_also_rises(self):
        self.assertTrue(all(a < b for a, b in zip(core.TIER_CULTIVATION_TARGETS,
                                                  core.TIER_CULTIVATION_TARGETS[1:])))
        knowledge_targets = [
            core.promotion_ratio(tier) * core.base_knowledge_capacity(tier)
            for tier in range(core.MAX_TIER)
        ]
        self.assertTrue(all(a < b for a, b in zip(knowledge_targets, knowledge_targets[1:])))
        self.assertLess(core.tier_income_multiplier(0), core.tier_income_multiplier(9))
        self.assertLess(core.tier_income_multiplier(0, knowledge=False),
                        core.tier_income_multiplier(9, knowledge=False))

    def test_tier_zero_quota_reaches_promotion_before_death(self):
        plan = core.promotion_plan(0)
        self.assertTrue(plan["fits"], plan)
        self.assertEqual(plan["quota"], 3)
        self.assertEqual(plan["days_available"], 42.0)

        self.assertLessEqual(plan["days_needed"], 8.0)
        self.assertGreater(plan["margin"], 70)
        weak = core.promotion_plan(0, insight=0.6, eloquence=0.6, memory=0.6, vitality=0.7)
        self.assertTrue(weak["fits"], weak)

    def test_every_tier_fits_its_wake_quota(self):
        for tier in range(core.MAX_TIER):
            plan = core.promotion_plan(tier)
            self.assertTrue(plan["fits"], plan)
            self.assertLessEqual(plan["days_needed"], plan["days_available"], plan)

    def test_no_effort_dies_at_tier_zero(self):
        died, days, tier, _promotions = simulate(3, studies=False)
        self.assertTrue(died)
        self.assertEqual(tier, 0)
        self.assertEqual(days, 42)

    def test_higher_tiers_buy_time_and_budget(self):
        low = core.tier_perks(0, 10)
        high = core.tier_perks(10, 10)
        self.assertLess(low["daily_budget"], high["daily_budget"])
        self.assertLess(low["lifespan_weeks"], high["lifespan_weeks"])
        self.assertFalse(low["appeal_unlocked"])
        self.assertTrue(high["resurrect_unlocked"])

    def test_ambition_reaches_the_top_sooner(self):
        lazy = simulate(3, insight=1.0, memory=1.0, vitality=1.0)
        eager = simulate(4, insight=1.2, memory=1.1, vitality=1.1)
        self.assertFalse(lazy[0])
        self.assertFalse(eager[0])
        self.assertEqual(lazy[2], 10)
        self.assertGreater(lazy[1], eager[1])


if __name__ == "__main__":
    unittest.main()
