"""P5: blog notification loop (docs/blog-closed-loop-plan-2026-10-12.md §6, §7.5, §10.4).

Real SQLite through the same fixture as P0–P4: event catalog, aggregation,
mute switches, read-time persona, read-by-post, stale targets, mentions,
follows, following unread and the inline-reply context.
"""
import json
import unittest
import unittest.mock

from classroom_app.services import (
    blog_comment_service,
    blog_community_service,
    blog_moderation_service,
    blog_notifications,
    blog_service,
    message_center_service,
    notification_delivery,
)
from tests.test_blog_p0_hardening import AUTHOR, CLASSMATE, OTHER_TEACHER, OUTSIDER, TEACHER, BlogP0Fixture


def comment(conn, user, post_id, text, *, parent=None, mode="real_name"):
    return blog_service.add_comment(
        conn, user, post_id, content_md=text, parent_comment_id=parent, author_display_mode=mode,
        notify_callback=blog_notifications.notify_new_comment,
    )["id"]


class P5Fixture(BlogP0Fixture):
    def setUp(self):
        super().setUp()
        # The following count is cached per identity across requests.
        blog_community_service._FOLLOWING_COUNT_CACHE.clear()

    def rows(self, user, *, category=None):
        sql = "SELECT * FROM message_center_notifications WHERE recipient_role = ? AND recipient_user_pk = ?"
        params = [user["role"], user["id"]]
        if category:
            sql += " AND category = ?"
            params.append(category)
        return [dict(row) for row in self.conn.execute(sql + " ORDER BY id", params).fetchall()]

    def items(self, user):
        return message_center_service.list_message_center_items(self.conn, user, include_private=False)

    def events(self, user):
        return [json.loads(row["metadata_json"] or "{}").get("event") for row in self.rows(user)]


class CommentEventTests(P5Fixture):
    def test_comment_reply_and_thread_reply_reach_the_right_people(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "第一楼")
        self.assertEqual(["blog.comment"], self.events(AUTHOR))
        comment(self.conn, OUTSIDER, post_id, "回复李四", parent=floor)
        comment(self.conn, TEACHER, post_id, "老师也回复", parent=floor)
        self.assertEqual(["blog.reply", "blog.reply"], self.events(CLASSMATE))
        # The post author hears about replies in floors as one rolling card.
        threads = [row for row in self.rows(AUTHOR) if row["aggregate_key"]]
        self.assertEqual(1, len(threads))
        self.assertEqual(2, threads[0]["aggregate_count"])
        card = next(item for item in self.items(AUTHOR) if item["blog"]["event"] == "blog.thread_reply")
        self.assertEqual("你的帖子下有 2 条新回复", card["title"])
        self.assertEqual({"王五", "王老师"}, {face["name"] for face in card["blog"]["avatar_stack"]})
        self.assertEqual(post_id, self.rows(AUTHOR)[0]["ref_post_id"])

    def test_nobody_is_notified_about_their_own_actions(self):
        post_id = self.post()
        floor = comment(self.conn, AUTHOR, post_id, "我自己补充")
        comment(self.conn, AUTHOR, post_id, "再补充", parent=floor)
        blog_service.toggle_like(self.conn, AUTHOR, "post", post_id)
        blog_service.toggle_like(self.conn, AUTHOR, "comment", floor)
        self.assertEqual([], self.rows(AUTHOR))

    def test_reply_title_follows_the_current_persona(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "请教一下")
        comment(self.conn, AUTHOR, post_id, "我来答", parent=floor)
        card = next(item for item in self.items(CLASSMATE) if item["blog"]["event"] == "blog.reply")
        self.assertEqual("张三 回复了你的评论", card["title"])
        self.assertEqual("student:21", card["actor_identity"])
        # The author later makes the post anonymous: the old card follows.
        blog_service.update_post(self.conn, AUTHOR, post_id, author_display_mode="anonymous")
        card = next(item for item in self.items(CLASSMATE) if item["blog"]["event"] == "blog.reply")
        self.assertEqual("楼主 回复了你的评论", card["title"])
        self.assertEqual("", card["actor_identity"])
        self.assertIsNone(card["actor_user_pk"])
        self.assertNotIn("张三", json.dumps(card, ensure_ascii=False))

    def test_anonymous_commenter_is_never_a_contactable_actor(self):
        post_id = self.post()
        comment(self.conn, CLASSMATE, post_id, "匿名说一句", mode="anonymous")
        card = self.items(AUTHOR)[0]
        self.assertTrue(card["title"].startswith("匿名同学·"))
        self.assertEqual("", card["actor_identity"])
        self.assertNotIn("李四", json.dumps(card, ensure_ascii=False))
        self.assertTrue(card["blog"]["can_reply"])


