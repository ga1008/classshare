import unittest

from classroom_app.services.wanderer_identity_service import load_wanderer_by_slug
from classroom_app.services.wanderer_relationship_service import (
    affinity_label,
    apply_turn_feeling,
    grieve_death,
    list_relationships,
    mutual_affinity,
    note_interaction,
    public_summary,
    record_feeling,
    relationship_block,
)
from tests.wanderer_test_support import WandererDbTestCase

TALENTS = '{"insight":1.0,"memory":1.0,"vitality":1.0,"eloquence":1.0,"charm":%s,"luck":1.0}'


class RelationshipTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute("DELETE FROM wanderer_relationships")
            conn.execute("DELETE FROM wanderer_legacies")
            conn.execute("DELETE FROM wanderer_action_log")
            conn.execute("UPDATE wanderer_agents SET status = 'active', born_at = created_at, "
                         "lifespan_weeks = 0, life_penalty_weeks = 0, life_extensions = 0, legacy_id = NULL")
            conn.commit()

    def _wanderer(self, conn, slug, charm=1.0):
        conn.execute("UPDATE wanderer_agents SET talents_json = ? WHERE slug = ?", (TALENTS % charm, slug))
        conn.commit()
        return load_wanderer_by_slug(conn, slug)

    def test_positive_and_negative_feelings_scale_with_charm(self):
        with self.connect() as conn:
            kind = self._wanderer(conn, "linwanzhou", charm=1.5)
            up = record_feeling(conn, kind, "student:4242", delta=2, mood="高兴", reason="聊得来", scene="social_dm")
            down = record_feeling(conn, kind, "student:4242", delta=-4, mood="厌烦", reason="对方刻薄")
            conn.commit()
        self.assertEqual(up["affinity"], 3.0)
        self.assertEqual(down["affinity"], 0.3)  # 3.0 − 2.7

    def test_affinity_is_clamped_and_labeled(self):
        with self.connect() as conn:
            wanderer = self._wanderer(conn, "linwanzhou", charm=1.0)
            for _ in range(30):
                record_feeling(conn, wanderer, "student:4242", delta=5)
                record_feeling(conn, wanderer, "teacher:7", delta=-5)
            conn.commit()
            high = conn.execute("SELECT affinity FROM wanderer_relationships WHERE peer_identity='student:4242'").fetchone()
            low = conn.execute("SELECT affinity FROM wanderer_relationships WHERE peer_identity='teacher:7'").fetchone()
        self.assertEqual(high["affinity"], 100.0)
        self.assertEqual(low["affinity"], -100.0)
        self.assertEqual(affinity_label(high["affinity"]), "挚友")
        self.assertEqual(affinity_label(low["affinity"]), "厌恶")

    def test_no_feeling_only_counts_the_interaction(self):
        with self.connect() as conn:
            wanderer = self._wanderer(conn, "linwanzhou")
            note_interaction(conn, wanderer, "student:4242", scene="social_dm")
            conn.commit()
            row = conn.execute("SELECT affinity, interactions FROM wanderer_relationships LIMIT 1").fetchone()
            unchanged = list_relationships(conn, wanderer["id"])[0]
        self.assertEqual(row["affinity"], 0.0)
        self.assertEqual(row["interactions"], 1)
        self.assertEqual(unchanged["label"], "平常")

    def test_prompt_block_is_bounded_and_carries_reason(self):
        with self.connect() as conn:
            wanderer = self._wanderer(conn, "linwanzhou")
            for index in range(8):
                for _ in range(5):  # 5 × 5 = 25 affinity → 亲近
                    record_feeling(conn, wanderer, f"student:{500 + index}", delta=5, mood="平静",
                                   reason=f"第{index}次聊得不错")
            conn.commit()
            block = relationship_block(conn, wanderer)
        self.assertIn("【你与他人的关系】", block)
        self.assertIn("亲近", block)
        self.assertIn("聊得不错", block)
        self.assertLessEqual(block.count("\n"), 7)  # intro + at most six people

    def test_grief_lowers_affinity_of_those_who_cared(self):
        with self.connect() as conn:
            mourner = self._wanderer(conn, "linwanzhou")
            friend = load_wanderer_by_slug(conn, "suqinghe")
            for _ in range(10):
                record_feeling(conn, mourner, f"student:{friend['student_id']}", delta=5)
            conn.commit()
            touched = grieve_death(conn, friend)
            conn.commit()
            row = conn.execute("SELECT affinity, last_mood, last_reason FROM wanderer_relationships "
                               "WHERE wanderer_id = ?", (mourner["id"],)).fetchone()
        self.assertEqual(touched, 1)
        self.assertEqual(row["affinity"], 44.0)
        self.assertEqual(row["last_mood"], "难过")
        self.assertIn("陨落", row["last_reason"])

    def test_mutual_affinity_needs_both_directions(self):
        with self.connect() as conn:
            a = self._wanderer(conn, "linwanzhou")
            b = load_wanderer_by_slug(conn, "suqinghe")
            for _ in range(9):
                record_feeling(conn, a, f"student:{b['student_id']}", delta=5)
            conn.commit()
            one_sided = mutual_affinity(conn, a, f"student:{b['student_id']}", minimum=40)
            for _ in range(9):
                record_feeling(conn, b, f"student:{a['student_id']}", delta=5)
            conn.commit()
            both = mutual_affinity(conn, a, f"student:{b['student_id']}", minimum=40)
        self.assertFalse(one_sided)
        self.assertTrue(both)

    def test_public_summary_counts_bonds(self):
        with self.connect() as conn:
            wanderer = self._wanderer(conn, "linwanzhou", charm=1.0)
            for _ in range(5):
                record_feeling(conn, wanderer, "student:4242", delta=5)
                record_feeling(conn, wanderer, "student:4243", delta=-5)
            record_feeling(conn, wanderer, "student:4244", delta=0)
            conn.commit()
            summary = public_summary(conn, wanderer["id"])
        self.assertEqual(summary, {"total": 3, "close": 1, "dislike": 1})

    def test_apply_turn_feeling_accepts_parsed_turn(self):
        with self.connect() as conn:
            wanderer = self._wanderer(conn, "linwanzhou")
            apply_turn_feeling(conn, wanderer, "student:4242",
                               {"feeling": {"affinity": 2, "reason": "很真诚"}, "mood": "开心"}, scene="social_dm")
            conn.commit()
            row = conn.execute("SELECT affinity, last_reason FROM wanderer_relationships").fetchone()
        self.assertEqual(row["affinity"], 2.0)
        self.assertEqual(row["last_reason"], "很真诚")


if __name__ == "__main__":
    unittest.main()
