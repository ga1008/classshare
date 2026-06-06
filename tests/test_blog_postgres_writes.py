import unittest
from unittest.mock import patch

from classroom_app.services import blog_service


class FakeRow(dict):
    def keys(self):
        return super().keys()


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.execute_calls = []

    def cursor(self):
        raise AssertionError("blog write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.execute_calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT MAX(display_order)"):
            return FakeCursor(FakeRow({"max_order": 2}))
        return FakeCursor()


class BlogPostgresWriteTests(unittest.TestCase):
    def test_create_post_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(
            blog_service,
            "_normalize_post_visibility_settings",
            return_value=(blog_service.VISIBILITY_PUBLIC, None, []),
        ), patch.object(
            blog_service,
            "_resolve_post_media_assets",
            return_value=[],
        ), patch.object(
            blog_service,
            "_build_post_author_snapshot",
            return_value={
                "identity": "teacher:3",
                "role": "teacher",
                "user_pk": 3,
                "display_name": "Teacher",
                "display_mode": blog_service.AUTHOR_DISPLAY_REAL,
                "avatar_hash": "",
                "avatar_mime": "",
                "system_tags": [],
            },
        ), patch.object(
            blog_service,
            "execute_insert_returning_id",
            return_value=101,
        ) as insert_helper, patch.object(
            blog_service,
            "_sync_post_attachments",
            return_value=None,
        ) as sync_attachments:
            result = blog_service.create_post(
                conn,
                {"id": 3, "role": "teacher", "name": "Teacher"},
                title="Post",
                content_md="Content",
            )

        self.assertEqual(101, result["id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO blog_posts", insert_helper.call_args.args[1])
        sync_attachments.assert_called_once_with(conn, 101, [])

    def test_add_comment_uses_insert_returning_helper(self):
        conn = FakeConnection()
        post = FakeRow({"id": 5, "allow_comments": 1, "comment_count": 0})

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
            return_value=202,
        ) as insert_helper:
            result = blog_service.add_comment(
                conn,
                {"id": 3, "role": "teacher", "name": "Teacher"},
                5,
                content_md="Nice",
            )

        self.assertEqual(202, result["id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO blog_comments", insert_helper.call_args.args[1])
        self.assertTrue(any(call[0].startswith("UPDATE blog_posts SET comment_count") for call in conn.execute_calls))

    def test_add_attachment_uses_insert_returning_helper(self):
        conn = FakeConnection()

        with patch.object(blog_service, "execute_insert_returning_id", return_value=303) as insert_helper:
            attachment_id = blog_service.add_attachment(
                conn,
                post_id=5,
                file_hash="abc",
                filename="a.png",
                mime_type="image/png",
                file_size=10,
                image_width=20,
                image_height=30,
            )

        self.assertEqual(303, attachment_id)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO blog_attachments", insert_helper.call_args.args[1])
        self.assertEqual(3, insert_helper.call_args.args[2][-1])


if __name__ == "__main__":
    unittest.main()
