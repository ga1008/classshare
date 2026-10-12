"""P7: performance convergence (docs/blog-closed-loop-plan-2026-10-12.md §9.3).

Badge TTL cache, the comment-image read path through blog_comment_media,
write routes off the event loop, and Agent-pinned handlers left untouched.
"""
import asyncio
import inspect
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from classroom_app.db.schema_blog_closed_loop import insert_comment_media
from classroom_app.routers import blog as blog_router
from classroom_app.routers import blog_comments, blog_moderation, blog_studio, message_center
from classroom_app.services import blog_comment_service, blog_community_service, blog_opportunity_service, blog_service
from classroom_app.services import agent_platform_request_registry as registry
from tests.test_blog_p0_hardening import AUTHOR, CLASSMATE, BlogP0Fixture

IMAGE = "f" * 64


def _request(body: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "headers": [], "path": "/"}, receive)


class BadgeCacheTests(unittest.TestCase):
    def setUp(self):
        blog_service.clear_badge_cache()
        self.addCleanup(blog_service.clear_badge_cache)

    def test_a_badge_is_built_once_within_the_ttl(self):
        with patch.object(blog_service, "build_student_public_cultivation_badge",
                          return_value={"title": "筑基"}) as build:
            first = blog_service._load_public_cultivation_badge(None, 7)
            first["title"] = "mutated by a caller"
            second = blog_service._load_public_cultivation_badge(None, 7)
        self.assertEqual(1, build.call_count)
        self.assertEqual({"title": "筑基"}, second)  # callers get copies

    def test_no_badge_is_cached_too(self):
        with patch.object(blog_service, "build_student_public_cultivation_badge", return_value=None) as build:
            self.assertIsNone(blog_service._load_public_cultivation_badge(None, 8))
            self.assertIsNone(blog_service._load_public_cultivation_badge(None, 8))
        self.assertEqual(1, build.call_count)

    def test_failures_are_retried_not_cached(self):
        with patch.object(blog_service, "build_student_public_cultivation_badge",
                          side_effect=[RuntimeError("db"), {"title": "金丹"}]) as build:
            self.assertIsNone(blog_service._load_public_cultivation_badge(None, 9))
            self.assertEqual({"title": "金丹"}, blog_service._load_public_cultivation_badge(None, 9))
        self.assertEqual(2, build.call_count)

    def test_the_ttl_expires(self):
        clock = iter([0.0, 10_000.0, 10_000.0])  # store, expired lookup, re-store
        with patch.object(blog_service, "build_student_public_cultivation_badge", return_value=None) as build, \
                patch.object(blog_service.time, "monotonic", side_effect=lambda: next(clock)):
            blog_service._load_public_cultivation_badge(None, 10)
            blog_service._load_public_cultivation_badge(None, 10)
        self.assertEqual(2, build.call_count)


class CommentImageReadPathTests(BlogP0Fixture):
    def _comment_with_image(self, post_id):
        self.conn.execute(
            "INSERT INTO blog_comments(post_id,author_identity,author_role,author_user_pk,author_display_name,content_md,"
            "attachments_json,status,created_at,updated_at) VALUES(?, 'student:21','student',21,'张三','x', ?, "
            "'active','2026-10-13T08:00:00','2026-10-13T08:00:00')",
            (post_id, json.dumps([{"file_hash": IMAGE, "name": "a.png", "mime_type": "image/png"}])),
        )
        return self.conn.execute("SELECT MAX(id) FROM blog_comments").fetchone()[0]

    def test_a_comment_image_is_found_through_the_media_index(self):
        post_id = self.post()
        comment_id = self._comment_with_image(post_id)
        insert_comment_media(self.conn, comment_id, post_id, IMAGE, "attachment", "2026-10-13T08:00:00")
        asset = blog_service.get_media_asset_for_user(self.conn, CLASSMATE, IMAGE)
        self.assertEqual(("a.png", "image/png"), (asset["original_filename"], asset["mime_type"]))

    def test_comment_bodies_are_not_scanned_any_more(self):
        post_id = self.post()
        self._comment_with_image(post_id)  # no index row: nothing references the image
        self.assertIsNone(blog_service.get_media_asset_for_user(self.conn, CLASSMATE, IMAGE))

    def test_a_deleted_comment_no_longer_serves_its_image(self):
        post_id = self.post()
        comment_id = self._comment_with_image(post_id)
        insert_comment_media(self.conn, comment_id, post_id, IMAGE, "attachment", "2026-10-13T08:00:00")
        self.assertIsNotNone(blog_service.get_media_asset_for_user(self.conn, CLASSMATE, IMAGE))
        blog_comment_service.delete_comment(self.conn, AUTHOR, comment_id)
        self.assertIsNone(blog_service.get_media_asset_for_user(self.conn, CLASSMATE, IMAGE))


