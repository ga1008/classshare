import unittest
from unittest.mock import MagicMock, patch

from classroom_app.services import blog_notifications, blog_service


class BlogNotificationTests(unittest.TestCase):
    def test_comment_on_assistant_post_does_not_target_non_user_inbox(self):
        conn = MagicMock()
        post = {
            "id": 12,
            "title": "AI 编辑部文章",
            "author_identity": "assistant:0",
            "author_role": "assistant",
            "author_user_pk": 0,
        }

        with patch.object(blog_notifications, "_insert_notification") as insert_notification:
            blog_notifications.notify_new_comment(
                conn,
                post,
                comment_id=31,
                parent_comment_id=None,
                commenter_identity="teacher:7",
                commenter_role="teacher",
                commenter_pk=7,
                commenter_name="张老师",
                comment_preview="@管家 你觉得呢",
            )

        insert_notification.assert_not_called()

    def test_reply_to_assistant_comment_does_not_target_non_user_inbox(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "author_identity": "assistant:0",
            "author_role": "assistant",
            "author_user_pk": 0,
            "author_display_name": "管家",
        }

        with patch.object(blog_notifications, "_insert_notification") as insert_notification:
            blog_notifications.notify_new_comment(
                conn,
                {"id": 12, "title": "讨论帖"},
                comment_id=33,
                parent_comment_id=32,
                commenter_identity="student:8",
                commenter_role="student",
                commenter_pk=8,
                commenter_name="小林",
                comment_preview="继续说说",
            )

        insert_notification.assert_not_called()

    def test_assistant_reply_still_notifies_human_commenter(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "author_identity": "student:8",
            "author_role": "student",
            "author_user_pk": 8,
            "author_display_name": "小林",
        }

        with patch.object(blog_notifications, "_insert_notification") as insert_notification:
            blog_notifications.notify_new_comment(
                conn,
                {"id": 12, "title": "讨论帖"},
                comment_id=34,
                parent_comment_id=33,
                commenter_identity="assistant:0",
                commenter_role="assistant",
                commenter_pk=0,
                commenter_name="管家",
                comment_preview="可以从三个角度看。",
            )

        payload = insert_notification.call_args.args[1]
        self.assertEqual("student:8", payload["recipient_identity"])
        self.assertEqual("student", payload["recipient_role"])
        self.assertEqual("assistant", payload["actor_role"])
        self.assertEqual("管家", payload["actor_display_name"])

    def test_assistant_posts_skip_featured_and_hot_notifications(self):
        conn = MagicMock()
        post = {
            "id": 12,
            "title": "AI 编辑部文章",
            "author_role": "assistant",
            "author_user_pk": 0,
        }

        with patch.object(blog_notifications, "_insert_notification") as insert_notification:
            blog_notifications.notify_post_featured(
                conn,
                post,
                moderator_identity="teacher:7",
                moderator_role="teacher",
                moderator_pk=7,
            )
            blog_notifications.notify_post_hot(conn, post, score=42)

        insert_notification.assert_not_called()

    def test_add_comment_to_assistant_post_completes_primary_write(self):
        conn = MagicMock()
        post = {
            "id": 12,
            "title": "AI 编辑部文章",
            "status": blog_service.POST_STATUS_PUBLISHED,
            "allow_comments": 1,
            "author_identity": "assistant:0",
            "author_role": "assistant",
            "author_user_pk": 0,
            "comment_count": 0,
        }

        with patch.object(blog_service, "_get_post_raw", return_value=post), patch.object(
            blog_service,
            "_can_view_post",
            return_value=True,
        ), patch.object(
            blog_service,
            "_normalize_comment_custom_emojis",
            return_value=[],
        ), patch.object(
            blog_service,
            "_normalize_comment_attachments",
            return_value=[],
        ), patch.object(
            blog_service,
            "execute_insert_returning_id",
            return_value=31,
        ):
            result = blog_service.add_comment(
                conn,
                {"id": 7, "role": "teacher", "name": "张老师"},
                12,
                content_md="@管家 你觉得呢",
                notify_callback=blog_notifications.notify_new_comment,
            )

        self.assertEqual(31, result["id"])
        self.assertTrue(
            any(
                "UPDATE blog_posts SET comment_count = comment_count + 1" in str(call.args[0])
                for call in conn.execute.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
