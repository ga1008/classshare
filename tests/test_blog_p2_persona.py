"""P2 of the blog closed loop: identity and avatar personas
(docs/blog-closed-loop-plan-2026-10-12.md §3, §9.3).

Real blog schema in an in-memory SQLite database; no network or AI.
"""
import random
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers import blog as blog_router
from classroom_app.services import blog_ai_service, blog_persona_service, blog_service
from tests.test_blog_p0_hardening import AUTHOR, CLASSMATE, OUTSIDER, TEACHER, BlogP0Fixture

AVATAR_HASH = "c" * 64


class PersonaFixture(BlogP0Fixture):
    def setUp(self):
        super().setUp()
        self.conn.execute("UPDATE students SET nickname='青山' WHERE id=21")
        self.conn.execute("UPDATE students SET avatar_file_hash=?, avatar_mime_type='image/png' WHERE id=22",
                          (AVATAR_HASH,))
        self.conn.commit()

    def comment(self, post_id, user=CLASSMATE, *, mode="real_name", content="评论", parent=None, notify=None):
        result = blog_service.add_comment(
            self.conn, user, post_id, content_md=content, author_display_mode=mode,
            parent_comment_id=parent, notify_callback=notify,
        )
        self.conn.commit()
        return int(result["id"])

    def comments(self, post_id, viewer=OUTSIDER):
        # OUTSIDER (another class) can read public posts.
        return {c["id"]: c for c in blog_service.list_comments(self.conn, viewer, post_id)["comments"]}

    def detail(self, post_id, viewer=OUTSIDER):
        return blog_service.get_post_detail(self.conn, viewer, post_id)


class PersonaResolveTests(unittest.TestCase):
    def resolve(self, **overrides):
        fields = dict(actor_identity="student:21", actor_role="student", actor_user_pk=21,
                      display_mode="real_name", snapshot_name="张三",
                      profiles={"student:21": {"name": "张三", "nickname": "青山", "avatar_hash": AVATAR_HASH}},
                      badge={"label": "筑基"})
        fields.update(overrides)
        return blog_persona_service.resolve(**fields)

    def test_real_exposes_identity_avatar_and_badge(self):
        persona = self.resolve()
        self.assertEqual(("real", "student:21", 21), (persona["kind"], persona["identity"], persona["user_pk"]))
        self.assertEqual(f"/api/blog/avatar/{AVATAR_HASH}", persona["avatar_url"])
        self.assertTrue(persona["can_follow"] and persona["can_message"])
        self.assertEqual("筑基", persona["cultivation_badge"]["label"])

    def test_pen_name_hides_everything_that_links_to_the_person(self):
        persona = self.resolve(display_mode="nickname")
        self.assertEqual(("pen", "青山"), (persona["kind"], persona["display_name"]))
        self.assertEqual(("", None, "", None), (persona["identity"], persona["user_pk"],
                                                persona["avatar_url"], persona["cultivation_badge"]))
        self.assertFalse(persona["can_message"])
        # P6: a pen name can be followed, but only through its own opaque ref.
        self.assertTrue(persona["can_follow"])
        real_ref = self.resolve()["ref"]
        self.assertTrue(persona["ref"] and persona["ref"] != real_ref)
        self.assertNotIn("student", persona["ref"])

    def test_teachers_cannot_hide_behind_a_face(self):
        persona = self.resolve(actor_identity="teacher:7", actor_role="teacher", actor_user_pk=7,
                               display_mode="anonymous", profiles={"teacher:7": {"name": "王老师"}})
        self.assertEqual(("real", "王老师"), (persona["kind"], persona["display_name"]))

    def test_anonymous_op_is_louzhu_inside_their_post(self):
        persona = self.resolve(display_mode="real_name", is_comment=True,
                               post_author_identity="student:21", post_display_mode="anonymous")
        self.assertEqual(("op_anon", "楼主", ""), (persona["kind"], persona["display_name"], persona["identity"]))


