import unittest

from classroom_app.services.wanderer_identity_service import (
    evaluate_death,
    knowledge_capacity_chars,
    load_wanderer,
    mark_dead,
    wisdom_directive,
)
from classroom_app.services.wanderer_knowledge_service import (
    add_knowledge,
    forget_all,
    knowledge_summary,
    select_knowledge_for_scene,
)
from tests.wanderer_test_support import WandererDbTestCase


def _statuses(conn, wanderer_id):
    rows = conn.execute(
        "SELECT source_ref, status FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND source_kind = 'material'",
        (wanderer_id,),
    ).fetchall()
    return {row["source_ref"]: row["status"] for row in rows}


class TierHelpersTest(unittest.TestCase):
    def test_capacity_grows_and_caps(self):
        self.assertEqual(knowledge_capacity_chars(0), 4000)
        self.assertEqual(knowledge_capacity_chars(1), 8000)
        self.assertEqual(knowledge_capacity_chars(2), 12800)
        self.assertEqual(knowledge_capacity_chars(10), 200_000)
        self.assertEqual(knowledge_capacity_chars(-3), 4000)

    def test_wisdom_bands_differ(self):
        bands = {wisdom_directive(tier) for tier in (0, 3, 6, 9)}
        self.assertEqual(len(bands), 4)
        self.assertEqual(wisdom_directive(1), wisdom_directive(2))


class WandererKnowledgeTest(WandererDbTestCase):
    def test_capacity_forgets_oldest_least_used(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "xiaoman")  # tier 0 -> 4000 chars
            conn.execute("UPDATE wanderer_knowledge_entries SET use_count = 5 WHERE wanderer_id = ?", (wanderer["id"],))
            for ref in ("k1", "k2", "k3", "k4"):
                result = add_knowledge(conn, wanderer, source_kind="material", source_ref=ref, title=ref,
                                       digest="知" * 1000, tags=["画画"])
            self.assertEqual(result["forgotten"], [conn.execute(
                "SELECT id FROM wanderer_knowledge_entries WHERE source_ref = 'k1'").fetchone()[0]])
            conn.execute("UPDATE wanderer_knowledge_entries SET use_count = 3 WHERE source_ref = 'k2'")
            add_knowledge(conn, wanderer, source_kind="material", source_ref="k5", title="k5",
                          digest="识" * 1000, tags=["画画"])
            statuses = _statuses(conn, wanderer["id"])
            summary = knowledge_summary(conn, wanderer)
            forget_logs = conn.execute(
                "SELECT COUNT(*) FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'knowledge_forget'",
                (wanderer["id"],),
            ).fetchone()[0]
            stored_chars = load_wanderer(conn, wanderer["id"])["knowledge_chars"]
            conn.rollback()
        self.assertEqual(statuses, {"k1": "forgotten", "k2": "active", "k3": "forgotten", "k4": "active", "k5": "active"})
        self.assertEqual(forget_logs, 2)
        self.assertLessEqual(summary["chars"], summary["capacity"])
        self.assertEqual(summary["capacity"], 4000)
        self.assertEqual(stored_chars, summary["chars"])

    def test_upsert_does_not_duplicate_or_double_count(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "chenmo")
            before = load_wanderer(conn, wanderer["id"])["cultivation_score"]
            first = add_knowledge(conn, wanderer, source_kind="material_page", source_ref="12:3", title="页",
                                  digest="a" * 200, tags=["编程"])
            second = add_knowledge(conn, wanderer, source_kind="material_page", source_ref="12:3", title="页",
                                   digest="a" * 250, tags=["编程"])
            rows = conn.execute(
                "SELECT COUNT(*) FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND source_ref = '12:3'",
                (wanderer["id"],),
            ).fetchone()[0]
            after = load_wanderer(conn, wanderer["id"])["cultivation_score"]
            conn.rollback()
        self.assertEqual(rows, 1)
        self.assertEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(first["score_delta"], 2.0)
        self.assertEqual(second["score_delta"], 0.5)
        self.assertAlmostEqual(after - before, 2.5)

    def test_rejects_bad_input(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "chenmo")
            with self.assertRaises(ValueError):
                add_knowledge(conn, wanderer, source_kind="rumor", source_ref="x", title="t", digest="d")
            with self.assertRaises(ValueError):
                add_knowledge(conn, wanderer, source_kind="seed", source_ref="x", title="t", digest="   ")

    def test_scene_selection_prefers_tag_overlap_and_bumps_use(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "suqinghe")
            picked = select_knowledge_for_scene(conn, wanderer, ["数据结构"], 1)
            count = conn.execute(
                "SELECT use_count FROM wanderer_knowledge_entries WHERE id = ?", (picked[0]["id"],)
            ).fetchone()[0]
            conn.rollback()
        self.assertEqual(picked[0]["title"], "栈与队列")
        self.assertEqual(count, 1)

    def test_death_after_wipe_at_tier_zero(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lurenjia")  # tier 0 with seed knowledge
            self.assertFalse(evaluate_death(conn, wanderer))
            forgotten = forget_all(conn, wanderer["id"])
            wiped = load_wanderer(conn, wanderer["id"])
            self.assertEqual(forgotten, 3)
            self.assertEqual(wiped["knowledge_chars"], 0)
            self.assertTrue(evaluate_death(conn, wiped))
            self.assertFalse(evaluate_death(conn, {**wiped, "tier": 1}))
            self.assertFalse(evaluate_death(conn, {**wiped, "cultivation_score": 0.5}))
            review_id = mark_dead(conn, wanderer["id"], "知识尽失")
            review = conn.execute(
                "SELECT review_kind, verdict, reason FROM wanderer_dao_reviews WHERE id = ?", (review_id,)
            ).fetchone()
            dead = load_wanderer(conn, wanderer["id"])
            conn.rollback()
        self.assertEqual((review["review_kind"], review["verdict"], review["reason"]), ("death", "died", "知识尽失"))
        self.assertEqual(dead["status"], "dead")
        self.assertTrue(dead["is_dead"])


if __name__ == "__main__":
    unittest.main()
