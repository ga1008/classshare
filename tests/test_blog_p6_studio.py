"""P6: creator studio, author faces and pages (docs/blog-closed-loop-plan-2026-10-12.md §9.4, §10.5).

Real SQLite through the P0 fixture: daily stats rollup per face, overview,
opaque refs, pen-name follows, author pages, bios, interactions and works.
"""
import json
import unittest
from datetime import date, datetime, timedelta

from classroom_app.services import (
    blog_author_stats,
    blog_community_service,
    blog_moderation_service,
    blog_notifications,
    blog_persona_service,
    blog_service,
    blog_studio_service,
)
from tests.test_blog_p0_hardening import AUTHOR, CLASSMATE, OUTSIDER, TEACHER, BlogP0Fixture

PEN = "青山"


def comment(conn, user, post_id, text, *, parent=None, mode="real_name"):
    return blog_service.add_comment(
        conn, user, post_id, content_md=text, parent_comment_id=parent, author_display_mode=mode,
        notify_callback=blog_notifications.notify_new_comment,
    )["id"]


class P6Fixture(BlogP0Fixture):
    def setUp(self):
        super().setUp()
        blog_community_service._FOLLOWING_COUNT_CACHE.clear()
        blog_studio_service._LAST_REF_REFRESH["at"] = 0.0  # the ref refresh throttle is per process
        self.conn.execute("UPDATE students SET nickname = ? WHERE id = 21", (PEN,))
        self.conn.commit()

    def view(self, post_id, viewer, when, *, completed=0):
        self.conn.execute(
            "INSERT INTO blog_post_views (post_id, viewer_identity, view_bucket, first_viewed_at, last_viewed_at, "
            "completed) VALUES (?, ?, ?, ?, ?, ?)",
            (post_id, viewer, when[:13], when, when, completed),
        )

    def pen_ref(self):
        return blog_persona_service.face_ref("student:21", "nickname")

    def real_ref(self):
        return blog_persona_service.face_ref("student:21", "real_name")


class StatsTests(P6Fixture):
    def test_rollup_counts_each_face_separately_and_skips_the_author(self):
        real_post = self.post()
        pen_post = self.post(title="笔名帖", mode="nickname")
        day = date.today() - timedelta(days=1)
        at = f"{day.isoformat()}T10:00:00"
        self.view(real_post, "student:22", at, completed=1)
        self.view(real_post, "student:23", at)
        self.view(real_post, "student:21", at)  # the author's own visit
        self.view(pen_post, "student:22", at)
        self.conn.execute(
            "INSERT INTO blog_likes (target_type, target_id, user_identity, user_role, user_pk, created_at) "
            "VALUES ('post', ?, 'student:22', 'student', 22, ?)", (real_post, at))
        self.assertEqual(2, blog_author_stats.rollup_day(self.conn, day))
        rows = {r["display_mode"]: dict(r) for r in self.conn.execute(
            "SELECT * FROM blog_author_daily_stats WHERE author_identity = 'student:21'").fetchall()}
        self.assertEqual((2, 2, 1, 1), (rows["real_name"]["views"], rows["real_name"]["readers"],
                                        rows["real_name"]["completed_reads"], rows["real_name"]["likes"]))
        self.assertEqual(1, rows["nickname"]["views"])
        # Idempotent: a re-run overwrites, never doubles.
        blog_author_stats.rollup_day(self.conn, day)
        self.assertEqual(2, self.conn.execute(
            "SELECT views FROM blog_author_daily_stats WHERE display_mode='real_name'").fetchone()[0])

    def test_run_rollup_covers_the_backfill_window(self):
        post_id = self.post()
        self.view(post_id, "student:22", "2026-10-01T09:00:00")
        self.view(post_id, "student:23", "2026-10-11T09:00:00")
        result = blog_author_stats.run_rollup(self.conn, today=date(2026, 10, 12))
        self.assertEqual(blog_author_stats.BACKFILL_DAYS, result["days"])
        stored = [r["stat_date"] for r in self.conn.execute(
            "SELECT stat_date FROM blog_author_daily_stats ORDER BY stat_date").fetchall()]
        self.assertEqual(["2026-10-01", "2026-10-11"], stored)
        blog_author_stats.run_rollup(self.conn, today=date(2026, 10, 12))
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM blog_author_daily_stats").fetchone()[0])

    def test_overview_includes_today_live_and_unknown_completion(self):
        post_id = self.post()
        overview = blog_studio_service.overview(self.conn, AUTHOR)
        self.assertIsNone(overview["stats"]["completion_rate"])
        self.view(post_id, "student:22", datetime.now().isoformat(timespec="seconds"), completed=1)
        overview = blog_studio_service.overview(self.conn, AUTHOR)
        self.assertEqual(1, overview["stats"]["views_7d"])
        self.assertEqual(100, overview["stats"]["completion_rate"])
        self.assertEqual(30, len(overview["stats"]["trend"]))
        self.assertEqual(["real_name"], [face["mode"] for face in overview["faces"]])

    def test_the_author_reading_their_own_post_is_not_a_view(self):
        post_id = self.post()
        blog_service.get_post_detail(self.conn, AUTHOR, post_id)
        blog_service.update_post_reading_progress(self.conn, AUTHOR, post_id, max_scroll_ratio=1)
        self.assertEqual(0, self.row(post_id)["view_count"])
        blog_service.get_post_detail(self.conn, CLASSMATE, post_id)
        self.assertEqual(1, self.row(post_id)["view_count"])


