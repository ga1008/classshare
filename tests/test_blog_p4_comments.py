"""P4 of the blog closed loop: comment floors, editing and soft delete
(docs/blog-closed-loop-plan-2026-10-12.md §7, acceptance in §9.3).

Real blog schema in an in-memory SQLite database; no network or AI.
"""
import statistics
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services import blog_comment_service as comments
from classroom_app.services import blog_moderation_service as moderation
from classroom_app.services import blog_safety_service as safety
from classroom_app.services import blog_service
from tests.test_blog_p0_hardening import AUTHOR, CLASSMATE, OTHER_TEACHER, OUTSIDER, TEACHER, BlogP0Fixture


class FloorFixture(BlogP0Fixture):
    def setUp(self):
        super().setUp()
        # Rate limits are P3's concern; these tests write many comments quickly.
        guard = patch.object(safety, "COMMENTS_PER_WINDOW", 10_000)
        guard.start()
        self.addCleanup(guard.stop)
        self.post_id = self.post(TEACHER, title="讨论帖")

    def comment(self, user=CLASSMATE, content="评论", parent=None, mode="real_name"):
        result = blog_service.add_comment(self.conn, user, self.post_id, content_md=content,
                                          parent_comment_id=parent, author_display_mode=mode)
        self.conn.commit()
        return int(result["id"])

    def floors(self, viewer=OUTSIDER, **kwargs):
        return comments.list_floors(self.conn, viewer, self.post_id, **kwargs)

    def row(self, comment_id):
        return dict(self.conn.execute("SELECT * FROM blog_comments WHERE id=?", (comment_id,)).fetchone())


class FloorTests(FloorFixture):
    def test_floors_page_and_replies_stay_flat(self):
        floor_ids = [self.comment(content=f"楼{i}") for i in range(25)]
        for i in range(5):
            self.comment(OUTSIDER, content=f"回复{i}", parent=floor_ids[0])
        first = self.floors(limit=20)
        self.assertEqual((25, 20, True), (first["total"], len(first["comments"]), first["has_more"]))
        floor = first["comments"][0]
        self.assertEqual((5, 3, True), (floor["reply_count"], len(floor["replies"]), floor["has_more_replies"]))
        self.assertTrue(all(not reply["replies"] for reply in floor["replies"]))
        second = self.floors(page=2, limit=20)
        self.assertEqual((5, False), (len(second["comments"]), second["has_more"]))
        rest = comments.list_replies(self.conn, OUTSIDER, self.post_id, floor_ids[0], offset=3)
        self.assertEqual((["回复3", "回复4"], False), ([r["content_md"] for r in rest["replies"]], rest["has_more"]))

    def test_a_reply_to_a_reply_joins_the_floor_and_names_its_target(self):
        floor = self.comment(content="楼主观点")
        first = self.comment(OUTSIDER, content="第一条回复", parent=floor, mode="anonymous")
        second = self.comment(AUTHOR, content="回复匿名同学", parent=first)
        replies = self.floors()["comments"][0]["replies"]
        self.assertEqual([first, second], [r["id"] for r in replies])
        self.assertEqual(floor, self.row(second)["root_comment_id"])
        target = replies[1]["reply_to"]
        self.assertTrue(target["display_name"].startswith("匿名同学·"))
        self.assertIsNone(replies[0]["reply_to"])  # a direct reply to the floor needs no label

    def test_deep_link_opens_the_right_page_and_reply(self):
        floor_ids = [self.comment(content=f"楼{i}") for i in range(25)]
        target_floor = floor_ids[22]
        replies = [self.comment(OUTSIDER, content=f"回复{i}", parent=target_floor) for i in range(6)]
        result = self.floors(limit=20, around=replies[5])
        self.assertEqual((2, replies[5]), (result["page"], result["focus_comment_id"]))
        floor = next(f for f in result["comments"] if f["id"] == target_floor)
        self.assertIn(replies[5], [r["id"] for r in floor["replies"]])

    def test_deep_link_to_a_hidden_comment_falls_back_to_the_first_page(self):
        floor = self.comment(content="加微信 abc_12345")  # held by L1, others cannot see it
        result = self.floors(around=floor)
        self.assertEqual((1, None), (result["page"], result["focus_comment_id"]))


