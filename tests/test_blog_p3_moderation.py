"""P3 of the blog closed loop: content safety and moderation
(docs/blog-closed-loop-plan-2026-10-12.md §4–§5, acceptance §10.3).

Real blog schema in an in-memory SQLite database. The AI is always a stub:
no network and no paid call (plan §9.3, P3 acceptance).
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from classroom_app.db.schema_blog_closed_loop import backfill_blog_closed_loop_data
from classroom_app.services import (
    blog_ai_service,
    blog_community_service,
    blog_effects_service,
    blog_moderation_service as moderation,
    blog_review_queue_service as review_queue,
    blog_safety_review as review,
    blog_safety_service as safety,
    blog_service,
)
from classroom_app.services.rate_limit_service import RateLimitExceededError
from tests.test_blog_p0_hardening import (
    AUTHOR, CLASSMATE, OTHER_TEACHER, OUTSIDER, TEACHER, BlogP0Fixture, _ReuseConnection,
)


def verdict(risk="none", categories=(), evidence="", note=""):
    return {"risk": risk, "categories": list(categories), "evidence": evidence, "author_note": note}


class ModerationFixture(BlogP0Fixture):
    def state(self, post_id):
        return self.row(post_id)["moderation_state"]

    def comment_row(self, comment_id):
        return dict(self.conn.execute("SELECT * FROM blog_comments WHERE id=?", (comment_id,)).fetchone())

    def comment(self, post_id, user=CLASSMATE, content="评论", parent=None):
        result = blog_service.add_comment(self.conn, user, post_id, content_md=content, parent_comment_id=parent)
        self.conn.commit()
        return int(result["id"])

    def run_review(self, target_type, target_id, revision=1, *, result=None, error=None, attempt=0):
        task = {"payload": {"target_type": target_type, "target_id": target_id, "revision": revision},
                "attempt_count": attempt, "max_attempts": safety.REVIEW_MAX_ATTEMPTS}

        async def fake_ask(*_args):
            if error:
                raise error
            return result, "stub-model"

        with patch("classroom_app.database.get_db_connection", return_value=_ReuseConnection(self.conn)), \
                patch.object(review, "_ask_ai", fake_ask):
            return asyncio.run(review.handle_blog_content_review(task))

    def notifications(self, role, pk):
        return [dict(row) for row in self.conn.execute(
            "SELECT category, title, body_preview FROM message_center_notifications WHERE recipient_role=? AND recipient_user_pk=?",
            (role, pk),
        ).fetchall()]

    def visible_titles(self, viewer):
        return {item["title"] for item in blog_service.list_posts(self.conn, viewer)["posts"]}


class VisibilityTests(ModerationFixture):
    def test_new_student_post_waits_for_review(self):
        post_id = self.post(reviewed=False, title="待审")
        self.assertEqual("pending", self.state(post_id))
        self.assertIn("待审", self.visible_titles(AUTHOR))
        self.assertNotIn("待审", self.visible_titles(CLASSMATE))
        self.assertFalse(blog_service._can_view_post(self.conn, CLASSMATE, self.row(post_id)))
        self.assertFalse(blog_service._can_view_post(self.conn, OTHER_TEACHER, self.row(post_id)))
        self.assertTrue(blog_service._can_view_post(self.conn, TEACHER, self.row(post_id)))

    def test_teacher_posts_skip_ai_and_publish_at_once(self):
        post_id = self.post(TEACHER, reviewed=False, title="教师帖")
        self.assertEqual("passed", self.state(post_id))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM blog_content_reviews").fetchone()[0])

    def test_flagged_is_visible_and_hidden_from_the_author_label(self):
        post_id = self.post(reviewed=False)
        self.conn.execute("UPDATE blog_posts SET moderation_state='flagged', moderated_by='system:ai' WHERE id=?", (post_id,))
        detail = blog_service.get_post_detail(self.conn, AUTHOR, post_id)
        self.assertEqual("passed", detail["moderation"]["state"])
        self.assertEqual("passed", detail["moderation_state"])
        self.assertTrue(blog_service._can_view_post(self.conn, CLASSMATE, self.row(post_id)))
        moderator_view = blog_service.get_post_detail(self.conn, TEACHER, post_id)
        self.assertEqual("flagged", moderator_view["moderation"]["state"])

    def test_global_search_never_lists_withheld_posts(self):
        from classroom_app.services.global_search_service import _search_blog

        self.post(reviewed=False, title="搜索待审帖", content="x")
        self.assertEqual([], _search_blog(self.conn, "%搜索待审%"))


class LocalRuleTests(ModerationFixture):
    def test_block_terms_are_refused_for_posts_and_comments(self):
        with self.assertRaises(safety.BlogSafetyBlocked) as caught:
            self.post(content="可以代写论文，价格优惠")
        self.assertIn("广告或引流", str(caught.exception))
        self.assertNotIn("代写论文", str(caught.exception))
        post_id = self.post()
        with self.assertRaises(safety.BlogSafetyBlocked):
            self.comment(post_id, content="需要的加我，办假证")

    def test_flagged_comment_is_held_for_review_and_seen_only_by_its_author(self):
        post_id = self.post()
        comment_id = self.comment(post_id, content="有事加微信 abc_12345 联系")
        self.assertEqual(("pending", "system:l1"), (self.comment_row(comment_id)["moderation_state"],
                                                     self.comment_row(comment_id)["moderated_by"]))
        others = blog_service.list_comments(self.conn, OUTSIDER, post_id)["comments"]
        self.assertEqual([], others)
        own = blog_service.list_comments(self.conn, CLASSMATE, post_id)["comments"]
        self.assertEqual("pending", own[0]["moderation"]["state"])

    def test_privacy_patterns_flag_phone_numbers(self):
        self.assertEqual(safety.LEVEL_FLAG, safety.check_text(self.conn, "电话 13812345678").level)
        self.assertEqual(safety.LEVEL_CLEAN, safety.check_text(self.conn, "版本号 3.14159265").level)

    def test_admin_terms_override_and_disable_builtins(self):
        self.conn.execute("INSERT INTO blog_safety_terms(term, level, category, enabled, updated_by, updated_at) VALUES('脑残', 'flag', 'abuse', 0, 't', '2026')")
        self.conn.execute("INSERT INTO blog_safety_terms(term, level, category, enabled, updated_by, updated_at) VALUES('黑话', 'block', 'spam', 1, 't', '2026')")
        self.assertEqual(safety.LEVEL_CLEAN, safety.check_text(self.conn, "脑残").level)
        self.assertEqual(safety.LEVEL_BLOCK, safety.check_text(self.conn, "这是黑 话").level)

    def test_rate_limits(self):
        for index in range(safety.POSTS_PER_WINDOW):
            self.post(title=f"帖{index}", content=f"正文{index}")
        with self.assertRaises(RateLimitExceededError):
            self.post(title="再发一篇", content="太快了")
        post_id = self.post(TEACHER, title="供评论", content="x")
        for index in range(safety.COMMENTS_PER_WINDOW):
            self.comment(post_id, OUTSIDER, content=f"评论{index}")
        with self.assertRaises(RateLimitExceededError):
            self.comment(post_id, OUTSIDER, content="第十一条")


class AiReviewTests(ModerationFixture):
    def test_publication_queues_one_review_and_a_timeout(self):
        post_id = self.post(reviewed=False)
        receipts = self.conn.execute("SELECT status, content_revision FROM blog_content_reviews WHERE target_id=?", (post_id,)).fetchall()
        self.assertEqual([("pending", 1)], [tuple(row) for row in receipts])
        kinds = sorted(row[0] for row in self.conn.execute("SELECT task_kind FROM scheduled_tasks").fetchall())
        self.assertEqual([safety.REVIEW_TASK_KIND, safety.REVIEW_TIMEOUT_TASK_KIND], kinds)

    def test_clean_verdict_publishes(self):
        post_id = self.post(reviewed=False, title="会通过")
        self.assertEqual("settled: passed", self.run_review("post", post_id, result=verdict("low")))
        self.assertIn("会通过", self.visible_titles(CLASSMATE))
        self.assertEqual("done", self.conn.execute("SELECT status FROM blog_content_reviews WHERE target_id=?", (post_id,)).fetchone()[0])

    def test_medium_risk_stays_visible_but_queued(self):
        post_id = self.post(reviewed=False)
        self.run_review("post", post_id, result=verdict("medium", ["spam"], "疑似引流"))
        self.assertEqual(("flagged", "system:ai"), (self.state(post_id), self.row(post_id)["moderated_by"]))
        self.assertEqual(1, review_queue.queue_counts(self.conn, TEACHER)["machine"])

    def test_high_risk_removes_notifies_and_logs(self):
        post_id = self.post(reviewed=False)
        self.run_review("post", post_id, result=verdict("high", ["abuse"], "人身攻击", "请文明交流"))
        row = self.row(post_id)
        self.assertEqual(("removed", "abuse", "请文明交流"), (row["moderation_state"], row["moderation_reason_code"], row["moderation_note"]))
        self.assertTrue(any("移除" in n["title"] for n in self.notifications("student", 21)))
        self.assertEqual("ai_remove", self.conn.execute("SELECT action FROM blog_moderation_logs ORDER BY id DESC").fetchone()[0])

    def test_a_late_verdict_for_an_old_revision_is_dropped(self):
        post_id = self.post(reviewed=False)
        revision = self.row(post_id)["content_revision"]
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="改过的正文", expected_revision=revision)
        self.assertEqual("stale: content changed or is gone", self.run_review("post", post_id, 1, result=verdict("high", ["abuse"])))
        self.assertEqual("pending", self.state(post_id))

    def test_the_machine_never_overrides_a_person(self):
        post_id = self.post(reviewed=False)
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        self.assertTrue(self.run_review("post", post_id, result=verdict("none")).startswith("kept"))
        self.assertEqual("limited", self.state(post_id))

    def test_failures_retry_then_release_as_flagged(self):
        post_id = self.post(reviewed=False)
        with self.assertRaises(RuntimeError):
            self.run_review("post", post_id, error=RuntimeError("down"), attempt=0)
        self.assertEqual("pending", self.state(post_id))
        result = self.run_review("post", post_id, error=RuntimeError("down"), attempt=safety.REVIEW_MAX_ATTEMPTS - 1)
        self.assertTrue(result.startswith("failed_final"))
        self.assertEqual("flagged", self.state(post_id))

    def test_timeout_releases_only_still_pending_posts(self):
        post_id = self.post(reviewed=False)
        task = {"payload": {"target_type": "post", "target_id": post_id, "revision": 1}}
        with patch("classroom_app.database.get_db_connection", return_value=_ReuseConnection(self.conn)):
            self.assertTrue(asyncio.run(review.handle_blog_content_review_timeout(task)).startswith("released"))
            self.assertEqual(("flagged", "system:timeout"), (self.state(post_id), self.row(post_id)["moderated_by"]))
            self.assertTrue(asyncio.run(review.handle_blog_content_review_timeout(task)).startswith("noop"))

    def test_daily_quota_releases_instead_of_waiting(self):
        with patch.dict("os.environ", {"BLOG_SAFETY_DAILY_LIMIT": "0"}):
            post_id = self.post(reviewed=False)
        self.assertEqual(("flagged", "system:quota"), (self.state(post_id), self.row(post_id)["moderated_by"]))

    def test_verdict_contract_is_strict(self):
        with self.assertRaises(review.ReviewSchemaError):
            review.parse_verdict({"response_text": "看起来没问题"})
        with self.assertRaises(review.ReviewSchemaError):
            review.parse_verdict({"response_json": {"risk": "maybe"}})
        parsed = review.parse_verdict({"response_text": 'x {"risk":"HIGH","categories":["abuse","bogus"],"evidence":"e"}'})
        self.assertEqual(("high", ["abuse"]), (parsed["risk"], parsed["categories"]))

    def test_editing_a_public_post_keeps_it_visible_while_rechecked(self):
        post_id = self.post(title="公开后修改")
        revision = self.row(post_id)["content_revision"]
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="补充一段", expected_revision=revision)
        self.assertEqual("none", self.state(post_id))
        self.assertIn("公开后修改", self.visible_titles(CLASSMATE))


class HumanModerationTests(ModerationFixture):
    def test_ordinary_teacher_can_only_emergency_limit(self):
        post_id = self.post()
        result = moderation.moderate(self.conn, OTHER_TEACHER, "post", post_id, action="limit", reason_code="abuse")
        self.assertTrue(result["emergency"])
        self.assertTrue(self.row(post_id)["moderated_by"].startswith("emergency:teacher:8"))
        self.assertEqual(1, review_queue.queue_counts(self.conn, TEACHER)["emergency"])
        with self.assertRaises(PermissionError):
            moderation.moderate(self.conn, OTHER_TEACHER, "post", post_id, action="restore", note="误判")
        with self.assertRaises(PermissionError):
            review_queue.queue_counts(self.conn, OTHER_TEACHER)

    def test_limit_requires_a_reason_and_restore_a_note(self):
        post_id = self.post()
        with self.assertRaises(moderation.ModerationError):
            moderation.moderate(self.conn, TEACHER, "post", post_id, action="limit")
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        with self.assertRaises(moderation.ModerationError):
            moderation.moderate(self.conn, TEACHER, "post", post_id, action="restore")
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="restore", note="核实无误")
        self.assertEqual("passed", self.state(post_id))

    def test_confirm_takes_an_item_off_the_desk(self):
        post_id = self.post(reviewed=False)
        self.run_review("post", post_id, result=verdict("medium", ["spam"]))
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="confirm")
        self.assertEqual(("flagged", "teacher:7"), (self.state(post_id), self.row(post_id)["moderated_by"]))
        self.assertEqual(0, review_queue.queue_counts(self.conn, TEACHER)["machine"])

    def test_handled_comment_keeps_its_thread_as_a_placeholder(self):
        post_id = self.post()
        parent = self.comment(post_id, CLASSMATE, content="原评论")
        child = self.comment(post_id, OUTSIDER, content="回复", parent=parent)
        moderation.moderate(self.conn, TEACHER, "comment", parent, action="remove", reason_code="abuse")
        outsider_view = blog_service.list_comments(self.conn, OUTSIDER, post_id)["comments"]
        self.assertTrue(outsider_view[0]["withheld"])
        self.assertEqual("", outsider_view[0]["content_md"])
        self.assertEqual(child, outsider_view[0]["replies"][0]["id"])
        author_view = blog_service.list_comments(self.conn, CLASSMATE, post_id)["comments"]
        self.assertEqual(("原评论", "removed"), (author_view[0]["content_md"], author_view[0]["moderation"]["state"]))


class ReportTests(ModerationFixture):
    def report(self, user, target_id, reason="abuse", target_type="post"):
        result = blog_community_service.create_report(
            self.conn, user, target_type=target_type, target_id=target_id, reason_code=reason,
        )
        self.conn.commit()
        return result

    def test_three_reporters_limit_until_a_moderator_decides(self):
        post_id = self.post(TEACHER, title="被举报")
        self.report(CLASSMATE, post_id)
        self.report(OUTSIDER, post_id, "privacy_identity")
        self.assertEqual("passed", self.state(post_id))
        self.assertTrue(self.report(AUTHOR, post_id, "spam")["auto_limited"])
        self.assertEqual(("limited", "system:reports"), (self.state(post_id), self.row(post_id)["moderated_by"]))
        queue = review_queue.list_queue(self.conn, TEACHER, "reports")
        self.assertTrue(queue["items"][0]["reports"]["identity_exposed"])
        moderation.resolve_reports(self.conn, TEACHER, "post", post_id, decision="dismiss", note="未发现违规")
        self.assertEqual("passed", self.state(post_id))
        self.assertTrue(any("已处理" in n["title"] for n in self.notifications("student", 22)))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM blog_reports WHERE status='pending'").fetchone()[0])

    def test_a_reporter_can_report_again_after_a_decision(self):
        post_id = self.post(TEACHER)
        self.report(CLASSMATE, post_id)
        moderation.resolve_reports(self.conn, TEACHER, "post", post_id, decision="dismiss")
        self.report(CLASSMATE, post_id, "spam")
        # Settling replaces the reporter's earlier settled row instead of violating UNIQUE.
        moderation.resolve_reports(self.conn, TEACHER, "post", post_id, decision="dismiss")


class AppealTests(ModerationFixture):
    def limited_post(self):
        post_id = self.post()
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="remove", reason_code="spam", note="广告")
        return post_id

    def test_one_appeal_per_sanction_and_accepting_restores(self):
        post_id = self.limited_post()
        appeal = moderation.create_appeal(self.conn, AUTHOR, post_id, reason="这是课程资料分享")
        self.assertEqual("appeal_pending", self.state(post_id))
        with self.assertRaises(moderation.ModerationError):
            moderation.create_appeal(self.conn, AUTHOR, post_id, reason="再申诉")
        with self.assertRaises(PermissionError):
            moderation.create_appeal(self.conn, CLASSMATE, post_id, reason="替他申诉")
        moderation.decide_appeal(self.conn, TEACHER, appeal["id"], decision="accept")
        self.assertEqual("passed", self.state(post_id))
        self.assertTrue(any("申诉已通过" in n["title"] for n in self.notifications("student", 21)))

    def test_rejecting_needs_a_reason_and_restores_the_sanction(self):
        post_id = self.limited_post()
        appeal = moderation.create_appeal(self.conn, AUTHOR, post_id, reason="理由")
        with self.assertRaises(moderation.ModerationError):
            moderation.decide_appeal(self.conn, TEACHER, appeal["id"], decision="reject")
        moderation.decide_appeal(self.conn, TEACHER, appeal["id"], decision="reject", note="确为广告")
        self.assertEqual("removed", self.state(post_id))
        status = moderation.appeal_status(self.conn, self.row(post_id))
        self.assertEqual((False, True), (status["can_appeal"], status["used"]))

    def test_editing_during_an_appeal_sends_the_post_back_to_review(self):
        post_id = self.limited_post()
        moderation.create_appeal(self.conn, AUTHOR, post_id, reason="理由")
        revision = self.row(post_id)["content_revision"]
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="删掉了广告", expected_revision=revision)
        self.assertEqual("pending", self.state(post_id))
        self.assertEqual("withdrawn", self.conn.execute("SELECT decision FROM blog_appeals").fetchone()[0])


class RevealAndAuditTests(ModerationFixture):
    def test_reveal_is_moderator_only_and_audited(self):
        post_id = self.post(mode="anonymous")
        with self.assertRaises(PermissionError):
            moderation.reveal_identity(self.conn, OTHER_TEACHER, "post", post_id, reason_kind="report")
        found = moderation.reveal_identity(self.conn, TEACHER, "post", post_id, reason_kind="report")
        self.assertEqual(("张三", "软件一班"), (found["name"], found["class_name"]))
        log = self.conn.execute("SELECT action, extra_json, target_persona_kind FROM blog_moderation_logs").fetchone()
        self.assertEqual(("identity_reveal", "anon"), (log[0], log[2]))
        self.assertEqual("report", json.loads(log[1])["reason_kind"])

    def test_review_desk_never_shows_who_is_behind_a_face(self):
        post_id = self.post(mode="anonymous", reviewed=False)
        self.run_review("post", post_id, result=verdict("medium", ["abuse"]))
        item = review_queue.list_queue(self.conn, TEACHER, "machine")["items"][0]
        self.assertNotIn("张三", json.dumps(item, ensure_ascii=False))
        self.assertTrue(item["can_reveal"])

    def test_audit_log_is_super_admin_only(self):
        with self.assertRaises(PermissionError):
            moderation.list_audit_logs(self.conn, TEACHER)
        self.conn.execute("UPDATE teachers SET is_super_admin=1 WHERE id=8")
        moderation.set_moderator(self.conn, OTHER_TEACHER, 7, granted=False)
        self.assertFalse(moderation.is_blog_moderator(self.conn, TEACHER))
        revoked = self.conn.execute("SELECT revoked_by FROM blog_moderators WHERE teacher_id=7").fetchone()
        self.assertEqual("teacher:8", revoked[0])
        moderation.set_moderator(self.conn, OTHER_TEACHER, 7, granted=True)
        self.assertTrue(moderation.is_blog_moderator(self.conn, TEACHER))
        self.assertEqual([], moderation.list_audit_logs(self.conn, OTHER_TEACHER)["items"])
        # An appeal by a pen-name/anonymous author must not name them in the audit list.
        post_id = self.post(mode="anonymous")
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        moderation.create_appeal(self.conn, AUTHOR, post_id, reason="理由")
        appeal_row = moderation.list_audit_logs(self.conn, OTHER_TEACHER, action="appeal")["items"][0]
        self.assertEqual("作者", appeal_row["actor_name"])


class HousekeeperTests(ModerationFixture):
    def test_reply_that_fails_local_rules_is_discarded(self):
        with patch.object(blog_ai_service, "get_db_connection", return_value=_ReuseConnection(self.conn)):
            with self.assertRaises(ValueError):
                blog_ai_service._screen_housekeeper_reply("联系我 13812345678")
            blog_ai_service._screen_housekeeper_reply("这是一条正常的回复")

    def test_daily_mention_quota(self):
        post_id = self.post(TEACHER)
        with patch.dict("os.environ", {"BLOG_HOUSEKEEPER_DAILY_LIMIT": "1"}):
            self.assertFalse(blog_effects_service.housekeeper_quota_exhausted(self.conn, "student:21"))
            self.conn.execute(
                "INSERT INTO blog_ai_reply_jobs(trigger_type, trigger_id, post_id, trigger_author_identity, created_at) "
                "VALUES('post', ?, ?, 'student:21', ?)", (post_id, post_id, blog_service._now_iso()),
            )
            self.assertTrue(blog_effects_service.housekeeper_quota_exhausted(self.conn, "student:21"))


class ConvergenceTests(ModerationFixture):
    def test_legacy_hidden_posts_converge_to_published_and_limited(self):
        post_id = self.post()
        self.conn.execute("UPDATE blog_posts SET status='moderated', moderation_state='none' WHERE id=?", (post_id,))
        backfill_blog_closed_loop_data(self.conn, apply=True)
        row = self.row(post_id)
        self.assertEqual(("published", "limited"), (row["status"], row["moderation_state"]))
        self.assertFalse(blog_service._can_view_post(self.conn, CLASSMATE, row))


class SecurityReviewRegressionTests(ModerationFixture):
    """Findings of the P3 security review; each one let a sanction be bypassed or erased."""

    def test_emergency_limit_cannot_touch_content_already_handled(self):
        post_id = self.post()
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="remove", reason_code="spam")
        appeal = moderation.create_appeal(self.conn, AUTHOR, post_id, reason="理由")
        with self.assertRaises(PermissionError):
            moderation.moderate(self.conn, OTHER_TEACHER, "post", post_id, action="limit", reason_code="spam")
        self.assertEqual("appeal_pending", self.state(post_id))
        self.assertEqual("pending", self.conn.execute("SELECT status FROM blog_appeals WHERE id=?", (appeal["id"],)).fetchone()[0])

    def test_an_edited_sanctioned_post_waits_for_a_person(self):
        post_id = self.post()
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="remove", reason_code="spam")
        revision = self.row(post_id)["content_revision"]
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="改过了", expected_revision=revision)
        self.assertEqual(("pending", "system:resubmit"), (self.state(post_id), self.row(post_id)["moderated_by"]))
        new_revision = self.row(post_id)["content_revision"]
        # Neither an AI pass, nor the timeout, nor the quota may publish it.
        self.assertTrue(self.run_review("post", post_id, new_revision, result=verdict("none")).startswith("kept"))
        task = {"payload": {"target_type": "post", "target_id": post_id, "revision": new_revision}}
        with patch("classroom_app.database.get_db_connection", return_value=_ReuseConnection(self.conn)):
            self.assertTrue(asyncio.run(review.handle_blog_content_review_timeout(task)).startswith("noop"))
        self.run_review("post", post_id, new_revision, error=RuntimeError("down"), attempt=safety.REVIEW_MAX_ATTEMPTS - 1)
        self.assertEqual("pending", self.state(post_id))
        self.assertFalse(blog_service._can_view_post(self.conn, CLASSMATE, self.row(post_id)))
        self.assertEqual(1, review_queue.queue_counts(self.conn, TEACHER)["resubmitted"])
        detail = blog_service.get_post_detail(self.conn, AUTHOR, post_id)
        self.assertTrue(detail["moderation"]["resubmitted"])
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="restore", note="已改正")
        self.assertEqual("passed", self.state(post_id))

    def test_deleting_a_moderated_post_keeps_the_audit_trail(self):
        post_id = self.post(mode="anonymous")
        moderation.reveal_identity(self.conn, TEACHER, "post", post_id, reason_kind="report")
        blog_service.delete_post(self.conn, AUTHOR, post_id)
        self.assertEqual("deleted", self.row(post_id)["status"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM blog_moderation_logs WHERE post_id=?", (post_id,)).fetchone()[0])
        self.assertFalse(blog_service._can_view_post(self.conn, AUTHOR, self.row(post_id)))
        self.assertEqual([], blog_service.get_my_posts(self.conn, AUTHOR)["posts"])
        unmoderated = self.post(title="普通帖")
        blog_service.delete_post(self.conn, AUTHOR, unmoderated)
        self.assertIsNone(self.conn.execute("SELECT id FROM blog_posts WHERE id=?", (unmoderated,)).fetchone())

    def test_appealed_or_pending_items_cannot_merely_be_confirmed(self):
        post_id = self.post()
        moderation.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        moderation.create_appeal(self.conn, AUTHOR, post_id, reason="理由")
        with self.assertRaises(moderation.ModerationError):
            moderation.moderate(self.conn, TEACHER, "post", post_id, action="confirm")
        self.assertEqual("appeal_pending", self.state(post_id))

    def test_a_late_high_risk_verdict_still_removes_after_the_timeout(self):
        post_id = self.post(reviewed=False)
        task = {"payload": {"target_type": "post", "target_id": post_id, "revision": 1}}
        with patch("classroom_app.database.get_db_connection", return_value=_ReuseConnection(self.conn)):
            asyncio.run(review.handle_blog_content_review_timeout(task))
        self.assertEqual("flagged", self.state(post_id))
        self.run_review("post", post_id, result=verdict("high", ["illegal"]))
        self.assertEqual("removed", self.state(post_id))

    def test_held_comment_images_and_reports_stay_with_the_author(self):
        post_id = self.post()
        image = "e" * 64
        self.conn.execute(
            "INSERT INTO blog_comments(post_id,author_identity,author_role,author_user_pk,author_display_name,content_md,"
            "attachments_json,status,created_at,updated_at,moderation_state) VALUES(?, 'student:22','student',22,'李四',"
            "'x', ?, 'active','2026','2026','pending')",
            (post_id, json.dumps([{"file_hash": image, "name": "a.png"}])),
        )
        comment_id = self.conn.execute("SELECT MAX(id) FROM blog_comments").fetchone()[0]
        self.assertIsNone(blog_service.get_media_asset_for_user(self.conn, OUTSIDER, image))
        self.assertIsNotNone(blog_service.get_media_asset_for_user(self.conn, CLASSMATE, image))
        with self.assertRaises(ValueError):
            blog_community_service.create_report(self.conn, OUTSIDER, target_type="comment", target_id=comment_id, reason_code="spam")

    def test_a_released_comment_notifies_the_post_author(self):
        post_id = self.post()
        comment_id = self.comment(post_id, CLASSMATE, content="加微信 abc_12345 详聊")
        self.assertEqual([], [n for n in self.notifications("student", 21) if n["category"] == "blog_comment"])
        self.run_review("comment", comment_id, result=verdict("none"))
        self.assertEqual(1, len([n for n in self.notifications("student", 21) if n["category"] == "blog_comment"]))

    def test_reveal_never_applies_to_drafts(self):
        draft_id = self.post(mode="anonymous", status="draft")
        with self.assertRaises(moderation.ModerationError):
            moderation.reveal_identity(self.conn, TEACHER, "post", draft_id, reason_kind="report")


if __name__ == "__main__":
    unittest.main()