class FaceAndFollowTests(P6Fixture):
    def test_pen_name_follow_uses_only_the_ref(self):
        pen_post = self.post(title="笔名帖", mode="nickname")
        self.post(title="实名帖")
        result = blog_community_service.set_follow(self.conn, CLASSMATE, target_type="pen", target_key=self.pen_ref())
        self.assertEqual(("pen", self.pen_ref(), True),
                         (result["target_type"], result["target_key"], result["following"]))
        listed = blog_community_service.list_follows(self.conn, CLASSMATE)
        self.assertNotIn("student:21", json.dumps(listed))
        feed = blog_community_service.list_following_posts(self.conn, CLASSMATE)
        self.assertEqual([pen_post], [post["id"] for post in feed["posts"]])
        title = self.conn.execute(
            "SELECT title FROM message_center_notifications WHERE recipient_user_pk = 21 AND category = 'blog_follow'"
        ).fetchone()[0]
        self.assertEqual("1 人关注了你的笔名", title)

    def test_real_and_pen_followers_are_counted_apart(self):
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key=self.real_ref())
        blog_community_service.set_follow(self.conn, OUTSIDER, target_type="pen", target_key=self.pen_ref())
        fans = blog_studio_service.followers(self.conn, AUTHOR)
        self.assertEqual((["李四"], ["王五"]), ([f["name"] for f in fans["real"]], [f["name"] for f in fans["pen"]]))
        blog_community_service.set_follow(self.conn, AUTHOR, target_type="author", target_key="student:22")
        self.assertTrue(blog_studio_service.followers(self.conn, AUTHOR)["real"][0]["follows_back"])

    def test_bad_refs_and_self_follows_are_refused(self):
        with self.assertRaises(ValueError):
            blog_community_service.set_follow(self.conn, CLASSMATE, target_type="pen", target_key="0" * 20)
        with self.assertRaises(ValueError):
            blog_community_service.set_follow(self.conn, CLASSMATE, target_type="pen", target_key="student:21")
        with self.assertRaises(ValueError):
            blog_community_service.set_follow(self.conn, AUTHOR, target_type="pen", target_key=self.pen_ref())
        # The legacy identity form for real-name follows keeps working.
        result = blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key="student:21")
        self.assertTrue(result["following"])

    def test_pen_author_page_shows_only_pen_posts_and_no_identity(self):
        pen_post = self.post(title="笔名帖", mode="nickname")
        self.post(title="实名帖")
        blog_studio_service.set_bios(self.conn, AUTHOR, real_bio="实名简介", pen_bio="笔名简介")
        page = blog_studio_service.author_page(self.conn, CLASSMATE, self.pen_ref())
        self.assertEqual([pen_post], [post["id"] for post in page["posts"]])
        self.assertEqual(("笔名", PEN, "笔名简介"), (page["face"], page["author"]["display_name"], page["bio"]))
        rendered = json.dumps(page, ensure_ascii=False)
        for leak in ("student:21", "张三", "实名简介"):
            self.assertNotIn(leak, rendered)
        real = blog_studio_service.author_page(self.conn, CLASSMATE, self.real_ref())
        self.assertEqual(["实名帖"], [post["title"] for post in real["posts"]])

    def test_cleared_pen_name_has_no_page(self):
        self.conn.execute("UPDATE students SET nickname = '' WHERE id = 21")
        with self.assertRaises(ValueError):
            blog_studio_service.author_page(self.conn, CLASSMATE, self.pen_ref())

    def test_bios_are_short_checked_and_teachers_have_no_pen_bio(self):
        bios = blog_studio_service.set_bios(self.conn, AUTHOR, real_bio="长" * 100)
        self.assertEqual(60, len(bios["real_bio"]))
        with self.assertRaises(ValueError):
            blog_studio_service.set_bios(self.conn, AUTHOR, pen_bio="加我 13812345678")
        teacher = blog_studio_service.set_bios(self.conn, TEACHER, pen_bio="不该保存")
        self.assertEqual("", teacher["pen_bio"])