class AnonymousCodeTests(PersonaFixture):
    def test_code_is_stable_per_person_and_unique_within_post(self):
        post_id = self.post()
        first = self.comment(post_id, CLASSMATE, mode="anonymous", content="一")
        again = self.comment(post_id, CLASSMATE, mode="anonymous", content="二")
        other = self.comment(post_id, OUTSIDER, mode="anonymous", content="三")
        names = {cid: c["author"]["display_name"] for cid, c in self.comments(post_id, TEACHER).items()}
        self.assertTrue(names[first].startswith("匿名同学·"))
        self.assertEqual(names[first], names[again])
        self.assertNotEqual(names[first], names[other])

    def test_codes_never_repeat_even_when_words_run_out(self):
        post_id = self.post()
        rng = random.Random(1)
        taken = set()
        for word in blog_persona_service.ANON_CODE_WORDS:
            taken.add(word)
            self.conn.execute(
                "INSERT INTO blog_anonymous_codes(post_id,author_identity,code,created_at) VALUES(?,?,?,'2026')",
                (post_id, f"student:{1000 + len(taken)}", word),
            )
        code = blog_persona_service.assign_anonymous_code(self.conn, post_id, "student:22", rng=rng)
        self.assertNotIn(code, taken)

    def test_a_lost_race_retries_without_breaking_the_transaction(self):
        post_id = self.post()
        # Someone else claimed the first word drawn between our read and insert.
        draws = iter(["青柚", "远山"])

        def racing_pick(taken, chooser):
            word = next(draws)
            if word == "青柚":
                self.conn.execute(
                    "INSERT INTO blog_anonymous_codes(post_id,author_identity,code,created_at)"
                    " VALUES(?,'student:99','青柚','2026')", (post_id,),
                )
            return word

        with patch.object(blog_persona_service, "_pick_code", racing_pick):
            code = blog_persona_service.assign_anonymous_code(self.conn, post_id, "student:22")
        self.assertEqual("远山", code)
        # The transaction is still usable and both claims are kept.
        self.assertEqual(2, self.conn.execute(
            "SELECT COUNT(*) FROM blog_anonymous_codes WHERE post_id=?", (post_id,)).fetchone()[0])

    def test_deleting_comments_does_not_release_a_code(self):
        post_id = self.post()
        first = self.comment(post_id, CLASSMATE, mode="anonymous", content="一")
        name = self.comments(post_id)[first]["author"]["display_name"]
        blog_service.delete_comment(self.conn, CLASSMATE, first)
        self.conn.commit()
        again = self.comment(post_id, CLASSMATE, mode="anonymous", content="二")
        self.assertEqual(name, self.comments(post_id)[again]["author"]["display_name"])

    def test_anonymous_comment_row_keeps_traceability_but_json_does_not(self):
        post_id = self.post()
        comment_id = self.comment(post_id, CLASSMATE, mode="anonymous")
        row = self.conn.execute("SELECT * FROM blog_comments WHERE id=?", (comment_id,)).fetchone()
        self.assertEqual(("student:22", "anonymous"), (row["author_identity"], row["author_display_mode"]))
        self.assertTrue(row["author_display_name"].startswith("匿名同学·"))
        author = self.comments(post_id)[comment_id]["author"]
        self.assertEqual(("", None, ""), (author["identity"], author["user_pk"], author["avatar_url"]))
        self.assertNotIn("李四", str(author))


class CommentFaceTests(PersonaFixture):
    def test_post_author_can_close_anonymous_comments(self):
        result = blog_service.create_post(self.conn, AUTHOR, title="t", content_md="c",
                                          status="published", allow_anonymous_comments=False)
        post_id = int(result["id"])
        self.conn.execute("UPDATE blog_posts SET moderation_state='passed' WHERE id=?", (post_id,))
        self.conn.commit()
        self.assertFalse(self.detail(post_id)["allow_anonymous_comments"])
        with self.assertRaisesRegex(ValueError, "匿名评论"):
            self.comment(post_id, CLASSMATE, mode="anonymous")
        # A real-name comment still works.
        self.comment(post_id, CLASSMATE)

    def test_pen_name_comment_requires_a_pen_name(self):
        post_id = self.post()
        with self.assertRaisesRegex(ValueError, "笔名"):
            self.comment(post_id, CLASSMATE, mode="nickname")
        comment_id = self.comment(post_id, AUTHOR, mode="nickname")
        author = self.comments(post_id)[comment_id]["author"]
        self.assertEqual(("pen", "青山", ""), (author["kind"], author["display_name"], author["identity"]))

    def test_anonymous_post_author_always_replies_as_louzhu(self):
        post_id = self.post(mode="anonymous")
        comment_id = self.comment(post_id, AUTHOR, mode="real_name")
        stored = self.conn.execute("SELECT author_display_name FROM blog_comments WHERE id=?", (comment_id,)).fetchone()
        self.assertEqual("楼主", stored["author_display_name"])
        author = self.comments(post_id)[comment_id]["author"]
        self.assertEqual(("op_anon", "楼主", ""), (author["kind"], author["display_name"], author["identity"]))

    def test_non_real_commenters_are_not_contactable_in_notifications(self):
        post_id = self.post()
        calls = []
        self.comment(post_id, CLASSMATE, mode="anonymous", notify=lambda *args: calls.append(args))
        self.comment(post_id, CLASSMATE, mode="real_name", content="实名", notify=lambda *args: calls.append(args))
        (_, _, _, _, _, anon_role, anon_pk, anon_name, _), (_, _, _, _, _, real_role, real_pk, _, _) = calls
        self.assertEqual(("", None), (anon_role, anon_pk))
        self.assertTrue(anon_name.startswith("匿名同学·"))
        self.assertEqual(("student", 22), (real_role, real_pk))