class EditTests(FloorFixture):
    def test_author_edits_within_the_window(self):
        comment_id = self.comment(content="原文")
        view = comments.edit_comment(self.conn, CLASSMATE, comment_id, content_md="改过的", expected_revision=1)
        self.assertEqual(("改过的", True, 2), (view["content_md"], view["edited"], view["content_revision"]))
        reviews = self.conn.execute("SELECT content_revision FROM blog_content_reviews WHERE target_type='comment' AND target_id=?",
                                    (comment_id,)).fetchall()
        self.assertEqual({1, 2}, {row[0] for row in reviews})

    def test_edit_rules(self):
        comment_id = self.comment(content="原文")
        with self.assertRaises(PermissionError):
            comments.edit_comment(self.conn, OUTSIDER, comment_id, content_md="别人改")
        with self.assertRaises(blog_service.BlogRevisionConflict):
            comments.edit_comment(self.conn, CLASSMATE, comment_id, content_md="x", expected_revision=9)
        with self.assertRaises(safety.BlogSafetyBlocked):
            comments.edit_comment(self.conn, CLASSMATE, comment_id, content_md="代写论文")
        old = (datetime.now() - timedelta(minutes=comments.EDIT_WINDOW_MINUTES + 1)).isoformat()
        self.conn.execute("UPDATE blog_comments SET created_at=? WHERE id=?", (old, comment_id))
        with self.assertRaises(ValueError):
            comments.edit_comment(self.conn, CLASSMATE, comment_id, content_md="太晚了")

    def test_a_sanctioned_comment_cannot_be_edited(self):
        comment_id = self.comment(content="原文")
        moderation.moderate(self.conn, TEACHER, "comment", comment_id, action="limit", reason_code="spam")
        with self.assertRaises(blog_service.BlogModerationLocked):
            comments.edit_comment(self.conn, CLASSMATE, comment_id, content_md="改")


class SoftDeleteTests(FloorFixture):
    def test_deleting_a_floor_keeps_its_replies_under_a_placeholder(self):
        floor = self.comment(content="楼")
        reply = self.comment(OUTSIDER, content="回复", parent=floor)
        before = blog_service._get_post_raw(self.conn, self.post_id)["comment_count"]
        blog_service.delete_comment(self.conn, CLASSMATE, floor)
        self.assertEqual("deleted", self.row(floor)["status"])
        view = self.floors()["comments"][0]
        self.assertEqual((True, "该评论已被作者删除", ""), (view["withheld"], view["placeholder_text"], view["content_md"]))
        self.assertEqual([reply], [r["id"] for r in view["replies"]])
        self.assertEqual(before - 1, blog_service._get_post_raw(self.conn, self.post_id)["comment_count"])

    def test_a_deleted_reply_or_lonely_floor_disappears(self):
        floor = self.comment(content="楼")
        reply = self.comment(OUTSIDER, content="回复", parent=floor)
        blog_service.delete_comment(self.conn, OUTSIDER, reply)
        self.assertEqual([], self.floors()["comments"][0]["replies"])
        blog_service.delete_comment(self.conn, CLASSMATE, floor)
        self.assertEqual([], self.floors()["comments"])

    def test_deleting_someone_elses_comment_needs_a_reason_and_notifies(self):
        floor = self.comment(content="楼")
        with self.assertRaises(PermissionError):
            blog_service.delete_comment(self.conn, OUTSIDER, floor)
        with self.assertRaises(PermissionError):
            blog_service.delete_comment(self.conn, OTHER_TEACHER, floor)  # neither a moderator nor the post author
        self.conn.execute("UPDATE blog_posts SET author_identity='teacher:8', author_user_pk=8 WHERE id=?", (self.post_id,))
        with self.assertRaises(ValueError):
            blog_service.delete_comment(self.conn, OTHER_TEACHER, floor)
        result = blog_service.delete_comment(self.conn, OTHER_TEACHER, floor, reason="与主题无关")
        self.assertEqual("post_author", result["deleted_by"])
        notice = self.conn.execute(
            "SELECT title, body_preview FROM message_center_notifications WHERE recipient_role='student' AND recipient_user_pk=22 "
            "AND category='blog_moderation'").fetchone()
        self.assertEqual("你的评论已被帖子作者删除", notice[0])
        self.assertIn("与主题无关", notice[1])

    def test_moderator_deletion_is_logged(self):
        floor = self.comment(content="楼")
        result = blog_service.delete_comment(self.conn, TEACHER, floor, reason="违规")
        self.assertEqual("moderator", result["deleted_by"])
        self.assertEqual("delete_comment", self.conn.execute("SELECT action FROM blog_moderation_logs").fetchone()[0])


