import asyncio
import json
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from classroom_app import database
from classroom_app.services import wanderer_behavior_service
from classroom_app.services.heavenly_dao_service import UNPARSABLE_REASON, run_dao_review_once
from classroom_app.services.wanderer_knowledge_service import forget_all
from classroom_app.services.wanderer_settings_service import SETTING_LAST_DAO_REVIEW_AT, wanderer_runtime_setting_get
from tests.wanderer_behavior_support import ONLY_BLOG, StubCaller, WandererWorldTestCase, turn

NO_DISCIPLINE = '{"warnings": [], "demotions": []}'


async def _noop(_ctx):
    return []


def _approved(reason="知识扎实，互动有质量", advice="继续研读"):
    return json.dumps({"verdict": "approved", "reason": reason, "advice": advice}, ensure_ascii=False)


def _rejected(reason="知识不足当前境界七成", advice="多读学习文档"):
    return json.dumps({"verdict": "rejected", "reason": reason, "advice": advice}, ensure_ascii=False)


def _warn(wanderer_id, reason="课堂进行中刷屏"):
    return json.dumps({"warnings": [{"wanderer_id": wanderer_id, "reason": reason}], "demotions": []},
                      ensure_ascii=False)


class HeavenlyDaoTest(WandererWorldTestCase):
    def review(self, caller, wanderer_ids, force=True):
        return asyncio.run(run_dao_review_once(database.get_db_connection, wanderer_ids=wanderer_ids,
                                               ai_caller=caller, force=force))

    def agent(self, wanderer_id):
        return self.rows("SELECT * FROM wanderer_agents WHERE id = ?", (wanderer_id,))[0]

    def reviews(self, wanderer_id=None, kind=None):
        sql, params = "SELECT * FROM wanderer_dao_reviews WHERE 1 = 1", []
        if wanderer_id is not None:
            sql += " AND wanderer_id = ?"
            params.append(wanderer_id)
        if kind is not None:
            sql += " AND review_kind = ?"
            params.append(kind)
        return self.rows(sql + " ORDER BY id", tuple(params))

    def dao_notes(self, wanderer):
        return self.rows("SELECT * FROM message_center_notifications WHERE recipient_identity = ? AND category = ?",
                         (f"student:{wanderer['student_id']}", "wanderer_dao"))

    def test_promotion_approved_raises_tier_and_notifies(self):
        atai = self.set_wanderer("atai", tier=1, promotion_requested=1, promotion_note="我读完了三份文档")
        caller = StubCaller(NO_DISCIPLINE, _approved())
        summary = self.review(caller, [atai["id"]])
        self.assertEqual(summary["counts"]["approved"], 1)
        agent = self.agent(atai["id"])
        self.assertEqual((agent["tier"], agent["promotion_requested"]), (2, 0))
        promotion = self.reviews(atai["id"], "promotion")
        self.assertEqual((promotion[0]["verdict"], promotion[0]["from_tier"], promotion[0]["to_tier"]),
                         ("approved", 1, 2))
        self.assertEqual(agent["last_review_id"], promotion[0]["id"])
        self.assertIn("我读完了三份文档", promotion[0]["evidence_json"])
        notes = self.dao_notes(atai)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["email_status"], "not_required")
        self.assertEqual(caller.labels, ["dao:discipline", "dao:promotion"])
        self.assertEqual(caller.calls[1][2]["student_id"], atai["student_id"])
        self.assertEqual(caller.calls[0][1]["task_type"], "deep_text_reasoning")
        audit = self.reviews(kind="daily_audit")
        self.assertEqual(len(audit), 1)
        self.assertIsNone(audit[0]["wanderer_id"])
        self.assertEqual(json.loads(audit[0]["evidence_json"])["counts"]["approved"], 1)
        with self.connect() as conn:
            self.assertTrue(wanderer_runtime_setting_get(conn, SETTING_LAST_DAO_REVIEW_AT))

    def test_promotion_rejected_keeps_tier_with_reason_and_advice(self):
        atai = self.set_wanderer("atai", tier=1, promotion_requested=1, promotion_note="想升阶")
        self.review(StubCaller(NO_DISCIPLINE, _rejected()), [atai["id"]])
        agent = self.agent(atai["id"])
        self.assertEqual((agent["tier"], agent["promotion_requested"]), (1, 0))
        promotion = self.reviews(atai["id"], "promotion")[0]
        self.assertEqual((promotion["verdict"], promotion["reason"], promotion["advice"]),
                         ("rejected", "知识不足当前境界七成", "多读学习文档"))

    def age_reviews(self, wanderer_id, hours=21):
        """Move existing reviews back in time (one review per day in reality)."""
        with self.connect() as conn:
            rows = conn.execute("SELECT id, created_at FROM wanderer_dao_reviews WHERE wanderer_id = ?",
                                (wanderer_id,)).fetchall()
            for row in rows:
                older = (datetime.fromisoformat(row["created_at"]) - timedelta(hours=hours)).isoformat(timespec="seconds")
                conn.execute("UPDATE wanderer_dao_reviews SET created_at = ? WHERE id = ?", (older, row["id"]))
            conn.commit()

    def test_warnings_accumulate_to_a_demotion(self):
        lele = self.set_wanderer("lele", tier=3)
        for expected_count in (1, 2):
            self.review(StubCaller(_warn(lele["id"])), [lele["id"]])
            self.assertEqual(self.agent(lele["id"])["warnings_count"], expected_count)
            self.assertEqual(self.agent(lele["id"])["tier"], 3)
            self.age_reviews(lele["id"])
        summary = self.review(StubCaller(_warn(lele["id"])), [lele["id"]])
        agent = self.agent(lele["id"])
        self.assertEqual((agent["tier"], agent["warnings_count"]), (2, 0))
        self.assertEqual((summary["counts"]["warnings"], summary["counts"]["demotions"]), (1, 1))
        verdicts = [row["verdict"] for row in self.reviews(lele["id"], "discipline")]
        self.assertEqual(verdicts, ["warning", "warning", "warning", "demoted"])
        self.assertTrue(agent["last_warning_at"])
        self.assertEqual(len(self.dao_notes(lele)), 4)
        # A fresh warning after the demotion starts counting again.
        self.age_reviews(lele["id"])
        self.review(StubCaller(_warn(lele["id"])), [lele["id"]])
        self.assertEqual(self.agent(lele["id"])["tier"], 2)

    def test_repeat_warning_within_20_hours_is_noted_not_counted(self):
        lele = self.set_wanderer("lele", tier=3)
        self.review(StubCaller(_warn(lele["id"])), [lele["id"]])
        notes_before = len(self.dao_notes(lele))
        summary = self.review(StubCaller(_warn(lele["id"])), [lele["id"]])
        agent = self.agent(lele["id"])
        self.assertEqual((agent["warnings_count"], agent["tier"]), (1, 3))
        self.assertEqual((summary["counts"]["warnings"], summary["counts"]["duplicate_warnings"]), (0, 1))
        rows = self.reviews(lele["id"], "discipline")
        self.assertEqual([row["verdict"] for row in rows], ["warning", "noted"])
        self.assertEqual(rows[1]["reason"], "已在 20 小时内警告过，本次不重复计数")
        self.assertTrue(rows[1]["acknowledged_at"])  # never reflected on
        self.assertEqual(len(self.dao_notes(lele)), notes_before)  # the wanderer is not notified again
        # Even a burst of identical warnings never reaches the 3-warning demotion.
        self.review(StubCaller(_warn(lele["id"])), [lele["id"]])
        self.assertEqual(self.agent(lele["id"])["tier"], 3)

    def test_explicit_demotion_and_unknown_ids_are_ignored(self):
        han = self.set_wanderer("hanbing", tier=0)
        caller = StubCaller(json.dumps({"warnings": [{"wanderer_id": 99999, "reason": "不存在"}],
                                        "demotions": [{"wanderer_id": han["id"], "reason": "辱骂同学"}]},
                                       ensure_ascii=False))
        summary = self.review(caller, [han["id"]])
        self.assertEqual((summary["counts"]["warnings"], summary["counts"]["demotions"]), (0, 1))
        self.assertEqual(self.agent(han["id"])["tier"], 0)  # floor at 0
        demoted = self.reviews(han["id"], "discipline")[0]
        self.assertEqual((demoted["verdict"], demoted["reason"]), ("demoted", "辱骂同学"))

    def test_unparsable_output_is_noted_and_changes_nothing(self):
        atai = self.set_wanderer("atai", tier=2, promotion_requested=1)
        summary = self.review(StubCaller("天道沉默，不出 JSON"), [atai["id"]])
        self.assertEqual(summary["outcome"], "unparsable")
        audit = self.reviews(kind="daily_audit")
        self.assertEqual([(row["verdict"], row["reason"]) for row in audit], [("noted", UNPARSABLE_REASON)])
        agent = self.agent(atai["id"])
        self.assertEqual((agent["tier"], agent["promotion_requested"], agent["warnings_count"]), (2, 1, 0))
        self.assertEqual(self.reviews(atai["id"]), [])

    def test_death_when_knowledge_and_cultivation_are_gone(self):
        xiaoman = self.set_wanderer("xiaoman", tier=0)
        with self.connect() as conn:
            forget_all(conn, xiaoman["id"])
            conn.commit()
        summary = self.review(StubCaller(NO_DISCIPLINE), [xiaoman["id"]])
        self.assertEqual(summary["counts"]["died"], 1)
        self.assertEqual(self.agent(xiaoman["id"])["status"], "dead")
        death = self.reviews(xiaoman["id"], "death")
        self.assertEqual(death[0]["verdict"], "died")
        self.assertTrue(self.dao_notes(xiaoman))
        # The dead are never reviewed again.
        caller = StubCaller()
        self.assertEqual(self.review(caller, [xiaoman["id"]])["counts"]["reviewed"], 0)
        self.assertEqual(caller.calls, [])

    def test_disabled_review_is_a_noop_unless_forced(self):
        caller = StubCaller(NO_DISCIPLINE)
        with patch.dict("os.environ", {"WANDERER_ENABLED": "false"}):
            self.assertEqual(self.review(caller, None, force=False)["status"], "disabled")
            self.assertEqual(caller.calls, [])
            self.assertEqual(self.review(caller, None, force=True)["status"], "ok")
        self.assertEqual(len(caller.calls), 1)  # one discipline call covers every wanderer
        audit = json.loads(self.reviews(kind="daily_audit")[0]["evidence_json"])
        self.assertEqual(audit["counts"]["reviewed"], 20)

    def test_wanderer_reflects_on_the_verdict_in_its_next_tick(self):
        atai = self.set_wanderer("atai", tier=1, promotion_requested=1, behaviors_json=ONLY_BLOG, activity_level=1)
        self.review(StubCaller(NO_DISCIPLINE, _rejected()), [atai["id"]])
        self.assertTrue(all(note["read_at"] is None for note in self.dao_notes(atai)))
        caller = StubCaller(turn("reply", ["天道说我还不够，那就多读两页。"]))
        with patch.dict(wanderer_behavior_service.ACTIONS, {"blog": _noop}):
            self.tick(caller, [atai["id"]])
        self.assertEqual(caller.labels[0], "wanderer:atai:reflect")
        review = self.reviews(atai["id"], "promotion")[0]
        self.assertTrue(review["acknowledged_at"])
        reflections = self.rows("SELECT * FROM wanderer_knowledge_entries WHERE wanderer_id = ? AND source_kind = ?",
                                (atai["id"], "reflection"))
        self.assertEqual([row["source_ref"] for row in reflections], [f"review:{review['id']}"])
        thread = self.rows("SELECT history_json FROM wanderer_memory_threads WHERE wanderer_id = ? "
                           "AND thread_key = 'dao'", (atai["id"],))
        self.assertIn("多读两页", thread[0]["history_json"])
        self.assertIn("reflect", [row["action_kind"] for row in self.actions(atai["id"])])
        notes = self.dao_notes(atai)
        self.assertTrue(notes)
        self.assertTrue(all(note["read_at"] for note in notes))  # inbox row read with the reflection


if __name__ == "__main__":
    unittest.main()