class InteractionAndWorksTests(P6Fixture):
    def test_received_and_sent_comments(self):
        mine = self.post()
        theirs = self.post(user=CLASSMATE, title="李四的帖")
        on_mine = comment(self.conn, CLASSMATE, mine, "在你帖子下")
        my_comment = comment(self.conn, AUTHOR, theirs, "我去评论", mode="anonymous")
        comment(self.conn, CLASSMATE, theirs, "回复你的匿名评论", parent=my_comment)
        comment(self.conn, AUTHOR, mine, "我自己的楼")
        received = blog_studio_service.received_comments(self.conn, AUTHOR)
        self.assertEqual({"在你帖子下", "回复你的匿名评论"}, {item["content_md"] for item in received["items"]})
        self.assertIn(on_mine, [item["id"] for item in received["items"]])
        sent = blog_studio_service.sent_comments(self.conn, AUTHOR)
        anon = next(item for item in sent["items"] if item["id"] == my_comment)
        self.assertEqual("anonymous", anon["face"]["mode"])
        self.assertTrue(anon["face"]["name"].startswith("匿名同学·"))

    def test_sent_comments_show_why_one_was_handled(self):
        post_id = self.post(user=CLASSMATE)
        mine = comment(self.conn, AUTHOR, post_id, "有问题的评论")
        blog_moderation_service.moderate(self.conn, TEACHER, "comment", mine, action="remove", reason_code="spam",
                                         note="广告")
        item = blog_studio_service.sent_comments(self.conn, AUTHOR)["items"][0]
        self.assertEqual(("removed", "广告"), (item["moderation"]["state"], item["moderation"]["note"]))
        self.assertTrue(item["moderation"]["reason_label"])

    def test_unreplied_comments_drop_once_answered(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "请教")
        self.assertEqual(1, blog_studio_service.overview(self.conn, AUTHOR)["todo"]["unreplied_comments"])
        comment(self.conn, AUTHOR, post_id, "回答", parent=floor)
        self.assertEqual(0, blog_studio_service.overview(self.conn, AUTHOR)["todo"]["unreplied_comments"])

    def test_works_list_sorts_filters_and_explains_sanctions(self):
        quiet = self.post(title="冷门")
        popular = self.post(title="热门")
        self.conn.execute("UPDATE blog_posts SET view_count = 50 WHERE id = ?", (popular,))
        blog_moderation_service.moderate(self.conn, TEACHER, "post", quiet, action="limit", reason_code="spam",
                                         note="疑似广告")
        by_views = blog_service.get_my_posts(self.conn, AUTHOR, sort="views")
        self.assertEqual(popular, by_views["posts"][0]["id"])
        attention = blog_service.get_my_posts(self.conn, AUTHOR, status_filter="attention")
        self.assertEqual([quiet], [post["id"] for post in attention["posts"]])
        moderation = attention["posts"][0]["moderation"]
        self.assertEqual(("limited", "疑似广告"), (moderation["state"], moderation["note"]))
        self.assertTrue(moderation["appeal"]["can_appeal"])