class PostFaceTests(PersonaFixture):
    def test_pen_name_post_has_no_class_tag_badge_or_identity(self):
        post_id = self.post(mode="nickname")
        author = self.detail(post_id)["author"]
        self.assertEqual(("pen", "青山", "", None), (author["kind"], author["display_name"],
                                                     author["identity"], author["cultivation_badge"]))
        self.assertNotIn("软件一班", str(self.detail(post_id).get("system_tags") or []))

    def test_real_post_uses_content_addressed_avatar(self):
        post_id = self.post(CLASSMATE)
        self.assertEqual(f"/api/blog/avatar/{AVATAR_HASH}", self.detail(post_id)["author"]["avatar_url"])

    def test_author_filter_and_active_authors_only_see_real_name_posts(self):
        self.post(mode="nickname", title="笔名帖")
        self.post(mode="anonymous", title="匿名帖")
        self.post(title="实名帖")
        titles = {item["title"] for item in blog_service.list_posts(
            self.conn, OUTSIDER, author_identity="student:21")["posts"]}
        self.assertEqual({"实名帖"}, titles)
        own = {item["title"] for item in blog_service.list_posts(self.conn, AUTHOR, author_identity="student:21")["posts"]}
        self.assertEqual({"实名帖", "笔名帖", "匿名帖"}, own)
        authors = blog_service.get_blog_discovery(self.conn, OUTSIDER)["active_authors"]
        self.assertEqual(["张三"], [a["display_name"] for a in authors])


class AvatarRouteTests(PersonaFixture):
    def call(self, file_hash):
        @contextmanager
        def connection():
            yield self.conn
        with patch.object(blog_router, "get_db_connection", connection), \
                patch.object(blog_router, "get_custom_emoji_path", return_value=__file__):
            return blog_router.api_get_blog_avatar(file_hash, user=OUTSIDER)

    def test_serves_a_current_avatar_with_immutable_cache(self):
        response = self.call(AVATAR_HASH.upper())
        self.assertIn("immutable", response.headers["cache-control"])
        self.assertEqual("image/png", response.media_type)

    def test_rejects_hashes_that_are_not_someones_avatar(self):
        for bad in ("d" * 64, "../etc", "c" * 63, "g" * 64):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as caught:
                self.call(bad)
            self.assertEqual(404, caught.exception.status_code)


class HousekeeperShieldTests(PersonaFixture):
    def test_reply_text_never_names_the_person_behind_a_face(self):
        names = blog_ai_service._shielded_names(self.conn, "student:21")
        self.assertEqual({"张三", "青山"}, set(names))
        text = blog_ai_service._shield_reply("张三同学（青山）你好", names, "匿名同学·白鹭")
        self.assertNotIn("张三", text)
        self.assertNotIn("青山", text)

    def test_public_name_itself_is_kept(self):
        text = blog_ai_service._shield_reply("青山你好", ["青山", "张三"], "青山")
        self.assertEqual("青山你好", text)


