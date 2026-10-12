"""P1 of the blog closed loop: data model, backfill and dual-write
(docs/blog-closed-loop-plan-2026-10-12.md §8–§9).

Real blog schema in an in-memory SQLite database; no network or AI.
"""
import unittest
from unittest.mock import patch

from classroom_app.db.schema_blog_closed_loop import backfill_blog_closed_loop_data
from classroom_app.services import blog_service
from tests.test_blog_p0_hardening import AUTHOR, CLASSMATE, TEACHER, BlogP0Fixture

IMAGE_A = "a" * 64
IMAGE_B = "b" * 64


class SchemaTests(BlogP0Fixture):
    def columns(self, table):
        return {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def test_new_columns_and_tables_exist(self):
        self.assertTrue({"moderation_state", "content_revision", "published_at", "deleted_at"} <= self.columns("blog_posts"))
        self.assertTrue({"root_comment_id", "reply_to_comment_id", "author_display_mode"} <= self.columns("blog_comments"))
        self.assertIn("target_mode", self.columns("blog_follows"))
        self.assertTrue({"reason_code", "target_type", "target_id"} <= self.columns("blog_moderation_logs"))
        self.assertTrue({"aggregate_key", "ref_post_id", "target_state"} <= self.columns("message_center_notifications"))
        for table in ("blog_content_reviews", "blog_comment_media", "blog_appeals", "blog_safety_terms",
                      "blog_author_daily_stats", "user_notification_preferences"):
            self.assertTrue(self.columns(table), table)


class BackfillTests(BlogP0Fixture):
    def legacy_post(self, status, title):
        post_id = self.post(title=title, content=f"{title} 的正文")
        # Shape the row as it was before P1 (new columns at their defaults).
        self.conn.execute(
            "UPDATE blog_posts SET status=?, moderation_state='none', published_at=NULL, "
            "moderation_reason_code='', moderation_note='' WHERE id=?",
            (status, post_id),
        )
        return post_id

    def legacy_comment(self, post_id, parent_id=None, attachments="[]", content="c"):
        cursor = self.conn.execute(
            "INSERT INTO blog_comments(post_id,parent_comment_id,author_identity,author_role,author_user_pk,"
            "author_display_name,content_md,attachments_json,status,created_at,updated_at)"
            " VALUES(?,?,'student:22','student',22,'李四',?,?,'active','2026-01-01','2026-01-01')",
            (post_id, parent_id, content, attachments),
        )
        return cursor.lastrowid

    def test_dry_run_reports_and_writes_nothing(self):
        hidden = self.legacy_post("moderated", "被隐藏")
        self.legacy_post("published", "已发布")
        report = backfill_blog_closed_loop_data(self.conn, apply=False)
        self.assertEqual(1, report["posts_marked_limited"])
        self.assertEqual(1, report["posts_marked_passed"])
        self.assertEqual(2, report["posts_published_at"])
        self.assertEqual("none", self.row(hidden)["moderation_state"])

    def test_apply_mirrors_hidden_posts_without_touching_legacy_status(self):
        hidden = self.legacy_post("moderated", "被隐藏")
        published = self.legacy_post("published", "已发布")
        draft = self.legacy_post("draft", "草稿")
        backfill_blog_closed_loop_data(self.conn, apply=True)
        row = self.row(hidden)
        self.assertEqual(("published", "limited", "other"),
                         (row["status"], row["moderation_state"], row["moderation_reason_code"]))  # status converged in P3
        self.assertIn("系统升级前", row["moderation_note"])
        self.assertEqual("passed", self.row(published)["moderation_state"])
        self.assertEqual(self.row(published)["created_at"], self.row(published)["published_at"])
        self.assertIsNone(self.row(draft)["published_at"])
        self.assertEqual("none", self.row(draft)["moderation_state"])

    def test_apply_is_idempotent(self):
        self.legacy_post("moderated", "被隐藏")
        post_id = self.legacy_post("published", "已发布")
        top = self.legacy_comment(post_id)
        self.legacy_comment(post_id, parent_id=top)
        backfill_blog_closed_loop_data(self.conn, apply=True)
        second = backfill_blog_closed_loop_data(self.conn, apply=False)
        self.assertEqual({}, {key: value for key, value in second.items() if value})

    def test_nested_replies_get_their_floor_and_direct_target(self):
        post_id = self.post()
        top = self.legacy_comment(post_id)
        child = self.legacy_comment(post_id, parent_id=top)
        grandchild = self.legacy_comment(post_id, parent_id=child)
        backfill_blog_closed_loop_data(self.conn, apply=True)
        rows = {
            row["id"]: (row["root_comment_id"], row["reply_to_comment_id"])
            for row in self.conn.execute("SELECT id, root_comment_id, reply_to_comment_id FROM blog_comments")
        }
        self.assertEqual((top, None), rows[top])
        self.assertEqual((top, top), rows[child])
        self.assertEqual((top, child), rows[grandchild])

    def test_comment_media_is_backfilled_from_attachments_and_inline_images(self):
        post_id = self.post()
        comment = self.legacy_comment(
            post_id,
            attachments=f'[{{"file_hash": "{IMAGE_A}", "name": "x"}}]',
            content=f"看图 ![](/api/blog/image/{IMAGE_B})",
        )
        backfill_blog_closed_loop_data(self.conn, apply=True)
        media = {
            (row["file_hash"], row["media_kind"])
            for row in self.conn.execute(
                "SELECT file_hash, media_kind FROM blog_comment_media WHERE comment_id=?", (comment,)
            )
        }
        self.assertEqual({(IMAGE_A, "attachment"), (IMAGE_B, "inline")}, media)


class DualWriteTests(BlogP0Fixture):
    def test_new_posts_record_publication_and_review_state(self):
        published = self.row(self.post())
        self.assertEqual(("passed", 1), (published["moderation_state"], published["content_revision"]))
        self.assertEqual(published["created_at"], published["published_at"])
        draft = self.row(self.post(status="draft", title="草稿"))
        self.assertEqual("none", draft["moderation_state"])
        self.assertIsNone(draft["published_at"])

    def test_publishing_a_draft_stamps_publication_time_once(self):
        draft_id = self.post(status="draft", title="草稿")

        def save(status):
            revision = self.row(draft_id)["content_revision"]
            blog_service.update_post(self.conn, AUTHOR, draft_id, status=status, expected_revision=revision)

        save("published")
        first = self.row(draft_id)["published_at"]
        self.assertIsNotNone(first)
        # A student's first publication waits for the content review (P3, D1).
        self.assertEqual("pending", self.row(draft_id)["moderation_state"])
        save("draft")
        save("published")
        self.assertEqual(first, self.row(draft_id)["published_at"])

    def test_latest_feed_orders_by_publication_not_first_save(self):
        old_draft = self.post(status="draft", title="早写晚发")
        self.post(title="中间发布的帖子", content="另一篇")
        self.conn.execute("UPDATE blog_posts SET created_at='2020-01-01T00:00:00' WHERE id=?", (old_draft,))
        revision = self.row(old_draft)["content_revision"]
        blog_service.update_post(self.conn, AUTHOR, old_draft, status="published", expected_revision=revision)
        self.conn.execute("UPDATE blog_posts SET moderation_state='passed' WHERE id=?", (old_draft,))
        titles = [item["title"] for item in blog_service.list_posts(self.conn, CLASSMATE)["posts"]]
        self.assertEqual("早写晚发", titles[0])

    def test_hide_and_restore_mirror_into_moderation_state_and_log(self):
        post_id = self.post()
        blog_service.hide_post(self.conn, TEACHER, post_id, reason="含有个人信息")
        row = self.row(post_id)
        self.assertEqual(
            ("published", "limited", "other", "含有个人信息", "teacher:7"),
            (row["status"], row["moderation_state"], row["moderation_reason_code"], row["moderation_note"], row["moderated_by"]),
        )
        blog_service.hide_post(self.conn, TEACHER, post_id)
        row = self.row(post_id)
        self.assertEqual(("published", "passed", ""), (row["status"], row["moderation_state"], row["moderation_reason_code"]))
        logs = self.conn.execute(
            "SELECT action, target_type, target_id, reason_code, note FROM blog_moderation_logs WHERE post_id=? ORDER BY id",
            (post_id,),
        ).fetchall()
        self.assertEqual(
            [("limit", "post", post_id, "other", "含有个人信息"), ("restore", "post", post_id, "", "恢复可见")],
            [tuple(log) for log in logs],
        )

    def test_comments_record_floor_target_and_snapshot(self):
        post_id = self.post()
        top = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="一楼")["id"]
        reply = blog_service.add_comment(self.conn, AUTHOR, post_id, content_md="回复一楼", parent_comment_id=top)["id"]
        deeper = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="再回复", parent_comment_id=reply)["id"]
        rows = {
            row["id"]: (row["root_comment_id"], row["reply_to_comment_id"], row["author_display_snapshot"])
            for row in self.conn.execute("SELECT * FROM blog_comments")
        }
        self.assertEqual((top, None, "李四"), rows[top])
        self.assertEqual((top, top, "张三"), rows[reply])
        self.assertEqual((top, reply, "李四"), rows[deeper])

    def test_comment_media_rows_follow_comment_lifecycle(self):
        post_id = self.post()
        with patch.object(
            blog_service, "_normalize_comment_attachments",
            return_value=[{"file_hash": IMAGE_A, "name": "图", "type": "image"}],
        ):
            comment = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="有图")["id"]
        count = lambda: self.conn.execute("SELECT COUNT(*) FROM blog_comment_media").fetchone()[0]
        self.assertEqual(1, count())
        blog_service.delete_comment(self.conn, CLASSMATE, comment)
        self.assertEqual(0, count())


