import unittest
from datetime import timedelta

from classroom_app.time_utils import local_now
from classroom_app.services.wanderer_emotion_service import (
    apply_turn_mood,
    effective_mood,
    mood_from_turn,
    save_mood,
    tuning_for_today,
)
from tests.wanderer_test_support import WandererDbTestCase


class EmotionUnitTest(unittest.TestCase):
    def test_mood_decays_toward_baseline(self):
        old = (local_now() - timedelta(hours=18)).isoformat(timespec="seconds")
        mood = effective_mood({"mood": {"emotion": "雀跃", "valence": 0.8, "arousal": 0.8,
                                        "updated_at": old}})
        self.assertAlmostEqual(mood["valence"], 0.4, places=2)
        self.assertAlmostEqual(mood["arousal"], 0.575, places=2)
        self.assertEqual(mood["emotion"], "雀跃")

    def test_turn_emotion_parses_and_clamps(self):
        mood = mood_from_turn({"emotion": {"name": "烦躁", "valence": -9, "arousal": 9, "reason": "被敷衍"}})
        self.assertEqual(mood["emotion"], "烦躁")
        self.assertEqual((mood["valence"], mood["arousal"]), (-1.0, 1.0))
        self.assertEqual(mood["cause"], "被敷衍")

    def test_tuning_is_bound_to_next_day(self):
        wanderer = {"self_tuning": {"next_day": {"effective_date": "2026-10-31", "proactive_multiplier": 9,
                                                 "intentions": ["早睡", "学习"]}}}
        today = tuning_for_today(wanderer, today="2026-10-31")
        self.assertEqual(today["proactive_multiplier"], 1.3)
        self.assertEqual(today["intentions"], ["早睡", "学习"])
        stale = tuning_for_today(wanderer, today="2026-11-01")
        self.assertEqual(stale["proactive_multiplier"], 1.0)


class EmotionDbTest(WandererDbTestCase):
    def test_turn_mood_is_persisted_and_reloaded(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            apply_turn_mood(conn, wanderer, {"emotion": {"name": "失落", "valence": -0.6, "arousal": 0.4,
                                                         "reason": "比赛输了"}})
            conn.commit()
            fresh = self.wanderer(conn, "atai")
        self.assertEqual(fresh["mood"]["emotion"], "失落")
        self.assertAlmostEqual(fresh["mood"]["valence"], -0.6)
        self.assertEqual(fresh["mood"]["cause"], "比赛输了")

    def test_save_mood_clamps(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "momo")
            save_mood(conn, wanderer, {"emotion": "x" * 40, "valence": -4, "arousal": 3, "cause": "y" * 400})
            conn.commit()
            fresh = self.wanderer(conn, "momo")
        self.assertEqual(len(fresh["mood"]["emotion"]), 20)
        self.assertEqual((fresh["mood"]["valence"], fresh["mood"]["arousal"]), (-1.0, 1.0))
        self.assertEqual(len(fresh["mood"]["cause"]), 160)


if __name__ == "__main__":
    unittest.main()