class ReviewRegressionTests(PersonaFixture):
    """Leaks found in the P2 review: each one links a face back to a person."""

    def test_author_mark_only_joins_matching_faces(self):
        post_id = self.post()  # real-name post by 张三
        pen = self.comment(post_id, AUTHOR, mode="nickname", content="笔名")
        anon = self.comment(post_id, AUTHOR, mode="anonymous", content="匿名")
        real = self.comment(post_id, AUTHOR, content="实名")
        comments = self.comments(post_id)
        self.assertFalse(comments[pen]["author"]["is_op"])
        self.assertFalse(comments[anon]["author"]["is_op"])
        self.assertTrue(comments[real]["author"]["is_op"])

    def test_following_a_person_never_surfaces_their_pen_name_posts(self):
        from classroom_app.services import blog_community_service
        self.post(mode="nickname", title="笔名帖")
        self.post(title="实名帖")
        blog_community_service.set_follow(self.conn, OUTSIDER, target_type="author", target_key="student:21", following=True)
        self.conn.commit()
        feed = blog_community_service.list_following_posts(self.conn, OUTSIDER)
        self.assertEqual({"实名帖"}, {item["title"] for item in feed["posts"]})

    def test_legacy_class_tags_on_shielded_posts_are_not_searchable(self):
        post_id = self.post(mode="nickname", title="旧笔名帖")
        self.conn.execute("UPDATE blog_posts SET system_tags_json='[\"软件一班\"]' WHERE id=?", (post_id,))
        self.conn.commit()
        by_tag = blog_service.list_posts(self.conn, OUTSIDER, tag="软件一班")["posts"]
        by_query = blog_service.list_posts(self.conn, OUTSIDER, query="软件一班")["posts"]
        self.assertEqual([], [p["title"] for p in by_tag + by_query])
        hot = blog_service.get_blog_discovery(self.conn, OUTSIDER)["hot_tags"]
        self.assertNotIn("软件一班", [tag["name"] for tag in hot])

    def test_pen_name_cannot_pose_as_reserved_faces_or_real_people(self):
        for bad in ("楼主", "管家", "匿名同学·青柚", "匿名", "李四", "王老师"):
            with self.subTest(bad=bad):
                problem = blog_persona_service.pen_name_problem(self.conn, bad, self_role="student", self_pk=21)
                self.assertTrue(problem)
        self.assertEqual("", blog_persona_service.pen_name_problem(self.conn, "张三", self_role="student", self_pk=21))
        self.assertEqual("", blog_persona_service.pen_name_problem(self.conn, "青山", self_role="student", self_pk=21))
        self.conn.execute("UPDATE students SET nickname='李四' WHERE id=21")
        with self.assertRaisesRegex(ValueError, "真实姓名"):
            self.comment(self.post(), AUTHOR, mode="nickname")

    def test_profile_rejects_a_new_pen_name_that_impersonates(self):
        from classroom_app.services import profile_service
        with patch("classroom_app.services.signature_account_lock_service.lock_identity_accounts"),                 self.assertRaisesRegex(ValueError, "保留称呼"):
            profile_service.update_basic_profile(self.conn, AUTHOR, {"nickname": "楼主"})

    def test_backfill_masks_legacy_op_comments_and_shielded_tags(self):
        from classroom_app.db.schema_blog_closed_loop import backfill_blog_closed_loop_data
        post_id = self.post(mode="anonymous")
        comment_id = self.comment(post_id, CLASSMATE)
        self.conn.execute(
            "INSERT INTO blog_comments(post_id,author_identity,author_role,author_user_pk,author_display_name,"
            "content_md,status,created_at,updated_at,author_display_mode)"
            " VALUES(?, 'student:21', 'student', 21, '张三', '旧回复', 'active', '2026', '2026', 'real_name')",
            (post_id,),
        )
        self.conn.execute("UPDATE blog_posts SET system_tags_json='[\"软件一班\"]' WHERE id=?", (post_id,))
        report = backfill_blog_closed_loop_data(self.conn, apply=True)
        self.assertEqual((1, 1), (report["comments_anonymous_op"], report["posts_shielded_system_tags"]))
        legacy = self.conn.execute(
            "SELECT author_display_name, author_display_mode FROM blog_comments WHERE content_md='旧回复'").fetchone()
        self.assertEqual(("楼主", "anonymous"), tuple(legacy))
        other = self.conn.execute("SELECT author_display_name FROM blog_comments WHERE id=?", (comment_id,)).fetchone()
        self.assertEqual("李四", other[0])
        again = backfill_blog_closed_loop_data(self.conn, apply=False)
        self.assertEqual((0, 0), (again["comments_anonymous_op"], again["posts_shielded_system_tags"]))


if __name__ == "__main__":
    unittest.main()
