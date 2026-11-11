import asyncio
import json
from datetime import datetime
from unittest.mock import patch

from classroom_app.services import wanderer_social_friends as friends
from classroom_app.services.wanderer_actions_common import ActionContext
from classroom_app.services.wanderer_behavior_service import daily_budget
from classroom_app.services.wanderer_life_core import (
    active_interaction_limit,
    interaction_gain,
    search_cultivation_gain,
    tier_perks,
    wanderer_search_limit,
)
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase, ZeroSleep


class RealUserRulesTest(WandererWorldTestCase):
    NOW = datetime(2026, 11, 1, 12, 0, 0)

    def _ctx(self, slug="atai", caller=None, tier=1):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, slug)
        wanderer = {**wanderer, "tier": tier,
                    "schedule": {**(wanderer.get("schedule") or {}), "active_hours": [[0, 24]]}}
        return ActionContext(conn_factory=self.connect, wanderer=wanderer,
                             ai_caller=caller or StubCaller(), sleep=ZeroSleep(), now=self.NOW)

    def test_real_user_search_only_returns_same_or_lower_tier_and_public_fields(self):
        ctx = self._ctx(tier=1)
        target = self.student_pk
        with patch("classroom_app.services.social_scope_service.student_global_tier",
                   side_effect=lambda _conn, sid: 5 if int(sid) == target else 0):
            with self.connect() as conn:
                self.assertFalse(any(item["identity"] == f"student:{target}"
                                     for item in friends.real_user_candidates(conn, ctx)))
        with patch("classroom_app.services.social_scope_service.student_global_tier",
                   side_effect=lambda _conn, sid: 1 if int(sid) == target else 0):
            with self.connect() as conn:
                items = friends.real_user_candidates(conn, ctx)
        chosen = next(item for item in items if item["identity"] == f"student:{target}")
        self.assertEqual(chosen["tier"], 1)
        for key in ("gender", "tier_name", "cultivation", "courses", "radar"):
            self.assertIn(key, chosen)

    def test_search_action_sends_a_request_and_records_location(self):
        ctx = self._ctx(tier=1, caller=StubCaller(json.dumps({
            "action": "request", "target": f"student:{self.student_pk}",
            "note": "想认识你", "reason": "看起来聊得来"}, ensure_ascii=False)))
        with patch("classroom_app.services.social_scope_service.student_global_tier", return_value=1), \
                patch("classroom_app.services.wanderer_social_friends.social_push_service.push_events") as push:
            push.return_value = None
            result = asyncio.run(friends.maybe_search_friends(ctx))
        self.assertEqual(result[0]["kind"], "social_search")
        with self.connect() as conn:
            row = conn.execute("SELECT status, requester_identity FROM social_friendships WHERE "
                               "(identity_a = ? AND identity_b = ?) OR (identity_a = ? AND identity_b = ?)",
                               (ctx.identity, f"student:{self.student_pk}",
                                f"student:{self.student_pk}", ctx.identity)).fetchone()
            action = conn.execute("SELECT location FROM wanderer_action_log WHERE wanderer_id = ? "
                                  "ORDER BY id DESC LIMIT 1", (ctx.wanderer_id,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["requester_identity"], ctx.identity)
        self.assertIn("好友搜索", action["location"])

    def test_real_user_reply_is_richer_and_scales_with_tier_and_length(self):
        bare = interaction_gain("social_message")
        normal = interaction_gain("social_message", chars=400)
        low = interaction_gain("social_message", real_user=True, target_tier=0, chars=0)
        high = interaction_gain("social_message", real_user=True, target_tier=5, chars=400)
        self.assertEqual(bare, 0.3)
        self.assertGreater(low, bare)
        self.assertGreater(high, low)
        # 真人加成封顶 5×：与同等质量（同长度）的散修互动相比。
        self.assertLessEqual(high, normal * 5 + 0.001)

    def test_active_and_search_limits_grow_twenty_percent_per_tier(self):
        self.assertEqual(active_interaction_limit(0), 20)
        self.assertEqual(active_interaction_limit(1), 24)
        self.assertEqual(active_interaction_limit(5), 40)
        self.assertEqual(active_interaction_limit(1, 50), 50)
        self.assertEqual(wanderer_search_limit(0), 12)
        self.assertEqual(wanderer_search_limit(5), 24)
        self.assertEqual(tier_perks(1, 10)["daily_budget"], 24)
        self.assertEqual(tier_perks(1, 10)["web_search_limit"], 14)

    def test_real_user_reply_is_awarded_more_than_a_wanderer_reply(self):
        from classroom_app.services.social_identity_service import load_people
        from classroom_app.services.wanderer_interaction_service import award_reply_cultivation, counterpart_context
        from classroom_app.services.wanderer_episodic_memory_service import today_trace

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            peer = self.wanderer(conn, "momo")
            real_identity = f"student:{self.student_pk}"
            peer_identity = f"student:{peer['student_id']}"
            real = award_reply_cultivation(conn, wanderer, real_identity, kind="dm_reply", chars=400)
            peer_gain = award_reply_cultivation(conn, wanderer, peer_identity, kind="dm_reply", chars=400)
            self.assertTrue(real["real_user"])
            self.assertFalse(peer_gain["real_user"])
            self.assertGreater(real["gained"], peer_gain["gained"])
            self.assertEqual(counterpart_context(conn, peer_identity)["real_user"], False)
            conn.execute("INSERT INTO wanderer_action_log (wanderer_id, action_kind, created_at, detail_json) "
                         "VALUES (?, 'dm_reply', ?, ?)",
                         (wanderer["id"], datetime.now().isoformat(timespec="seconds"),
                          json.dumps({"cultivation_gain": 3.5}, ensure_ascii=False)))
            conn.commit()
            trace = today_trace(conn, wanderer, now=datetime.now())
            people = load_people(conn, [peer_identity])
        self.assertIn("修为 +3.5", trace)
        self.assertIn("cultivation_score", people[peer_identity])

    def test_search_rewards_cultivation_by_tier_and_is_not_an_autonomous_action(self):
        from classroom_app.services.wanderer_actions_common import today_action_count
        from classroom_app.services.wanderer_life_service import award_search_cultivation
        from classroom_app.services.wanderer_identity_service import log_wanderer_action

        self.assertLess(search_cultivation_gain(0), search_cultivation_gain(5))
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            before = wanderer["cultivation_score"]
            gained = award_search_cultivation(conn, wanderer, 1)
            log_wanderer_action(conn, wanderer["id"], "web_search", target_type="web_search",
                                content_preview="搜索：气候", location="网络搜索 · 气候")
            conn.commit()
            after = self.wanderer(conn, "atai")["cultivation_score"]
            count = today_action_count(conn, wanderer["id"], datetime.now())
        self.assertEqual(gained, search_cultivation_gain(wanderer["tier"]))
        self.assertAlmostEqual(after, before + gained, places=2)
        self.assertEqual(count, 0)

        from classroom_app.services.wanderer_actions_common import _record_search_actions
        ctx = self._ctx(tier=wanderer["tier"])
        ctx.wanderer = {**wanderer, "cultivation_score": after}
        _record_search_actions(ctx, {"search_log": [{"query": "最新气候", "status": "succeeded"}]})
        with self.connect() as conn:
            rows = conn.execute("SELECT action_kind, detail_json FROM wanderer_action_log "
                                "WHERE wanderer_id = ? AND action_kind = 'web_search'", (wanderer["id"],)).fetchall()
            count = today_action_count(conn, wanderer["id"], datetime.now())
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn("cultivation_gain", rows[-1]["detail_json"])
        self.assertEqual(count, 0)

    def test_wanderer_search_quota_uses_the_tier_allowance(self):
        from classroom_app.services.ai_web_search_service import search_context_block, snapshot_for_wanderer

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            conn.execute('UPDATE wanderer_agents SET tier=5 WHERE id=?',(wanderer['id'],))
            quota = snapshot_for_wanderer(conn, wanderer)
        self.assertEqual(quota["limit"], 24)
        self.assertEqual(quota["available"], True)
        text = search_context_block({"limit": 24, "used": 2, "remaining": 22, "history": [
            {"date": "2026-10-31", "total": 3}, {"date": "2026-11-01", "total": 2}]})
        self.assertIn("最近七天使用", text)
        self.assertIn("11-01 用 2 次", text)

    def test_dao_interaction_penalty_reduces_the_daily_budget(self):
        from classroom_app.services.heavenly_dao_service import _apply_interaction_limit_row, parse_discipline

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            parsed = parse_discipline({"interaction_limits": [{
                "wanderer_id": wanderer["id"], "amount": 99, "days": 99, "reason": "连续伤害真实用户"}]},
                {wanderer["id"]})
            entry = parsed["interaction_limits"][0]
            self.assertEqual(entry["amount"], 20)
            self.assertEqual(entry["days"], 14)
            _apply_interaction_limit_row(conn, wanderer, entry, None)
            conn.commit()
            fresh = self.wanderer(conn, "atai")
        self.assertEqual(fresh["interaction_limit_penalty"], 1_000_000)
        self.assertGreaterEqual(fresh["interaction_penalty_until"], datetime.now().isoformat(timespec="seconds"))
        self.assertEqual(daily_budget(fresh), 0)