class LikeAndPreferenceTests(P5Fixture):
    def test_likes_aggregate_per_day_and_count_each_person_once(self):
        post_id = self.post()
        for user in (CLASSMATE, OUTSIDER, TEACHER, OTHER_TEACHER):
            blog_service.toggle_like(self.conn, user, "post", post_id)
        # Unlike and like again must not inflate the count.
        blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        rows = self.rows(AUTHOR, category="blog_like")
        self.assertEqual(1, len(rows))
        self.assertEqual(4, rows[0]["aggregate_count"])
        card = self.items(AUTHOR)[0]
        self.assertEqual("4 人赞了你的帖子", card["title"])
        self.assertNotIn("aggregate_members", card["metadata"])
        for name in ("李四", "王五", "王老师", "李老师"):
            self.assertNotIn(name, json.dumps(card, ensure_ascii=False))
        # Once read, a new person's like starts a new unread card...
        message_center_service.mark_message_center_items_read(self.conn, AUTHOR, notification_ids=[rows[0]["id"]])
        self.conn.execute(
            "INSERT INTO students(id,student_id_number,name,class_id,enrollment_status) "
            "VALUES(24,'2024004','赵六',30,'active')"
        )
        blog_service.toggle_like(self.conn, {"id": 24, "role": "student", "name": "赵六"}, "post", post_id)
        # ...while someone already counted today cannot re-notify by unliking and liking.
        blog_service.toggle_like(self.conn, OUTSIDER, "post", post_id)
        blog_service.toggle_like(self.conn, OUTSIDER, "post", post_id)
        rows = self.rows(AUTHOR, category="blog_like")
        self.assertEqual(2, len(rows))
        self.assertIsNone(rows[1]["read_at"])
        self.assertEqual(1, rows[1]["aggregate_count"])

    def test_comment_likes_go_to_the_commenter(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "好评")
        blog_service.toggle_like(self.conn, AUTHOR, "comment", floor)
        card = self.items(CLASSMATE)[0]
        self.assertEqual(("blog_like", "1 人赞了你的评论"), (card["category"], card["title"]))
        self.assertIn(f"comment={floor}", card["link_url"])

    def test_muted_events_are_not_written_and_forced_ones_cannot_be_muted(self):
        post_id = self.post()
        blog_notifications.set_preference(self.conn, "student:21", "blog.like_post", False)
        blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        self.assertEqual([], self.rows(AUTHOR))
        with self.assertRaises(ValueError):
            blog_notifications.set_preference(self.conn, "student:21", "blog.moderation", False)
        with self.assertRaises(ValueError):
            blog_notifications.set_preference(self.conn, "student:21", "blog.unknown", False)
        view = {item["key"]: item for item in blog_notifications.preferences_view(self.conn, "student:21")}
        self.assertFalse(view["blog.like_post"]["enabled"])
        self.assertTrue(view["blog.moderation"]["forced"] and view["blog.moderation"]["enabled"])

    def test_seen_markers_never_show_up_as_switches(self):
        notification_delivery.mark_seen(self.conn, "student:21", "blog.following")
        self.assertEqual({}, notification_delivery.load_preferences(self.conn, "student:21"))
        with self.assertRaises(ValueError):
            notification_delivery.set_event_enabled(self.conn, "student:21", "seen:blog.following", False)


