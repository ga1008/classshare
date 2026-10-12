"""P0 hardening of the blog center (docs/blog-closed-loop-plan-2026-10-12.md §9.2).

Uses the real blog schema in an in-memory SQLite database; no network or AI.
"""
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from classroom_app.db import schema_scheduler
from classroom_app.db.migrations import _ensure_organization_catalog_schema
from classroom_app.db.schema_classroom_activity import ensure_classroom_activity_schema
from classroom_app.db.schema_foundation import ensure_foundation_schema
from classroom_app.db.schema_learning_blog import ensure_learning_blog_signature_schema
from classroom_app.db.schema_offering_class_links import ensure_offering_class_links_schema
from classroom_app.routers import blog as blog_router
from classroom_app.services import blog_ai_service, blog_notifications, blog_section_service, blog_service

TEACHER = {"id": 7, "role": "teacher", "name": "王老师"}
OTHER_TEACHER = {"id": 8, "role": "teacher", "name": "李老师"}
AUTHOR = {"id": 21, "role": "student", "name": "张三"}
CLASSMATE = {"id": 22, "role": "student", "name": "李四"}
OUTSIDER = {"id": 23, "role": "student", "name": "王五"}


class _ReuseConnection:
    """Stand-in for get_db_connection() that yields the fixture connection."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class BlogP0Fixture(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        for target in (
            "classroom_app.services.organization_scope_service.get_configured_db_engine",
            "classroom_app.services.blog_section_service.get_configured_db_engine",
            "classroom_app.services.blog_service.get_configured_db_engine",
            "classroom_app.db.schema_scheduler.get_configured_db_engine",
        ):
            guard = patch(target, return_value="sqlite")
            guard.start()
            self.addCleanup(guard.stop)
        # P3 queues reviews in scheduled_tasks; each in-memory DB needs the table.
        guard = patch.object(schema_scheduler, "_SCHEMA_READY", False)
        guard.start()
        self.addCleanup(guard.stop)
        ensure_foundation_schema(self.conn)
        _ensure_organization_catalog_schema(self.conn)
        ensure_classroom_activity_schema(self.conn)
        ensure_offering_class_links_schema(self.conn, force=True)
        ensure_learning_blog_signature_schema(self.conn)
        blog_section_service.ensure_default_blog_sections(self.conn)
        schema_scheduler.ensure_scheduler_schema(self.conn)
        for pk, name in ((7, "王老师"), (8, "李老师")):
            self.conn.execute(
                "INSERT INTO teachers(id,name,email,hashed_password,is_active) VALUES(?,?,?,'x',1)",
                (pk, name, f"t{pk}@example.test"),
            )
        # Teacher 7 is a blog moderator (P3, plan D7); teacher 8 is an ordinary teacher.
        self.conn.execute("INSERT INTO blog_moderators(teacher_id, granted_by, created_at) VALUES(7, 'test', '2026')")
        self.conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(30,'软件一班',7)")
        self.conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(31,'软件二班',8)")
        self.conn.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES(20,'程序设计',7)")
        self.conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(40,20,30,7)")
        for pk, number, name, class_id in (
            (21, "2024001", "张三", 30),
            (22, "2024002", "李四", 30),
            (23, "2024003", "王五", 31),
        ):
            self.conn.execute(
                "INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) VALUES(?,?,?,?,'active')",
                (pk, number, name, class_id),
            )
        self.conn.commit()

    def post(self, user=AUTHOR, *, title="标题", content="正文", mode="real_name", status="published", reviewed=True):
        result = blog_service.create_post(
            self.conn, user, title=title, content_md=content, author_display_mode=mode, status=status,
        )
        if reviewed:
            # Stand-in for a passed AI review (P3): student posts start pending.
            self.conn.execute(
                "UPDATE blog_posts SET moderation_state='passed' WHERE id=? AND moderation_state='pending'",
                (int(result["id"]),),
            )
        self.conn.commit()
        return int(result["id"])

    def row(self, post_id):
        return dict(self.conn.execute("SELECT * FROM blog_posts WHERE id=?", (post_id,)).fetchone())


class ModerationAndVisibilityTests(BlogP0Fixture):
    def test_author_cannot_withdraw_or_republish_a_hidden_post(self):
        post_id = self.post()
        blog_service.hide_post(self.conn, TEACHER, post_id)
        version = self.row(post_id)["updated_at"]
        # P3: the sanction lives in moderation_state; status changes stay locked.
        with self.assertRaises(blog_service.BlogModerationLocked):
            blog_service.update_post(self.conn, AUTHOR, post_id, status="draft", expected_updated_at=version)
        self.assertEqual(("published", "limited"), (self.row(post_id)["status"], self.row(post_id)["moderation_state"]))

    def test_author_may_still_edit_text_of_a_hidden_post(self):
        post_id = self.post()
        blog_service.hide_post(self.conn, TEACHER, post_id)
        version = self.row(post_id)["updated_at"]
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="改正后的正文", expected_updated_at=version)
        row = self.row(post_id)
        self.assertEqual("改正后的正文", row["content_md"])
        # The corrected text goes back to review instead of staying hidden.
        self.assertEqual(("published", "pending"), (row["status"], row["moderation_state"]))

    def test_resending_the_current_status_is_not_a_status_change(self):
        post_id = self.post()
        blog_service.hide_post(self.conn, TEACHER, post_id)
        version = self.row(post_id)["updated_at"]
        # The web composer always sends a status; editing text must not be blocked by it.
        blog_service.update_post(
            self.conn, AUTHOR, post_id, title="新标题", status="moderated", expected_updated_at=version,
        )
        self.assertEqual("新标题", self.row(post_id)["title"])

    def test_moderator_cannot_read_someone_elses_draft(self):
        draft_id = self.post(status="draft")
        self.assertFalse(blog_service._can_view_post(self.conn, TEACHER, self.row(draft_id)))
        self.assertTrue(blog_service._can_view_post(self.conn, AUTHOR, self.row(draft_id)))

    def test_only_blog_moderators_see_hidden_posts(self):
        post_id = self.post()
        blog_service.hide_post(self.conn, TEACHER, post_id)
        self.assertTrue(blog_service._can_view_post(self.conn, TEACHER, self.row(post_id)))
        self.assertFalse(blog_service._can_view_post(self.conn, OTHER_TEACHER, self.row(post_id)))
        self.assertFalse(blog_service._can_view_post(self.conn, CLASSMATE, self.row(post_id)))

    def test_editorial_actions_refuse_drafts(self):
        draft_id = self.post(status="draft")
        for action in (blog_service.hide_post, blog_service.pin_post, blog_service.feature_post):
            with self.assertRaises(blog_service.BlogModerationLocked):
                action(self.conn, TEACHER, draft_id)
        self.assertEqual("draft", self.row(draft_id)["status"])

    def test_hide_then_restore_round_trips_a_published_post(self):
        post_id = self.post()
        self.assertEqual("limited", blog_service.hide_post(self.conn, TEACHER, post_id)["moderation_state"])
        self.assertEqual("passed", blog_service.hide_post(self.conn, TEACHER, post_id)["moderation_state"])
        self.assertEqual("published", self.row(post_id)["status"])

    def test_feature_notifies_once_and_never_the_featuring_teacher(self):
        calls = []
        notify = lambda *args: calls.append(args)
        post_id = self.post()
        blog_service.feature_post(self.conn, TEACHER, post_id, notify_callback=notify)
        blog_service.feature_post(self.conn, TEACHER, post_id, notify_callback=notify)  # off
        blog_service.feature_post(self.conn, TEACHER, post_id, notify_callback=notify)  # on again
        self.assertEqual(1, len(calls))
        own_post = self.post(TEACHER, title="教师自己的帖子")
        blog_service.feature_post(self.conn, TEACHER, own_post, notify_callback=notify)
        self.assertEqual(1, len(calls))


class UpdateRouteStatusCodeTests(BlogP0Fixture):
    """409 must only ever mean "version changed", so the editor can resolve it."""

    def put(self, post_id, body):
        import asyncio

        class _JsonRequest:
            async def json(inner):
                return body

        with patch.object(blog_router, "get_db_connection", return_value=_ReuseConnection(self.conn)):
            try:
                asyncio.run(blog_router.api_update_post(post_id, _JsonRequest(), None, user=AUTHOR))
            except HTTPException as exc:
                return exc.status_code
        return 200

    def test_moderation_lock_is_forbidden_not_conflict(self):
        post_id = self.post()
        blog_service.hide_post(self.conn, TEACHER, post_id)
        version = self.row(post_id)["updated_at"]
        self.assertEqual(403, self.put(post_id, {"status": "draft", "expected_updated_at": version}))

    def test_stale_version_is_conflict(self):
        post_id = self.post()
        self.assertEqual(409, self.put(post_id, {"title": "新", "expected_updated_at": "1999-01-01T00:00:00"}))

    def test_validation_error_is_bad_request_not_missing(self):
        post_id = self.post()
        version = self.row(post_id)["updated_at"]
        self.assertEqual(400, self.put(post_id, {"title": "   ", "expected_updated_at": version}))


class AnonymityTests(BlogP0Fixture):
    def comments_of(self, post_id, viewer):
        return blog_service.list_comments(self.conn, viewer, post_id)["comments"]

    def test_anonymous_author_comment_is_masked_as_op(self):
        post_id = self.post(mode="anonymous")
        blog_service.add_comment(self.conn, AUTHOR, post_id, content_md="楼主补充一句")
        blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="路过")
        self.conn.commit()
        for viewer in (CLASSMATE, TEACHER, OUTSIDER):
            comments = self.comments_of(post_id, viewer)
            op = next(item for item in comments if item["content_md"] == "楼主补充一句")
            self.assertEqual("楼主", op["author"]["display_name"])
            self.assertEqual("", op["author"]["identity"])
            self.assertIsNone(op["author"]["user_pk"])
            self.assertEqual("", op["author"]["avatar_url"])
            self.assertEqual("anon", op["author"]["avatar_kind"])
            self.assertTrue(op["author"]["is_op"])
            serialized = json.dumps(comments, ensure_ascii=False)
            self.assertNotIn("张三", serialized)
            self.assertNotIn("student:21", serialized)

    def test_op_still_sees_and_can_delete_own_masked_comment(self):
        post_id = self.post(mode="anonymous")
        blog_service.add_comment(self.conn, AUTHOR, post_id, content_md="我的回复")
        self.conn.commit()
        comment = self.comments_of(post_id, AUTHOR)[0]
        self.assertTrue(comment["is_mine"])
        self.assertTrue(comment["can_delete"])

    def test_real_name_post_author_is_marked_op_but_not_masked(self):
        post_id = self.post()
        blog_service.add_comment(self.conn, AUTHOR, post_id, content_md="作者回复")
        self.conn.commit()
        author = self.comments_of(post_id, CLASSMATE)[0]["author"]
        self.assertTrue(author["is_op"])
        self.assertEqual("张三", author["display_name"])

    def test_anonymous_and_assistant_avatars_never_use_the_viewer_endpoint(self):
        anon_id = self.post(mode="anonymous")
        summary = blog_service._serialize_post_summary(self.row(anon_id), viewer_identity="student:22")
        self.assertEqual("", summary["author"]["avatar_url"])
        self.assertEqual("anon", summary["author"]["avatar_kind"])
        self.assertEqual("", blog_service._build_avatar_url("assistant", 0))
        self.assertEqual("assistant", blog_service._avatar_kind("assistant"))

    def test_reply_notification_from_anonymous_op_hides_identity(self):
        post_id = self.post(mode="anonymous")
        parent = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="请问一下")
        captured = []
        with patch.object(
            blog_notifications, "_insert_notification", side_effect=lambda conn, payload: captured.append(payload),
        ):
            blog_service.add_comment(
                self.conn, AUTHOR, post_id, content_md="回答你", parent_comment_id=parent["id"],
                notify_callback=blog_notifications.notify_new_comment,
            )
        self.assertEqual(1, len(captured))
        rendered = json.dumps(captured[0], ensure_ascii=False, default=str)
        self.assertIn("楼主", rendered)
        self.assertNotIn("张三", rendered)
        self.assertNotIn("student:21", rendered)

    def test_housekeeper_context_masks_the_anonymous_author(self):
        post_id = self.post(mode="anonymous")
        blog_service.add_comment(self.conn, AUTHOR, post_id, content_md="补充细节")
        self.conn.commit()
        context = blog_ai_service._build_recent_comments_context(
            self.conn, post_id, masked_author_identity="student:21",
        )
        self.assertIn("楼主", context)
        self.assertNotIn("张三", context)


class HousekeeperFailureTests(BlogP0Fixture):
    def test_generation_failure_notifies_privately_and_posts_nothing(self):
        post_id = self.post(content="@管家 帮我看看")
        self.conn.execute(
            "INSERT INTO blog_ai_reply_jobs(trigger_type,trigger_id,post_id,trigger_author_identity,status,created_at,updated_at)"
            " VALUES('post',?,?,'student:21','pending','2026-01-01','2026-01-01')",
            (post_id, post_id),
        )
        self.conn.commit()
        captured = []
        with patch.object(blog_ai_service, "get_db_connection", return_value=_ReuseConnection(self.conn)), patch.object(
            blog_notifications, "_insert_notification", side_effect=lambda conn, payload: captured.append(payload),
        ):
            for reason in ("管家暂时无法回复，请稍后重新 @管家", "again"):
                # The second abandon of the same job is a no-op: no duplicate notice.
                blog_ai_service._abandon_and_notify(
                    trigger_type="post", trigger_id=post_id, post_id=post_id,
                    recipient_role="student", recipient_pk=21, reason=reason,
                )
        job = self.conn.execute("SELECT status FROM blog_ai_reply_jobs WHERE trigger_id=?", (post_id,)).fetchone()
        self.assertEqual("failed_final", job["status"])
        self.assertEqual(1, len(captured))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM blog_comments").fetchone()[0])


class HousekeeperRetryAfterEditTests(BlogP0Fixture):
    def setUp(self):
        super().setUp()
        from classroom_app.db import schema_scheduler

        for target in (
            patch.object(schema_scheduler, "_SCHEMA_READY", False),
            patch("classroom_app.db.schema_scheduler.get_configured_db_engine", return_value="sqlite"),
        ):
            target.start()
            self.addCleanup(target.stop)
        schema_scheduler.ensure_scheduler_schema(self.conn)

    def test_resaving_after_a_reported_failure_rearms_the_finished_task(self):
        from classroom_app.services import blog_effects_service as effects

        post_id = self.post(content="@管家 帮我看看")
        task_id = effects.enqueue_blog_mention(self.conn, AUTHOR, trigger_type="post", trigger_id=post_id)
        # The attempt failed privately: job abandoned, scheduler task finished.
        self.conn.execute(
            "INSERT INTO blog_ai_reply_jobs(trigger_type,trigger_id,post_id,trigger_author_identity,status,created_at,updated_at)"
            " VALUES('post',?,?,'student:21','failed_final','2026-01-01T00:00:00','2026-01-01T00:00:00')",
            (post_id, post_id),
        )
        self.conn.execute("UPDATE scheduled_tasks SET status='done' WHERE id=?", (task_id,))
        self.conn.commit()

        # Without a re-save nothing is retried (no paid call, no repeat notice).
        effects.enqueue_blog_mention(self.conn, AUTHOR, trigger_type="post", trigger_id=post_id)
        self.assertEqual("done", self.conn.execute("SELECT status FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()[0])

        version = self.row(post_id)["updated_at"]
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="@管家 换个问法", expected_updated_at=version)
        effects.enqueue_blog_mention(self.conn, AUTHOR, trigger_type="post", trigger_id=post_id)
        self.assertEqual("pending", self.conn.execute("SELECT status FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()[0])
        job = self.conn.execute("SELECT status FROM blog_ai_reply_jobs WHERE trigger_id=?", (post_id,)).fetchone()
        self.assertEqual("failed", job["status"])  # _prepare_reply_job may take it again


class IdempotencyTests(BlogP0Fixture):
    def test_double_submitted_post_is_created_once(self):
        first = blog_service.create_post(self.conn, AUTHOR, title="同一篇", content_md="同样的正文")
        second = blog_service.create_post(self.conn, AUTHOR, title="同一篇", content_md="同样的正文")
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second.get("deduplicated"))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM blog_posts").fetchone()[0])

    def test_double_submitted_comment_is_created_and_notified_once(self):
        post_id = self.post()
        calls = []
        notify = lambda *args: calls.append(args)
        first = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="好文", notify_callback=notify)
        second = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="好文", notify_callback=notify)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, self.row(post_id)["comment_count"])

    def test_different_reply_targets_are_not_treated_as_duplicates(self):
        post_id = self.post()
        first = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="甲")
        repeat = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="甲")
        reply = blog_service.add_comment(self.conn, CLASSMATE, post_id, content_md="甲", parent_comment_id=first["id"])
        self.assertEqual(first["id"], repeat["id"])
        self.assertNotEqual(first["id"], reply["id"])

    def test_like_race_settles_without_double_count(self):
        post_id = self.post()
        blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        # A concurrent duplicate request arriving after the like row exists.
        self.assertFalse(
            blog_service._insert_once(
                self.conn,
                "INSERT INTO blog_likes (target_type,target_id,user_identity,user_role,user_pk,created_at)"
                " SELECT 'post', ?, 'student:22', 'student', 22, 'x'"
                " WHERE NOT EXISTS (SELECT 1 FROM blog_likes WHERE target_type='post' AND target_id=?"
                " AND user_identity='student:22')",
                (post_id, post_id),
            )
        )
        self.assertEqual(1, self.row(post_id)["like_count"])

    def test_unique_violation_rolls_back_instead_of_500(self):
        post_id = self.post()
        blog_service.toggle_bookmark(self.conn, CLASSMATE, post_id)
        self.conn.commit()
        inserted = blog_service._insert_once(
            self.conn,
            "INSERT INTO blog_bookmarks (post_id,user_identity,user_role,user_pk,created_at)"
            " VALUES (?, 'student:22', 'student', 22, 'x')",
            (post_id,),
        )
        self.assertFalse(inserted)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM blog_bookmarks").fetchone()[0])


class SearchTests(BlogP0Fixture):
    def titles(self, query):
        result = blog_service.list_posts(self.conn, CLASSMATE, query=query)
        return {item["title"] for item in result["posts"]}

    def test_search_is_case_insensitive(self):
        self.post(title="Python 入门")
        self.assertEqual({"Python 入门"}, self.titles("python"))

    def test_wildcards_are_literal(self):
        self.post(title="百分之百", content="甲")
        self.post(title="100% 完成", content="乙")
        self.assertEqual({"100% 完成"}, self.titles("%"))
        self.assertEqual(set(), self.titles("_"))


class UsersSearchTests(BlogP0Fixture):
    def search(self, user, q):
        with patch.object(blog_router, "get_db_connection", return_value=_ReuseConnection(self.conn)):
            return blog_router.api_users_search(q=q, class_id=None, user=user)["users"]

    def test_student_only_reaches_own_class_and_its_teachers(self):
        names = {item["name"] for item in self.search(CLASSMATE, "王老")}
        self.assertEqual({"王老师"}, names)
        self.assertEqual([], self.search(CLASSMATE, "王五"))  # another class
        self.assertEqual([], self.search(CLASSMATE, "李老"))  # does not teach this class
        self.assertEqual({"张三"}, {item["name"] for item in self.search(CLASSMATE, "张三")})

    def test_student_cannot_probe_student_numbers_or_single_characters(self):
        self.assertEqual([], self.search(CLASSMATE, "2024"))
        self.assertEqual([], self.search(CLASSMATE, "张"))

    def test_student_results_do_not_map_nicknames(self):
        self.conn.execute("UPDATE students SET nickname='风之子' WHERE id=21")
        self.assertEqual([], self.search(CLASSMATE, "风之子"))
        self.assertTrue(all(item["nickname"] == "" for item in self.search(CLASSMATE, "张三")))

    def test_teacher_search_is_unrestricted(self):
        names = {item["name"] for item in self.search(TEACHER, "王")}
        self.assertIn("王五", names)


class LogoutNavigationTests(unittest.TestCase):
    """`![x](/logout)` in user Markdown must not end anyone's session."""

    def deliberate(self, **headers):
        from starlette.requests import Request

        from classroom_app.routers.ui_parts.auth import _is_deliberate_logout_navigation

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/logout",
            "headers": [(key.replace("_", "-").encode(), value.encode()) for key, value in headers.items()],
        }
        return _is_deliberate_logout_navigation(Request(scope))

    def test_embedded_image_or_frame_request_is_ignored(self):
        self.assertFalse(self.deliberate(sec_fetch_dest="image", sec_fetch_site="same-origin"))
        self.assertFalse(self.deliberate(sec_fetch_dest="iframe", sec_fetch_site="same-origin"))

    def test_cross_site_navigation_is_ignored(self):
        self.assertFalse(self.deliberate(sec_fetch_dest="document", sec_fetch_site="cross-site"))

    def test_clicking_the_logout_link_still_works(self):
        self.assertTrue(self.deliberate(sec_fetch_dest="document", sec_fetch_site="same-origin"))
        self.assertTrue(self.deliberate(sec_fetch_dest="document", sec_fetch_site="none"))

    def test_browsers_without_fetch_metadata_keep_old_behaviour(self):
        self.assertTrue(self.deliberate())


