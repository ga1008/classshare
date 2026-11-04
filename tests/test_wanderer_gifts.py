import asyncio
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock

from classroom_app import database
from classroom_app.services import heavenly_dao_service
from classroom_app.services.heavenly_dao_service import _apply_gift, run_dao_review_once
from classroom_app.services.social_common import pair
from classroom_app.services.wanderer_identity_service import current_life, load_wanderer
from classroom_app.services.wanderer_life_core import lifespan_weeks_for, max_gift_weeks
from tests.wanderer_behavior_support import StubCaller, WandererWorldTestCase

NEUTRAL = '{"insight":1.0,"memory":1.0,"vitality":1.0,"eloquence":1.0,"charm":1.0,"luck":1.0}'
EMPTY = '{"warnings": [], "demotions": [], "penalties": [], "talent_changes": []}'
APPROVED = json.dumps({"verdict": "approved", "reason": "他快撑不住了，我愿意分一点。"}, ensure_ascii=False)
REJECTED = json.dumps({"verdict": "rejected", "reason": "理由不足，且赠后自己只剩不到一周。"}, ensure_ascii=False)


def _ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


class WandererGiftTest(WandererWorldTestCase):
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

    def make_friends(self, first, second):
        a, b = pair(f"student:{first['student_id']}", f"student:{second['student_id']}")
        now = _ago(0)
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO social_friendships (identity_a, identity_b, status, requester_identity, "
                "created_at, responded_at) VALUES (?, ?, 'accepted', ?, ?, ?)",
                (a, b, a, now, now),
            )
            conn.commit()

    def request_gift(self, giver, recipient, weeks=1.5, reason="他寿元快尽了"):
        now = _ago(0)
        with self.connect() as conn:
            conn.execute(
                "UPDATE wanderer_agents SET gift_requested = 1, gift_target_identity = ?, gift_weeks = ?, "
                "gift_reason = ?, gift_requested_at = ?, updated_at = ? WHERE id = ?",
                (f"student:{recipient['student_id']}", weeks, reason, now, now, int(giver["id"])),
            )
            conn.commit()

    def pair_of_wanderers(self, giver_tier=3, giver_weeks=None, recipient_tier=1):
        giver = self.set_wanderer(
            "atai", tier=giver_tier, born_at=_ago(0),
            lifespan_weeks=giver_weeks or lifespan_weeks_for(giver_tier, 1.0),
            life_penalty_weeks=0, life_gift_out_weeks=0, life_gift_in_weeks=0, gift_requested=0,
            talents_json=NEUTRAL,
        )
        recipient = self.set_wanderer(
            "lele", tier=recipient_tier, born_at=_ago(0),
            lifespan_weeks=lifespan_weeks_for(recipient_tier, 1.0),
            life_penalty_weeks=0, life_gift_out_weeks=0, life_gift_in_weeks=0, gift_requested=0,
            talents_json=NEUTRAL,
        )
        return giver, recipient

    def test_gift_action_only_opens_a_pending_request(self):
        from classroom_app.services.wanderer_actions_common import ActionContext
        from classroom_app.services.wanderer_actions_knowledge import offer_lifespan
        from tests.wanderer_behavior_support import ZeroSleep

        giver, recipient = self.pair_of_wanderers()
        self.make_friends(giver, recipient)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO wanderer_relationships (wanderer_id, peer_identity, affinity, interactions) "
                "VALUES (?, ?, ?, 3)",
                (int(giver["id"]), f"student:{recipient['student_id']}", 60.0),
            )
            conn.commit()
            fresh = load_wanderer(conn, giver["id"])
        gift = json.dumps({"action": "reply", "parts": ["分你一点。"], "gift": {
            "target": f"student:{recipient['student_id']}", "weeks": 99, "reason": "他快撑不住了",
        }}, ensure_ascii=False)
        ctx = ActionContext(conn_factory=self.connect, wanderer=fresh, ai_caller=StubCaller(gift), sleep=ZeroSleep())
        result = asyncio.run(offer_lifespan(ctx))
        agent = self.agent(giver["id"])
        self.assertEqual(result[0]["kind"], "life_gift")
        self.assertEqual(agent["gift_requested"], 1)
        self.assertEqual(agent["gift_target_identity"], f"student:{recipient['student_id']}")
        with self.connect() as conn:
            room = max_gift_weeks(current_life(load_wanderer(conn, giver["id"]))["remaining_weeks"])
        self.assertLessEqual(float(agent["gift_weeks"]), room)
        self.assertAlmostEqual(float(agent["life_gift_out_weeks"] or 0), 0.0, places=1)

    def test_approved_gift_moves_life_and_is_verified(self):
        giver, recipient = self.pair_of_wanderers()
        self.make_friends(giver, recipient)
        self.request_gift(giver, recipient, weeks=1.5)
        with self.connect() as conn:
            before_giver = current_life(load_wanderer(conn, giver["id"]))
            before_recipient = current_life(load_wanderer(conn, recipient["id"]))
        summary = self.review(StubCaller(EMPTY, APPROVED), giver["id"])
        self.assertEqual(summary["counts"]["gift_approved"], 1)
        agent = self.agent(giver["id"])
        self.assertEqual(agent["gift_requested"], 0)
        self.assertAlmostEqual(agent["life_gift_out_weeks"], 1.5, places=1)
        self.assertAlmostEqual(self.agent(recipient["id"])["life_gift_in_weeks"], 1.5, places=1)
        with self.connect() as conn:
            after_giver = current_life(load_wanderer(conn, giver["id"]))
            after_recipient = current_life(load_wanderer(conn, recipient["id"]))
        self.assertAlmostEqual(
            before_giver["remaining_days"] - after_giver["remaining_days"], 10.5, delta=1.5
        )
        self.assertAlmostEqual(
            after_recipient["remaining_days"] - before_recipient["remaining_days"], 10.5, delta=1.5
        )
        given = self.reviews(giver["id"], "lifespan_gift")
        self.assertEqual([row["verdict"] for row in given], ["gift_approved"])
        self.assertTrue(json.loads(given[0]["evidence_json"])["verified"])
        received = self.reviews(recipient["id"], "lifespan_gift")
        self.assertEqual([row["verdict"] for row in received], ["gift_received"])

    def test_rejected_gift_does_not_move_life_and_allows_a_new_request(self):
        giver, recipient = self.pair_of_wanderers()
        self.make_friends(giver, recipient)
        self.request_gift(giver, recipient, weeks=1.0)
        summary = self.review(StubCaller(EMPTY, REJECTED), giver["id"])
        agent = self.agent(giver["id"])
        self.assertEqual(summary["counts"]["gift_rejected"], 1)
        self.assertEqual(agent["gift_requested"], 0)
        self.assertAlmostEqual(agent["life_gift_out_weeks"], 0.0, places=1)
        self.assertAlmostEqual(self.agent(recipient["id"])["life_gift_in_weeks"], 0.0, places=1)
        self.assertEqual(self.reviews(giver["id"], "lifespan_gift")[0]["verdict"], "gift_rejected")

    def test_non_friend_is_rejected_without_an_ai_verdict(self):
        giver, recipient = self.pair_of_wanderers()
        self.request_gift(giver, recipient, weeks=1.0)
        caller = StubCaller(EMPTY, APPROVED)
        summary = self.review(caller, giver["id"])
        self.assertEqual(summary["counts"]["gift_rejected"], 1)
        self.assertEqual(len(caller.calls), 1)  # only the discipline judgement
        self.assertIn("好友", self.reviews(giver["id"], "lifespan_gift")[0]["reason"])
        self.assertEqual(self.agent(giver["id"])["gift_requested"], 0)

    def test_low_tier_and_dead_recipient_are_rejected(self):
        giver, recipient = self.pair_of_wanderers(giver_tier=2)
        self.make_friends(giver, recipient)
        self.request_gift(giver, recipient, weeks=1.0)
        summary = self.review(StubCaller(EMPTY, APPROVED), giver["id"])
        self.assertEqual(summary["counts"]["gift_rejected"], 1)
        self.assertIn("三阶", self.reviews(giver["id"], "lifespan_gift")[0]["reason"])

        giver, recipient = self.pair_of_wanderers()
        self.make_friends(giver, recipient)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET status = 'dead' WHERE id = ?", (int(recipient["id"]),))
            conn.commit()
        self.request_gift(giver, recipient, weeks=1.0)
        summary = self.review(StubCaller(EMPTY, APPROVED), giver["id"])
        self.assertEqual(summary["counts"]["gift_rejected"], 1)
        self.assertIn("不在世", self.reviews(giver["id"], "lifespan_gift")[-1]["reason"])

    def test_request_is_clamped_to_the_fourth_of_remaining_life(self):
        giver, recipient = self.pair_of_wanderers(giver_weeks=8.0)
        self.make_friends(giver, recipient)
        self.request_gift(giver, recipient, weeks=40)
        with self.connect() as conn:
            room = max_gift_weeks(current_life(load_wanderer(conn, giver["id"]))["remaining_weeks"])
        summary = self.review(StubCaller(EMPTY, APPROVED), giver["id"])
        self.assertEqual(summary["counts"]["gift_approved"], 1)
        self.assertAlmostEqual(self.agent(giver["id"])["life_gift_out_weeks"], room, places=1)
        self.assertAlmostEqual(self.agent(recipient["id"])["life_gift_in_weeks"], room, places=1)

    def test_a_second_review_cannot_transfer_the_same_gift_twice(self):
        giver, recipient = self.pair_of_wanderers()
        self.make_friends(giver, recipient)
        self.request_gift(giver, recipient, weeks=1.0)
        with self.connect() as conn:
            stale_giver = load_wanderer(conn, giver["id"])
            recipient_row = load_wanderer(conn, recipient["id"])
            self.assertEqual(_apply_gift(conn, stale_giver, recipient_row, "approved", "他需要", {}), "approved")
            conn.commit()
            recipient_row = load_wanderer(conn, recipient["id"])
            with self.assertRaises(RuntimeError):
                _apply_gift(conn, stale_giver, recipient_row, "approved", "他需要", {})
            conn.rollback()
        self.assertAlmostEqual(self.agent(giver["id"])["life_gift_out_weeks"], 1.0, places=1)
        self.assertAlmostEqual(self.agent(recipient["id"])["life_gift_in_weeks"], 1.0, places=1)

    def test_failed_verification_rolls_the_transfer_back(self):
        giver, recipient = self.pair_of_wanderers()
        self.make_friends(giver, recipient)
        self.request_gift(giver, recipient, weeks=1.0)
        with self.connect() as conn:
            fresh_giver = load_wanderer(conn, giver["id"])
            fresh_recipient = load_wanderer(conn, recipient["id"])
            stale = [fresh_giver, fresh_recipient]
            with mock.patch.object(heavenly_dao_service, "load_wanderer", side_effect=stale):
                with self.assertRaises(RuntimeError):
                    _apply_gift(conn, fresh_giver, fresh_recipient, "approved", "他需要", {})
            conn.rollback()
            after_giver = load_wanderer(conn, giver["id"])
            after_recipient = load_wanderer(conn, recipient["id"])
        self.assertAlmostEqual(float(after_giver["life_gift_out_weeks"] or 0), 0.0, places=1)
        self.assertAlmostEqual(float(after_recipient["life_gift_in_weeks"] or 0), 0.0, places=1)
        self.assertEqual(int(after_giver["gift_requested"] or 0), 1)