class ReadAndGoneTests(P5Fixture):
    def test_opening_the_post_reads_every_card_about_it(self):
        post_id = self.post()
        other_post = self.post(title="另一篇")
        comment(self.conn, CLASSMATE, post_id, "一")
        blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        comment(self.conn, CLASSMATE, other_post, "二")
        self.assertEqual(2, blog_notifications.mark_post_read(self.conn, AUTHOR, post_id))
        unread = [row for row in self.rows(AUTHOR) if not row["read_at"]]
        self.assertEqual([other_post], [row["ref_post_id"] for row in unread])

    def test_deleted_comment_cards_say_the_content_is_gone(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "要删掉的")
        blog_service.toggle_like(self.conn, AUTHOR, "comment", floor)
        blog_comment_service.delete_comment(self.conn, CLASSMATE, floor)
        card = self.items(AUTHOR)[0]
        self.assertTrue(card["blog"]["gone"])
        self.assertFalse(card["blog"]["can_reply"])
        self.assertEqual("", card["link_url"])
        like_card = self.items(CLASSMATE)[0]
        self.assertTrue(like_card["blog"]["gone"])

    def test_limited_post_is_gone_for_others_but_not_for_its_author(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "评论")
        comment(self.conn, AUTHOR, post_id, "回复", parent=floor)
        blog_moderation_service.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        self.assertTrue(self.items(CLASSMATE)[0]["blog"]["gone"])
        author_cards = [item for item in self.items(AUTHOR) if item["category"] != "blog_moderation"]
        self.assertFalse(any(item["blog"]["gone"] for item in author_cards))
        blog_moderation_service.moderate(self.conn, TEACHER, "post", post_id, action="restore", note="已核实")
        self.assertFalse(self.items(CLASSMATE)[0]["blog"]["gone"])

    def test_deleted_post_is_gone_for_everyone(self):
        post_id = self.post()
        comment(self.conn, CLASSMATE, post_id, "评论")
        blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        floor = comment(self.conn, AUTHOR, post_id, "作者楼")
        comment(self.conn, CLASSMATE, post_id, "回复作者", parent=floor)
        version = self.row(post_id)["updated_at"]
        blog_service.delete_post(self.conn, AUTHOR, post_id, expected_updated_at=version)
        self.assertTrue(all(item["blog"]["gone"] for item in self.items(AUTHOR)))

    def test_open_redirect_of_a_gone_card_stays_in_the_message_center(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "评论")
        blog_comment_service.delete_comment(self.conn, CLASSMATE, floor)
        notification_id = self.rows(AUTHOR)[0]["id"]
        opened = message_center_service.open_message_center_notification(self.conn, AUTHOR, notification_id)
        self.assertEqual("", opened["link_url"])