class ReviewRegressionTests(P6Fixture):
    """Findings of the P6 code and security reviews."""

    def test_refs_survive_a_key_rotation(self):
        self.conn.execute(
            "INSERT INTO blog_author_refs (ref, identity, display_mode, created_at) "
            "VALUES (?, 'student:21', 'nickname', '2026')", ("f" * 20,))
        self.assertEqual(("student:21", "nickname"), blog_studio_service.resolve_ref(self.conn, self.pen_ref()))
        self.assertIsNone(blog_studio_service.resolve_ref(self.conn, "f" * 20))

    def test_unknown_refs_rescan_at_most_once_a_minute(self):
        self.assertIsNone(blog_studio_service.resolve_ref(self.conn, "0" * 20))
        self.conn.execute(
            "INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) "
            "VALUES(24,'2024004','赵六',30,'active')")
        newcomer = blog_persona_service.face_ref("student:24", "real_name")
        self.assertIsNone(blog_studio_service.resolve_ref(self.conn, newcomer))  # throttled
        blog_studio_service._LAST_REF_REFRESH["at"] = 0.0
        self.assertEqual(("student:24", "real_name"), blog_studio_service.resolve_ref(self.conn, newcomer))

    def test_teachers_have_no_pen_face(self):
        teacher_pen = blog_persona_service.face_ref("teacher:7", "nickname")
        self.assertIsNone(blog_studio_service.resolve_ref(self.conn, teacher_pen))
        with self.assertRaises(ValueError):
            blog_community_service.set_follow(self.conn, CLASSMATE, target_type="pen", target_key=teacher_pen)

    def test_a_cleared_pen_name_cannot_be_followed_or_linked(self):
        self.post(title="笔名帖", mode="nickname")
        self.conn.execute("UPDATE students SET nickname = '' WHERE id = 21")
        with self.assertRaises(ValueError):
            blog_community_service.set_follow(self.conn, CLASSMATE, target_type="pen", target_key=self.pen_ref())
        faces = {face["mode"]: face["ref"] for face in blog_studio_service.overview(self.conn, AUTHOR)["faces"]}
        self.assertEqual("", faces["nickname"])

    def test_yesterday_is_live_until_the_rollup_stored_it(self):
        post_id = self.post()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.view(post_id, "student:22", f"{yesterday}T08:00:00")
        trend = blog_studio_service.overview(self.conn, AUTHOR)["stats"]["trend"]
        self.assertEqual(1, trend[-2]["views"])

    def test_new_follows_carry_a_local_iso_time_and_count_that_day(self):
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key=self.real_ref())
        created = self.conn.execute("SELECT created_at FROM blog_follows").fetchone()[0]
        self.assertEqual(date.today().isoformat() + "T", created[:11])
        blog_author_stats.rollup_day(self.conn, date.today())
        self.assertEqual(1, self.conn.execute(
            "SELECT followers_delta FROM blog_author_daily_stats WHERE display_mode = 'real_name'").fetchone()[0])

    def test_follower_lists_report_true_totals(self):
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key=self.real_ref())
        fans = blog_studio_service.followers(self.conn, AUTHOR)
        self.assertEqual((1, 0), (fans["real_total"], fans["pen_total"]))

    def test_profile_card_never_breaks_the_profile_page(self):
        from unittest import mock

        from classroom_app.services import profile_service

        with mock.patch.object(blog_studio_service, "creator_summary", side_effect=RuntimeError("boom")):
            self.assertIsNone(profile_service._load_creator_summary(self.conn, AUTHOR))
        self.assertIsNone(profile_service._load_creator_summary(self.conn, {"id": 1, "role": "admin"}))
        self.assertEqual(0, profile_service._load_creator_summary(self.conn, AUTHOR)["published"])


if __name__ == "__main__":
    unittest.main()
