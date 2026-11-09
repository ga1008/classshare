import unittest

from classroom_app.services import wanderer_life_core as core
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
from classroom_app.services.wanderer_prompt_service import _knowledge_depth_block, _knowledge_digest
from classroom_app.services.wanderer_text_style import (
    KNOWLEDGE_DEPTH_HIGH,
    KNOWLEDGE_DEPTH_LOW,
    KNOWLEDGE_DEPTH_MID,
)
from tests.wanderer_test_support import WandererDbTestCase


NEUTRAL_TALENTS = '{"insight":1.0,"memory":1.0,"vitality":1.0,"eloquence":1.0,"charm":1.0,"luck":1.0}'


def _neutral(conn, wanderer):
    """Talent-neutral copy: capacity/gain tests assert the base formula, not a persona's gifts."""
    conn.execute("UPDATE wanderer_agents SET talents_json = ? WHERE id = ?", (NEUTRAL_TALENTS, wanderer["id"]))
    return load_wanderer(conn, wanderer["id"])


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


class KnowledgeDepthTest(unittest.TestCase):
    def test_digest_caps_follow_tier(self):
        long = "第一句。" + "细节" * 300
        low = _knowledge_digest(long, 0)
        mid = _knowledge_digest(long, 4)
        high = _knowledge_digest(long, 8)
        self.assertLessEqual(len(low), 201)
        self.assertLessEqual(len(mid), 441)
        self.assertLessEqual(len(high), 681)
        self.assertLess(len(low), len(mid))
        self.assertLess(len(mid), len(high))
        self.assertTrue(low.startswith("第一句。"))
        self.assertTrue(low.endswith("…"))

    def test_depth_block_bands(self):
        self.assertEqual(_knowledge_depth_block({"tier": 0}), KNOWLEDGE_DEPTH_LOW)
        self.assertEqual(_knowledge_depth_block({"tier": 3}), KNOWLEDGE_DEPTH_MID)
        self.assertEqual(_knowledge_depth_block({"tier": 6}), KNOWLEDGE_DEPTH_HIGH)