class MentionTests(P5Fixture):
    def test_mentions_reach_thread_participants_once(self):
        post_id = self.post()
        comment(self.conn, OUTSIDER, post_id, "我是王五")
        comment(self.conn, CLASSMATE, post_id, "@王五 你怎么看？@张三")
        mention = [row for row in self.rows(OUTSIDER) if json.loads(row["metadata_json"])["event"] == "blog.mention"]
        self.assertEqual(1, len(mention))
        self.assertEqual("李四 在评论中提到了你", self.items(OUTSIDER)[0]["title"])
        # 张三 already got "评论了你的帖子" for this comment: no second card.
        self.assertEqual(["blog.comment", "blog.comment"], self.events(AUTHOR))

    def test_anonymous_voices_cannot_be_mentioned(self):
        post_id = self.post()
        anon = comment(self.conn, CLASSMATE, post_id, "匿名发言", mode="anonymous")
        code = self.conn.execute("SELECT author_display_name FROM blog_comments WHERE id=?", (anon,)).fetchone()[0]
        comment(self.conn, OUTSIDER, post_id, f"@{code} @李四 你是谁")
        self.assertEqual([], self.rows(CLASSMATE))

    def test_post_mentions_respect_visibility(self):
        blog_community_service.set_follow(self.conn, AUTHOR, target_type="author", target_key="student:23")
        blog_community_service.set_follow(self.conn, AUTHOR, target_type="author", target_key="teacher:7")
        post_id = self.post(content="@王五 @王老师 来看看")
        self.conn.execute("UPDATE blog_posts SET visibility='class', visible_class_id=30 WHERE id=?", (post_id,))
        self.assertEqual(1, blog_notifications.notify_post_mentions(self.conn, post_id))
        self.assertIn("blog.mention", self.events(TEACHER))
        self.assertNotIn("blog.mention", self.events(OUTSIDER))
        # Sent once per post, however often it is re-published.
        self.assertEqual(0, blog_notifications.notify_post_mentions(self.conn, post_id))

    def test_find_mentions_prefers_the_longest_name(self):
        found = blog_notifications.find_mentions("@王五一你好 @王五", {"王五": "student:23", "王五一": "student:24"})
        self.assertEqual(["student:24", "student:23"], found)


class FollowTests(P5Fixture):
    def test_follow_cards_aggregate_and_name_real_followers(self):
        for user in (CLASSMATE, OUTSIDER):
            blog_community_service.set_follow(self.conn, user, target_type="author", target_key="student:21")
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key="student:21",
                                          following=False)
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key="student:21")
        rows = self.rows(AUTHOR, category="blog_follow")
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["aggregate_count"])
        card = self.items(AUTHOR)[0]
        self.assertEqual("2 人关注了你", card["title"])
        self.assertEqual({"李四", "王五"}, {face["name"] for face in card["blog"]["avatar_stack"]})

    def test_following_unread_counts_new_posts_until_seen(self):
        blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key="student:21")
        self.post(title="新帖")
        self.assertEqual(1, blog_community_service.count_following_unread(self.conn, CLASSMATE))
        blog_community_service.mark_following_seen(self.conn, CLASSMATE)
        self.assertEqual(0, blog_community_service.count_following_unread(self.conn, CLASSMATE))
        self.assertEqual(0, blog_community_service.count_following_unread(self.conn, OUTSIDER))


class SystemEventTests(P5Fixture):
    def test_featured_is_from_the_blog_center_and_sent_once(self):
        post_id = self.post()
        for _ in range(3):
            blog_service.feature_post(self.conn, TEACHER, post_id, notify_callback=blog_notifications.notify_post_featured)
        rows = [row for row in self.rows(AUTHOR) if row["ref_type"] == "blog_featured"]
        self.assertEqual(1, len(rows))
        self.assertEqual("博客中心", rows[0]["actor_display_name"])
        self.assertEqual("", rows[0]["actor_identity"])

    def test_report_result_has_no_link_once_content_was_handled(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "有问题的评论")
        blog_community_service.create_report(self.conn, OUTSIDER, target_type="comment", target_id=floor,
                                             reason_code="spam", details="")
        blog_moderation_service.moderate(self.conn, TEACHER, "comment", floor, action="remove", reason_code="spam")
        report = [row for row in self.rows(OUTSIDER) if json.loads(row["metadata_json"])["event"] == "blog.report_result"]
        self.assertEqual(1, len(report))
        self.assertEqual("", report[0]["link_url"])

    def test_new_categories_are_visible_tabs(self):
        for role in ("student", "teacher"):
            visible = message_center_service.get_visible_categories(role)
            self.assertIn("blog_like", visible)
            self.assertIn("blog_follow", visible)


