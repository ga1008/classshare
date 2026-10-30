import asyncio
import json
import unittest

from classroom_app import database
from classroom_app.services.heavenly_dao_genesis import persist_genesis_seed, run_genesis
from classroom_app.services.wanderer_identity_service import death_reason, load_wanderer_by_slug, mark_dead
from classroom_app.services.wanderer_legacy_service import (
    fallback_seed,
    legacy_payload,
    load_legacy,
    pending_legacies,
    record_legacy,
)
from classroom_app.services.wanderer_life_core import lifespan_weeks_for
from classroom_app.services.wanderer_life_service import can_resurrect, resurrect
from classroom_app.services.wanderer_relationship_service import record_feeling
from tests.wanderer_behavior_support import StubCaller
from tests.wanderer_test_support import WandererDbTestCase

GENESIS_SEED = {
    "slug": "xinyu", "display_name": "许新雨", "archetype": "雨天写诗的理科生", "gender": "女",
    "hometown": "四川成都",
    "childhood": "小时候常在下雨天趴在窗边数水珠，把每一滴都想象成一个短句。",
    "schooling": "高中物理竞赛出身，后来发现写诗和推导公式都需要耐心。",
    "interests": ["雨", "诗歌", "物理"],
    "speech_style": {"voice_profile": "reflective", "catchphrases": [], "punctuation": "句号偏多",
                     "emoji": "很少", "length": "中等"},
    "temperament": ["安静", "敏感", "爱观察"],
    "interaction_habit": "下雨天才会主动说话",
    "interaction_frequency": {"activity_level": 3, "active_hours": [[12, 14], [21, 23]],
                              "weekday_weights": {}},
    "reply_policy": {"ignore_prob": 0.3, "emoji_only_prob": 0.1, "multi_part_prob": 0.2, "max_parts": 2},
    "taboos": ["不嘲笑别人的敏感"],
    "initial_tier": 0, "daily_action_budget": 10,
    "public_intro": "把公式写成诗的人。",
    "initial_knowledge": [
        {"title": "雨的形成", "digest": "水汽遇冷凝结成云滴，再合并增大到空气托不住时落下。", "tags": ["雨"]},
        {"title": "自由落体", "digest": "忽略空气阻力时，落体加速度与质量无关，约为九点八米每二次方秒。", "tags": ["物理"]},
        {"title": "短句的节奏", "digest": "短句靠停顿制造节奏，句号越多，呼吸越慢。", "tags": ["诗歌"]},
    ],
}


def _kill(conn, slug):
    wanderer = load_wanderer_by_slug(conn, slug)
    mark_dead(conn, int(wanderer["id"]), "寿元耗尽（合成测试）")
    conn.commit()
    return wanderer


class LegacyRecordTest(WandererDbTestCase):
    def test_death_writes_a_pending_life_story(self):
        with self.connect() as conn:
            wanderer = load_wanderer_by_slug(conn, "xiaoman")
            record_feeling(conn, wanderer, "student:4242", delta=5, mood="亲近", reason="聊得来")
            _kill(conn, "xiaoman")
            legacy = pending_legacies(conn, 5)[0]
            payload = legacy_payload(legacy, detail=True)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["display_name"], "小满")
        self.assertIn("小满", payload["summary"])
        self.assertIn("寿元耗尽", payload["summary"])
        self.assertIn("final_tier", payload["trajectory"])
        self.assertIn("archetype", payload["lineage"])
        self.assertGreaterEqual(payload["weeks_lived"], 0)

    def test_record_legacy_is_idempotent(self):
        with self.connect() as conn:
            wanderer = load_wanderer_by_slug(conn, "xiaoman")
            first = record_legacy(conn, wanderer, "测试")
            second = record_legacy(conn, wanderer, "测试")
            conn.commit()
            rows = pending_legacies(conn, 5)
        self.assertEqual(first, second)
        self.assertEqual(len(rows), 1)


class GenesisReincarnationTest(WandererDbTestCase):
    def test_genesis_can_claim_a_legacy_seed(self):
        with self.connect() as conn:
            _kill(conn, "xiaoman")
            legacy = pending_legacies(conn, 5)[0]
        seed = {**GENESIS_SEED, "legacy_id": legacy["id"]}
        caller = StubCaller(json.dumps({"rationale": "让她带着雨声重来", "seeds": [seed]}, ensure_ascii=False))
        summary = asyncio.run(run_genesis(database.get_db_connection, manual=True, ai_caller=caller))
        self.assertEqual(summary["created"], 1, summary)
        self.assertEqual(summary["reincarnated"], 1)
        with self.connect() as conn:
            born = load_wanderer_by_slug(conn, "xinyu")
            claimed = load_legacy(conn, legacy["id"])
            entry = conn.execute(
                "SELECT source_kind, title FROM wanderer_knowledge_entries WHERE wanderer_id = ? "
                "AND source_kind = 'legacy'", (born["id"],),
            ).fetchone()
            review = conn.execute(
                "SELECT evidence_json FROM wanderer_dao_reviews WHERE wanderer_id = ? AND review_kind = 'genesis' "
                "ORDER BY id DESC LIMIT 1", (born["id"],),
            ).fetchone()
        self.assertEqual(born["origin"], "reincarnated")
        self.assertEqual(born["legacy_id"], legacy["id"])
        self.assertEqual(claimed["status"], "claimed")
        self.assertIsNotNone(entry)
        self.assertIn("legacy_of", json.loads(review["evidence_json"]))