class RevisionLockTests(BlogP0Fixture):
    def test_comments_and_pins_do_not_invalidate_an_open_edit(self):
        post_id = self.post()
        revision = self.row(post_id)["content_revision"]
        blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="编辑期间的评论")
        blog_service.pin_post(self.conn, TEACHER, post_id)
        result = blog_service.update_post(self.conn, AUTHOR, post_id, title="改好了", expected_revision=revision)
        self.assertEqual(revision + 1, result["content_revision"])
        self.assertEqual("改好了", self.row(post_id)["title"])

    def test_a_concurrent_content_edit_is_a_conflict(self):
        post_id = self.post()
        revision = self.row(post_id)["content_revision"]
        blog_service.update_post(self.conn, AUTHOR, post_id, title="另一台设备", expected_revision=revision)
        with self.assertRaises(blog_service.BlogRevisionConflict):
            blog_service.update_post(self.conn, AUTHOR, post_id, title="这台设备", expected_revision=revision)

    def test_updated_at_lock_still_works_for_older_callers(self):
        post_id = self.post()
        version = self.row(post_id)["updated_at"]
        blog_service.update_post(self.conn, AUTHOR, post_id, title="新", expected_updated_at=version)
        with self.assertRaises(blog_service.BlogRevisionConflict):
            blog_service.update_post(self.conn, AUTHOR, post_id, title="旧", expected_updated_at=version)

    def test_serialized_post_exposes_revision_and_publication(self):
        post_id = self.post()
        summary = blog_service._serialize_post_summary(self.row(post_id), viewer_identity="student:21")
        self.assertEqual(1, summary["content_revision"])
        self.assertIsNotNone(summary["published_at"])
        self.assertEqual("passed", summary["moderation_state"])


if __name__ == "__main__":
    unittest.main()