class ReplyContextTests(P5Fixture):
    def test_context_quotes_my_comment_and_offers_my_faces(self):
        post_id = self.post()
        mine = comment(self.conn, CLASSMATE, post_id, "第一行\n第二行\n第三行\n第四行")
        theirs = comment(self.conn, AUTHOR, post_id, "回复你", parent=mine)
        context = blog_comment_service.reply_context(self.conn, CLASSMATE, theirs)
        self.assertTrue(context["can_reply"])
        self.assertEqual("第一行\n第二行\n第三行", context["quote"])
        self.assertEqual(theirs, context["comment"]["id"])
        self.assertEqual(["real_name", "anonymous"], context["modes"])
        self.assertEqual("", context["forced_mode"])

    def test_anonymous_post_author_always_replies_as_op(self):
        post_id = self.post(mode="anonymous")
        theirs = comment(self.conn, CLASSMATE, post_id, "问一下")
        context = blog_comment_service.reply_context(self.conn, AUTHOR, theirs)
        self.assertEqual(("anonymous", "楼主"), (context["forced_mode"], context["forced_label"]))

    def test_closed_or_missing_targets_cannot_be_replied_to(self):
        post_id = self.post()
        theirs = comment(self.conn, AUTHOR, post_id, "作者楼")
        self.conn.execute("UPDATE blog_posts SET allow_comments=0 WHERE id=?", (post_id,))
        context = blog_comment_service.reply_context(self.conn, CLASSMATE, theirs)
        self.assertEqual((False, "帖子已关闭评论"), (context["can_reply"], context["reason"]))
        blog_comment_service.delete_comment(self.conn, AUTHOR, theirs)
        context = blog_comment_service.reply_context(self.conn, CLASSMATE, theirs)
        self.assertEqual((False, "这条评论已不存在"), (context["can_reply"], context["reason"]))