class FallbackReincarnationTest(WandererDbTestCase):
    def test_model_failure_still_reincarnates(self):
        with self.connect() as conn:
            _kill(conn, "xiaoman")
            legacy = pending_legacies(conn, 5)[0]
        caller = StubCaller(error=RuntimeError("synthetic outage"))
        summary = asyncio.run(run_genesis(database.get_db_connection, manual=True, ai_caller=caller))
        self.assertEqual(summary["created"], 1, summary)
        self.assertEqual(summary["reincarnated"], 1)
        with self.connect() as conn:
            reincarnated = conn.execute(
                "SELECT slug, display_name, origin, legacy_id FROM wanderer_agents WHERE origin = 'reincarnated'"
            ).fetchone()
            claimed = load_legacy(conn, legacy["id"])
        self.assertIsNotNone(reincarnated)
        self.assertEqual(reincarnated["legacy_id"], legacy["id"])
        self.assertEqual(claimed["status"], "claimed")
        self.assertTrue(reincarnated["display_name"].endswith("二世"))

    def test_fallback_seed_is_a_valid_persona(self):
        with self.connect() as conn:
            _kill(conn, "xiaoman")
            legacy = pending_legacies(conn, 5)[0]
        seed = fallback_seed(legacy)
        self.assertIsNotNone(seed)
        self.assertEqual(seed["origin"], "reincarnated")
        self.assertEqual(seed["legacy_id"], legacy["id"])
        self.assertEqual(seed["initial_tier"], 0)


class ResurrectionTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET last_resurrect_at = NULL")
            conn.commit()

    def test_reviving_a_dao_wanderer_never_leaves_zero_knowledge(self):
        with self.connect() as conn:
            persist_genesis_seed(conn, GENESIS_SEED, rationale="合成", activity={})
            conn.commit()
        with self.connect() as conn:
            target = load_wanderer_by_slug(conn, "xinyu")
            mark_dead(conn, int(target["id"]), "合成陨落")
            reviver = load_wanderer_by_slug(conn, "suqinghe")
            conn.execute("UPDATE wanderer_agents SET tier = 10, lifespan_weeks = ?, life_penalty_weeks = 0, "
                         "born_at = ? WHERE id = ?",
                         (lifespan_weeks_for(10, 1.0), target["created_at"], reviver["id"]))
            conn.commit()
            reviver = load_wanderer_by_slug(conn, "suqinghe")
            result = resurrect(conn, reviver, load_wanderer_by_slug(conn, "xinyu"), reason="救回来")
            conn.commit()
            revived = load_wanderer_by_slug(conn, "xinyu")
            active = conn.execute("SELECT COUNT(*) FROM wanderer_knowledge_entries WHERE wanderer_id = ? "
                                  "AND status = 'active'", (revived["id"],)).fetchone()[0]
            reason = death_reason(conn, revived)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(active, 1)
        self.assertEqual(reason, "")

    def test_tier_ten_wanderer_pays_weeks_to_revive_a_peer(self):
        with self.connect() as conn:
            target = load_wanderer_by_slug(conn, "xiaoman")
            _kill(conn, "xiaoman")
            reviver = load_wanderer_by_slug(conn, "suqinghe")
            conn.execute("UPDATE wanderer_agents SET tier = 10, lifespan_weeks = ?, life_penalty_weeks = 0, "
                         "born_at = ? WHERE id = ?",
                         (lifespan_weeks_for(10, 1.0), target["created_at"], reviver["id"]))
            conn.commit()
            reviver = load_wanderer_by_slug(conn, "suqinghe")
            target = load_wanderer_by_slug(conn, "xiaoman")
            check = can_resurrect(conn, reviver)
            self.assertTrue(check["ok"], check)
            result = resurrect(conn, reviver, target, reason="她不该就这样消失。")
            conn.commit()
            revived = load_wanderer_by_slug(conn, "xiaoman")
            after = load_wanderer_by_slug(conn, "suqinghe")
            legacy = conn.execute("SELECT status FROM wanderer_legacies WHERE wanderer_id = ?",
                                  (revived["id"],)).fetchone()
        self.assertTrue(result["ok"])
        self.assertEqual(revived["status"], "active")
        self.assertEqual(revived["tier"], 0)
        self.assertAlmostEqual(after["life_penalty_weeks"], 8.0, places=1)
        self.assertEqual(legacy["status"], "void")


if __name__ == "__main__":
    unittest.main()