class ViewAndPerformanceTests(FloorFixture):
    def test_single_comment_view_for_local_insertion(self):
        floor = self.comment(content="楼")
        reply = self.comment(OUTSIDER, content="回复", parent=floor)
        view = comments.get_comment_view(self.conn, OUTSIDER, reply)
        self.assertEqual((False, floor, True), (view["is_floor"], view["root_comment_id"], view["can_edit"]))
        self.assertTrue(comments.get_comment_view(self.conn, CLASSMATE, floor)["is_floor"])

    def test_a_thousand_comments_stay_fast(self):
        now = datetime.now()
        floors = [(self.post_id, f"楼{i}", (now + timedelta(seconds=i)).isoformat()) for i in range(200)]
        self.conn.executemany(
            "INSERT INTO blog_comments(post_id,author_identity,author_role,author_user_pk,author_display_name,content_md,"
            "status,created_at,updated_at,moderation_state) VALUES(?,'student:22','student',22,'李四',?,'active',?,?,'none')",
            [(p, text, ts, ts) for p, text, ts in floors],
        )
        self.conn.execute("UPDATE blog_comments SET root_comment_id=id WHERE parent_comment_id IS NULL")
        floor_ids = [r[0] for r in self.conn.execute("SELECT id FROM blog_comments WHERE parent_comment_id IS NULL").fetchall()]
        replies = []
        for index, floor_id in enumerate(floor_ids):
            for j in range(4):
                ts = (now + timedelta(seconds=1000 + index * 4 + j)).isoformat()
                replies.append((self.post_id, floor_id, floor_id, floor_id, f"回复{j}", ts, ts))
        self.conn.executemany(
            "INSERT INTO blog_comments(post_id,parent_comment_id,root_comment_id,reply_to_comment_id,author_identity,author_role,"
            "author_user_pk,author_display_name,content_md,status,created_at,updated_at,moderation_state) "
            "VALUES(?,?,?,?,'student:23','student',23,'王五',?,'active',?,?,'none')",
            replies,
        )
        self.conn.commit()
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM blog_comments").fetchone()[0], 1000)
        timings = []
        for _ in range(20):
            start = time.perf_counter()
            result = self.floors(viewer=OUTSIDER)
            timings.append((time.perf_counter() - start) * 1000)
        self.assertEqual(20, len(result["comments"]))
        p95 = statistics.quantiles(timings, n=20)[18]
        self.assertLess(p95, 300, f"floors p95 {p95:.1f} ms")


class ReviewRegressionTests(FloorFixture):
    def test_a_hidden_comment_is_not_fetchable_or_answerable_by_others(self):
        held = self.comment(content="加微信 abc_12345")  # L1 holds it
        with self.assertRaises(ValueError):
            comments.get_comment_view(self.conn, OUTSIDER, held)
        self.assertEqual(held, comments.get_comment_view(self.conn, CLASSMATE, held)["id"])
        with self.assertRaises(ValueError):
            blog_service.add_comment(self.conn, OUTSIDER, self.post_id, content_md="回复", parent_comment_id=held)

    def test_the_legacy_read_keeps_whole_floors(self):
        floor = self.comment(content="楼")
        for i in range(5):
            self.comment(OUTSIDER, content=f"回复{i}", parent=floor)
        legacy = blog_service.list_comments(self.conn, OUTSIDER, self.post_id, page=1, limit=100)
        self.assertEqual(5, len(legacy["comments"][0]["replies"]))

    def test_an_edit_drops_the_previous_verdict_text(self):
        comment_id = self.comment(content="原文")
        self.conn.execute("UPDATE blog_comments SET moderation_state='flagged', moderation_reason_code='spam', "
                          "moderation_note='旧说明' WHERE id=?", (comment_id,))
        comments.edit_comment(self.conn, CLASSMATE, comment_id, content_md="新内容")
        row = self.row(comment_id)
        self.assertEqual(("", ""), (row["moderation_reason_code"], row["moderation_note"]))


if __name__ == "__main__":
    unittest.main()