class ReviewRegressionTests(P5Fixture):
    """Findings of the P5 code and security reviews."""

    def test_editing_the_post_does_not_revive_a_deleted_comment_card(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "删掉的评论")
        blog_comment_service.delete_comment(self.conn, CLASSMATE, floor)
        blog_service.update_post(self.conn, AUTHOR, post_id, content_md="补充正文")
        self.assertTrue(self.items(AUTHOR)[0]["blog"]["gone"])

    def test_limit_then_restore_keeps_a_deleted_comment_card_gone(self):
        post_id = self.post()
        mine = comment(self.conn, CLASSMATE, post_id, "我的评论")
        gone_floor = comment(self.conn, AUTHOR, post_id, "作者回复", parent=mine)
        blog_comment_service.delete_comment(self.conn, AUTHOR, gone_floor)
        blog_moderation_service.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        blog_moderation_service.moderate(self.conn, TEACHER, "post", post_id, action="restore", note="已核实")
        self.assertTrue(self.items(CLASSMATE)[0]["blog"]["gone"])

    def test_opening_the_post_leaves_moderation_cards_unread(self):
        post_id = self.post()
        blog_moderation_service.moderate(self.conn, TEACHER, "post", post_id, action="limit", reason_code="spam")
        blog_notifications.mark_post_read(self.conn, AUTHOR, post_id)
        unread = [row for row in self.rows(AUTHOR) if not row["read_at"]]
        self.assertEqual(["blog_moderation"], [row["category"] for row in unread])

    def test_repeated_mentions_from_one_person_fold_into_one_card(self):
        post_id = self.post()
        comment(self.conn, OUTSIDER, post_id, "我在")
        for text in ("@王五 一", "@王五 二", "@王五 三"):
            comment(self.conn, CLASSMATE, post_id, text)
        mentions = [row for row in self.rows(OUTSIDER) if json.loads(row["metadata_json"])["event"] == "blog.mention"]
        self.assertEqual(1, len(mentions))
        self.assertEqual("李四 在评论中提到了你（3 次）", self.items(OUTSIDER)[0]["title"])

    def test_a_name_shared_by_two_people_pings_nobody(self):
        candidates = blog_notifications._mention_candidates(self.conn, {"id": 0, "author_display_mode": "anonymous"},
                                                            "student:21")
        self.assertEqual({}, candidates)
        self.conn.execute("UPDATE students SET name='李四' WHERE id=23")
        post_id = self.post()
        comment(self.conn, CLASSMATE, post_id, "我是李四")
        comment(self.conn, OUTSIDER, post_id, "我也叫李四")
        comment(self.conn, AUTHOR, post_id, "@李四 你好")
        self.assertNotIn("blog.mention", self.events(CLASSMATE) + self.events(OUTSIDER))

    def test_only_existing_people_can_be_followed(self):
        with self.assertRaises(ValueError):
            blog_community_service.set_follow(self.conn, CLASSMATE, target_type="author", target_key="student:999")
        self.assertEqual([], self.rows({"role": "student", "id": 999}))

    def test_a_failing_notification_never_undoes_the_like(self):
        post_id = self.post()
        with unittest.mock.patch.object(blog_notifications, "notify_like", side_effect=RuntimeError("boom")):
            result = blog_service.toggle_like(self.conn, CLASSMATE, "post", post_id)
        self.assertTrue(result["liked"])
        self.assertEqual(1, self.row(post_id)["like_count"])

    def test_dashboard_and_profile_feeds_use_the_current_persona(self):
        from classroom_app.services import dashboard_service, profile_service

        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "请教")
        comment(self.conn, AUTHOR, post_id, "回答", parent=floor)
        blog_service.update_post(self.conn, AUTHOR, post_id, author_display_mode="anonymous")
        activity = dashboard_service._load_recent_activity(self.conn, CLASSMATE)
        recent = profile_service._load_recent_notifications(self.conn, role="student", user_id=22)
        rendered = json.dumps([activity, recent], ensure_ascii=False)
        self.assertIn("楼主 回复了你的评论", rendered)
        self.assertNotIn("张三", rendered)

    def test_cards_never_carry_grouping_keys_or_raw_ids(self):
        for user in (CLASSMATE, OUTSIDER):
            blog_community_service.set_follow(self.conn, user, target_type="author", target_key="student:21")
        card = self.items(AUTHOR)[0]
        self.assertNotIn("aggregate_key", card)
        self.assertNotIn("aggregate_recent", card["metadata"])
        self.assertNotIn("student:22", json.dumps(card["metadata"]))

    def test_thread_stack_skips_deleted_replies(self):
        post_id = self.post()
        floor = comment(self.conn, CLASSMATE, post_id, "楼")
        comment(self.conn, OUTSIDER, post_id, "回复一", parent=floor)
        gone = comment(self.conn, TEACHER, post_id, "回复二", parent=floor)
        blog_comment_service.delete_comment(self.conn, TEACHER, gone)
        card = next(item for item in self.items(AUTHOR) if item["blog"]["event"] == "blog.thread_reply")
        self.assertEqual(["王五"], [face["name"] for face in card["blog"]["avatar_stack"]])

    def test_reply_context_marks_whose_quote_it_is_and_hides_post_existence(self):
        post_id = self.post()
        theirs = comment(self.conn, OUTSIDER, post_id, "王五的楼")
        reply = comment(self.conn, AUTHOR, post_id, "@李四 你也看看", parent=theirs)
        self.assertFalse(blog_comment_service.reply_context(self.conn, CLASSMATE, reply)["quote_is_mine"])
        self.conn.execute("UPDATE blog_posts SET visibility='class', visible_class_id=31 WHERE id=?", (post_id,))
        context = blog_comment_service.reply_context(self.conn, CLASSMATE, reply)
        self.assertEqual("这条评论已不存在", context["reason"])


if __name__ == "__main__":
    unittest.main()