class WandererKnowledgeTest(WandererDbTestCase):
    def test_capacity_forgets_least_used_without_exceeding_capacity(self):
        with self.connect() as conn:
            wanderer = _neutral(conn, self.wanderer(conn, "xiaoman"))  # tier 0 -> 4000 chars, 1000/note
            conn.execute("DELETE FROM wanderer_knowledge_entries WHERE wanderer_id = ?", (wanderer["id"],))
            conn.execute("UPDATE wanderer_agents SET knowledge_chars = 0 WHERE id = ?", (wanderer["id"],))
            conn.execute("UPDATE wanderer_knowledge_entries SET use_count = 5 WHERE wanderer_id = ?", (wanderer["id"],))
            for ref in ("k1", "k2", "k3", "k4"):
                result = add_knowledge(conn, wanderer, source_kind="material", source_ref=ref, title=ref,
                                       digest="知" * 1000, tags=["画画"])
            self.assertEqual(result["forgotten"], [])  # 4 × 1000 = exactly 4000
            result = add_knowledge(conn, wanderer, source_kind="material", source_ref="k5", title="k5",
                                   digest="识" * 1000, tags=["画画"])
            self.assertTrue(result["forgotten"])
            conn.execute("UPDATE wanderer_knowledge_entries SET use_count = 3 WHERE source_ref = 'k2'")
            result = add_knowledge(conn, wanderer, source_kind="material", source_ref="k6", title="k6",
                                   digest="记" * 1000, tags=["画画"])
            self.assertTrue(result["forgotten"])
            statuses = _statuses(conn, wanderer["id"])
            summary = knowledge_summary(conn, wanderer)
            forget_logs = conn.execute(
                "SELECT COUNT(*) FROM wanderer_action_log WHERE wanderer_id = ? AND action_kind = 'knowledge_forget'",
                (wanderer["id"],),
            ).fetchone()[0]
            stored_chars = load_wanderer(conn, wanderer["id"])["knowledge_chars"]
            conn.rollback()
        self.assertEqual(statuses["k6"], "active")
        self.assertGreaterEqual(sum(1 for value in statuses.values() if value == "forgotten"), 2)
        self.assertGreaterEqual(forget_logs, 2)
        self.assertLessEqual(summary["chars"], summary["capacity"])
        self.assertEqual(summary["capacity"], 4000)
        self.assertEqual(stored_chars, summary["chars"])

    def test_upsert_does_not_duplicate_or_double_count(self):
        with self.connect() as conn:
            wanderer = _neutral(conn, self.wanderer(conn, "chenmo"))
            before = load_wanderer(conn, wanderer["id"])["cultivation_score"]
            no_crit = lambda: 1.0  # noqa: E731 - deterministic 顿悟 roll
            first = add_knowledge(conn, wanderer, source_kind="material_page", source_ref="12:3", title="页",
                                  digest="a" * 200, tags=["编程"], rng=no_crit)
            second = add_knowledge(conn, wanderer, source_kind="material_page", source_ref="12:3", title="页",
                                   digest="a" * 250, tags=["编程"], rng=no_crit)
            rows = conn.execute(
                "SELECT COUNT(*) FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND source_ref = '12:3'",
                (wanderer["id"],),
            ).fetchone()[0]
            after = load_wanderer(conn, wanderer["id"])["cultivation_score"]
            tier = load_wanderer(conn, wanderer["id"]).get("tier")
            conn.rollback()
        self.assertEqual(rows, 1)
        self.assertEqual(first["entry_id"], second["entry_id"])
        # chenmo sits at a higher tier, so both reads carry the tier income bonus.
        bonus = core.tier_income_multiplier(tier)
        self.assertAlmostEqual(first["score_delta"], round(2.0 * bonus, 2))
        self.assertAlmostEqual(second["score_delta"], round(0.5 * bonus, 2))
        self.assertAlmostEqual(after - before, round(2.5 * bonus, 2))

    def test_shortening_and_archiving_do_not_reopen_rewarded_source_growth(self):
        with self.connect() as conn:
            wanderer=_neutral(conn,self.wanderer(conn,'chenmo'))
            def write(text):
                return add_knowledge(conn,wanderer,source_kind='material_page',source_ref='growth:one',
                                     title='Source growth',digest=text,rng=lambda:1.0)
            first=write('a'*200)
            short=write('b'*50)
            rebuilt=write('c'*200)
            conn.execute("UPDATE wanderer_knowledge_entries SET status='forgotten' WHERE id=?",(first['entry_id'],))
            restored=write('d'*200)
            larger=write('e'*250)
            bonus=core.tier_income_multiplier(wanderer['tier'])
            self.assertEqual([short['score_delta'],rebuilt['score_delta'],restored['score_delta']],[0,0,0])
            self.assertAlmostEqual(larger['score_delta'],round(0.5*bonus,2))
            self.assertEqual(conn.execute('SELECT rewarded_chars FROM wanderer_knowledge_growth WHERE entry_id=?',(first['entry_id'],)).fetchone()[0],250)
            conn.rollback()

    def test_growth_migration_keeps_larger_archived_revision(self):
        import json
        from classroom_app.time_utils import local_iso
        with self.connect() as conn:
            wanderer=_neutral(conn,self.wanderer(conn,'chenmo'))
            args=dict(source_kind='material_page',source_ref='growth:migration',title='Migration',rng=lambda:1.0)
            first=add_knowledge(conn,wanderer,digest='a'*50,**args)
            conn.execute('DELETE FROM wanderer_knowledge_growth WHERE entry_id=?',(first['entry_id'],))
            conn.execute('INSERT INTO wanderer_knowledge_revisions(entry_id,revision,wanderer_id,turn_id,record_json,created_at) VALUES(?,1,?,?,?,?)',
                         (first['entry_id'],wanderer['id'],'historical',json.dumps({'chars':200,'digest':'old'*66}),local_iso()))
            rebuilt=add_knowledge(conn,wanderer,digest='b'*200,**args)
            self.assertEqual(rebuilt['score_delta'],0)
            self.assertEqual(conn.execute('SELECT rewarded_chars FROM wanderer_knowledge_growth WHERE entry_id=?',(first['entry_id'],)).fetchone()[0],200)
            conn.rollback()

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
            add_knowledge(conn,wanderer,source_kind='material_page',source_ref='fixture:stacks',title='栈与队列',
                          digest='栈后进先出，队列先进先出。',tags=['数据结构'],rng=lambda:1.0)
            picked = select_knowledge_for_scene(conn, wanderer, ["数据结构"], 1)
            count = conn.execute(
                "SELECT use_count FROM wanderer_knowledge_entries WHERE id = ?", (picked[0]["id"],)
            ).fetchone()[0]
            conn.rollback()
        self.assertEqual(picked[0]["title"], "栈与队列")
        self.assertEqual(count, 1)

    def test_empty_knowledge_does_not_kill_at_tier_zero(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lurenjia")
            self.assertFalse(evaluate_death(conn, wanderer))
            forgotten = forget_all(conn, wanderer["id"])
            wiped = load_wanderer(conn, wanderer["id"])
            self.assertEqual(forgotten, 0)
            self.assertEqual(wiped["knowledge_chars"], 0)
            self.assertFalse(evaluate_death(conn, wiped))
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