class SearchEscapingTests(BlogP0Fixture):
    def _tagged_post(self, tag):
        post_id = blog_service.create_post(self.conn, AUTHOR, title=f"带 {tag}", content_md="正文", tags=[tag])["id"]
        self.conn.execute("UPDATE blog_posts SET moderation_state='passed' WHERE id=?", (post_id,))
        return int(post_id)

    def test_opportunity_search_is_case_insensitive_and_literal(self):
        post_id = self.post(title="校园招聘")
        self.conn.execute("UPDATE blog_posts SET section_key='career' WHERE id=?", (post_id,))
        blog_opportunity_service.upsert_opportunity_for_post(
            self.conn, post_id, {"employer_name": "ACMEXTech", "positions_text": "开发"},
            source_url="https://example.com/jobs/1",
        )

        def found(query):
            return blog_opportunity_service.list_opportunities(self.conn, CLASSMATE, query=query)["total"]

        self.assertEqual(1, found("acmextech"))
        self.assertEqual(0, found("acme_tech"))  # "_" is a literal underscore
        self.assertEqual(0, found("acme%"))

    def test_a_followed_tag_matches_only_itself(self):
        literal = self._tagged_post("a_b")
        self._tagged_post("axb")
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="tag", target_key="a_b")
        posts = blog_community_service.list_following_posts(self.conn, CLASSMATE)["posts"]
        self.assertEqual([literal], [int(post["id"]) for post in posts])


class EventLoopTests(unittest.TestCase):
    SYNC_HANDLERS = (
        (blog_router, "blog_page"), (blog_router, "api_create_blog_section"), (blog_router, "api_update_blog_section"),
        (blog_router, "api_update_blog_reading_progress"), (blog_router, "api_set_opportunity_state"),
        (blog_router, "api_create_post"), (blog_router, "api_add_comment"), (blog_router, "api_get_blog_image"),
        (blog_comments, "api_edit_comment"), (blog_comments, "api_soft_delete_comment"),
        (blog_moderation, "api_create_appeal"), (blog_moderation, "api_moderate"),
        (blog_moderation, "api_review_reports"), (blog_moderation, "api_review_appeal"),
        (blog_moderation, "api_review_reveal"), (blog_moderation, "api_review_set_moderator"),
        (blog_studio, "api_studio_profile"),
        (message_center, "api_message_center_read_by_ref"), (message_center, "api_set_message_center_preference"),
    )

    def test_blog_write_handlers_run_in_the_threadpool(self):
        for module, name in self.SYNC_HANDLERS:
            with self.subTest(handler=name):
                self.assertFalse(inspect.iscoroutinefunction(getattr(module, name)))

    def test_agent_pinned_blog_handlers_keep_their_reviewed_source(self):
        from classroom_app.app import app

        pinned = [op for op in registry.CAPABILITIES if op.module in {"blog", "message_center"}]
        self.assertGreaterEqual(len(pinned), 11)
        for operation in pinned:
            with self.subTest(handler=operation.handler):
                self.assertEqual(operation, registry.resolve_capability(app, operation.key)[0])

    def test_the_hide_compat_route_is_gone(self):
        self.assertFalse(hasattr(blog_router, "api_hide_post"))
        paths = {getattr(route, "path", "") for route in blog_router.router.routes}
        self.assertNotIn("/api/blog/posts/{post_id}/hide", paths)

    def test_json_body_rejects_invalid_or_non_object_bodies(self):
        self.assertEqual({"a": 1}, asyncio.run(blog_router._json_body(_request(b'{"a": 1}'))))
        for body in (b"not json", b"[1, 2]"):
            with self.subTest(body=body), self.assertRaises(HTTPException) as caught:
                asyncio.run(blog_router._json_body(_request(body)))
            self.assertEqual(400, caught.exception.status_code)


if __name__ == "__main__":
    unittest.main()