class UploadInspectionTests(unittest.TestCase):
    def image_bytes(self, fmt="PNG", size=(4, 4), exif=None):
        buffer = io.BytesIO()
        image = Image.new("RGB", size, (200, 30, 30))
        kwargs = {"exif": exif} if exif is not None else {}
        image.save(buffer, format=fmt, **kwargs)
        return buffer.getvalue()

    def test_valid_png_passes_with_server_detected_mime(self):
        _, mime, width, height = blog_router._inspect_blog_image(self.image_bytes())
        self.assertEqual(("image/png", 4, 4), (mime, width, height))

    def test_non_image_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            blog_router._inspect_blog_image(b"not an image at all")
        self.assertEqual(400, caught.exception.status_code)

    def test_oversized_canvas_is_rejected_before_decoding(self):
        with patch.object(blog_router, "MAX_BLOG_IMAGE_PIXELS", 10):
            with self.assertRaises(HTTPException) as caught:
                blog_router._inspect_blog_image(self.image_bytes())
        self.assertEqual(400, caught.exception.status_code)

    def test_photo_metadata_is_stripped_from_jpeg_png_and_webp(self):
        for fmt, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")):
            with self.subTest(fmt=fmt):
                exif = Image.Exif()
                exif[0x010F] = "SecretCamera"  # Make
                raw = self.image_bytes(fmt, exif=exif.tobytes())
                with Image.open(io.BytesIO(raw)) as original:
                    self.assertIn(0x010F, original.getexif())  # fixture really carries it
                clean, detected, _, _ = blog_router._inspect_blog_image(raw)
                self.assertEqual(mime, detected)
                with Image.open(io.BytesIO(clean)) as reopened:
                    self.assertNotIn(0x010F, reopened.getexif())

    def test_metadata_free_image_is_stored_unchanged(self):
        raw = self.image_bytes("PNG")
        clean, _, _, _ = blog_router._inspect_blog_image(raw)
        self.assertEqual(raw, clean)


if __name__ == "__main__":
    unittest.main()
