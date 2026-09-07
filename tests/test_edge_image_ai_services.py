from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack, contextmanager
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from classroom_app.services import blog_ai_service as blog
from classroom_app.services import chat_image_derivatives as derivatives
from classroom_app.services import edge_image_input_service as images
from classroom_app.services import message_center_service as private
from tests import test_message_center_private_ai_jobs as private_job_tests


class EdgeImagePreparationTests(unittest.TestCase):
    def test_preview_is_bounded_and_decoded_only_for_authorized_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source_hash = "a" * 64
            Image.new("RGB", (1600, 900), "white").save(directory / source_hash, format="PNG")
            resolver = lambda value: directory / value if (directory / value).is_file() else None
            with patch.object(images, "resolve_global_file_path", side_effect=resolver), patch.object(
                derivatives, "global_file_write_path", side_effect=lambda value: directory / value
            ):
                result = asyncio.run(images.prepare_edge_image_inputs(
                    [{"file_hash": source_hash, "mime_type": "image/png", "original_filename": "题目.png"}],
                    source_feature="private_message", max_images=8, max_bytes=1000000,
                ))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["source_kind"], "current_message_attachment")
            with Image.open(io.BytesIO(base64.b64decode(result[0]["url"].split(",", 1)[1]))) as preview:
                self.assertEqual(preview.format, "JPEG")
                self.assertLessEqual(preview.width, 1024)
                self.assertLessEqual(preview.height, 720)

    def test_missing_bad_hash_wrong_mime_and_count_fail_instead_of_silent_omission(self):
        cases = [
            [{"file_hash": "https://example.invalid/image", "mime_type": "image/png"}],
            [{"file_hash": "a" * 64, "mime_type": "application/pdf"}],
            [{"file_hash": "a" * 64, "mime_type": "image/png"}],
            [{"file_hash": "a" * 64, "mime_type": "image/png"}] * 9,
        ]
        with patch.object(images, "resolve_global_file_path", return_value=None):
            for assets in cases:
                with self.subTest(assets=assets), self.assertRaises(ValueError):
                    images._prepare_images(assets, "private_message", 8, 1000000)

    def test_pixel_limit_is_checked_before_shared_full_decode(self):
        path = Mock()
        path.is_file.return_value = True
        path.stat.return_value.st_size = 100
        probe = Mock()
        probe.size = (10000, 10000)
        opened = Mock()
        opened.__enter__ = Mock(return_value=probe)
        opened.__exit__ = Mock(return_value=False)
        with patch.object(images, "resolve_global_file_path", return_value=path), patch.object(
            images.Image, "open", return_value=opened
        ), patch.object(images, "build_chat_image_derivative_sync") as decoder:
            with self.assertRaisesRegex(ValueError, "像素"):
                images._prepare_images([{"file_hash": "a" * 64, "mime_type": "image/png"}], "blog", 4, 1000)
            decoder.assert_not_called()


class PrivateImageReplyTests(unittest.TestCase):
    def setUp(self):
        self.conn = private_job_tests.PrivateMessageAIJobTests()._sqlite_conn()
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE private_messages(id INTEGER PRIMARY KEY, conversation_key TEXT,
              class_offering_id INTEGER, sender_identity TEXT, sender_role TEXT,
              recipient_identity TEXT, content TEXT, created_at TEXT);
            CREATE TABLE private_message_attachments(id INTEGER PRIMARY KEY, message_id INTEGER,
              conversation_key TEXT, uploaded_by_identity TEXT, file_hash TEXT, mime_type TEXT,
              original_filename TEXT);
            INSERT INTO private_messages VALUES(1,'thread',10,'student:1','student','assistant:10','历史','1');
            INSERT INTO private_messages VALUES(2,'thread',10,'student:1','student','assistant:10','','2');
            INSERT INTO private_messages VALUES(3,'thread',10,'student:1','student','assistant:10','稍后的消息','3');
            INSERT INTO private_message_ai_jobs(id, conversation_key, class_offering_id, request_message_id,
              requester_identity, requester_role, requester_user_pk, status, attempt_count)
              VALUES(7,'thread',10,2,'student:1','student',1,'running',1);
        """)
        self.conn.execute("INSERT INTO private_message_attachments VALUES(1,1,'thread','student:1',?,'image/png','旧图')", ("a" * 64,))
        self.conn.execute("INSERT INTO private_message_attachments VALUES(2,2,'thread','student:1',?,'image/png','本图')", ("b" * 64,))
        self.conn.commit()
        self.user = {"id": 1, "role": "student", "name": "同学"}

    @contextmanager
    def db(self):
        try:
            yield self.conn
        except Exception:
            self.conn.rollback()
            raise

    def test_exact_request_owner_and_scope_and_bounded_text_history(self):
        assets = private._load_private_ai_request_assets(self.conn, user=self.user, class_offering_id=10,
                                                         conversation_key="thread", request_message_id=2)
        self.assertEqual([item["original_filename"] for item in assets], ["本图"])
        self.assertEqual([item["id"] for item in private._load_private_ai_history(self.conn, "thread", 2)], [1, 2])
        for kwargs in ({"user": {"id": 2, "role": "student"}}, {"class_offering_id": 20}, {"conversation_key": "else"}):
            params = {"user": self.user, "class_offering_id": 10, "conversation_key": "thread", "request_message_id": 2}
            params.update(kwargs)
            with self.assertRaises(ValueError):
                private._load_private_ai_request_assets(self.conn, **params)
        self.conn.execute("UPDATE private_message_attachments SET uploaded_by_identity='student:2' WHERE id=2")
        with self.assertRaisesRegex(ValueError, "附件"):
            private._load_private_ai_request_assets(self.conn, user=self.user, class_offering_id=10,
                                                   conversation_key="thread", request_message_id=2)

    def _generation_patches(self, image_inputs):
        stack = ExitStack()
        stack.enter_context(patch.object(private, "get_db_connection", self.db))
        stack.enter_context(patch.object(private, "_resolve_contact", return_value={"can_send": True}))
        for name, value in (("load_ai_class_config", {}), ("build_classroom_ai_context", {}),
                            ("load_latest_hidden_profile", {}), ("_build_ai_private_user_context", ""),
                            ("compose_classroom_chat_system_prompt", "系统")):
            stack.enter_context(patch.object(private, name, return_value=value))
        stack.enter_context(patch.object(images, "prepare_edge_image_inputs", AsyncMock(return_value=image_inputs)))
        response = Mock()
        response.json.return_value = {"status": "success", "response_text": "图片里是一道题。"}
        client = stack.enter_context(patch.object(private.ai_client, "post", AsyncMock(return_value=response)))
        return stack, client

    def test_pure_image_request_routes_vision_current_only_and_text_routes_standard(self):
        for visual in (True, False):
            if not visual:
                self.conn.execute("DELETE FROM private_message_attachments WHERE message_id=2")
                self.conn.execute("UPDATE private_messages SET content='解释一下' WHERE id=2")
            stack, client = self._generation_patches([{"url": "data:image/jpeg;base64,YQ=="}] if visual else [])
            with stack:
                asyncio.run(private._generate_ai_private_reply_text(self.user, class_offering_id=10,
                                                                     conversation_key="thread", request_message_id=2))
            payload = client.call_args.kwargs["json"]
            self.assertEqual(payload["model_capability"], "vision" if visual else "standard")
            self.assertEqual(payload["task_type"], "vision_interactive" if visual else "fast_text_response")
            self.assertTrue(payload["new_message"])
            self.assertEqual(payload["business_context"]["logical_call_id"], "private-message:2")
            self.assertNotIn("稍后的消息", str(payload))
            self.assertNotIn("旧图", str(payload))

    def test_removed_classroom_access_blocks_queued_image_before_ai(self):
        stack, client = self._generation_patches([])
        with stack, patch.object(private, "_resolve_contact", return_value=None):
            with self.assertRaisesRegex(ValueError, "无法访问"):
                asyncio.run(private._generate_ai_private_reply_text(self.user, class_offering_id=10,
                    conversation_key="thread", request_message_id=2))
            client.assert_not_called()

    def test_reply_and_job_finish_are_atomic_and_replay_does_not_call_ai(self):
        def insert(conn, **kwargs):
            conn.execute("INSERT INTO private_messages VALUES(9,'thread',10,'assistant:10','assistant','student:1',?,'4')", (kwargs["content"],))
            return {"id": 9, "content": kwargs["content"]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(private, "get_db_connection", self.db))
            generate = stack.enter_context(patch.object(private, "_generate_ai_private_reply_text", AsyncMock(return_value="回答")))
            stack.enter_context(patch.object(private, "_lookup_identity_display_name", return_value={"display_name": "AI"}))
            stack.enter_context(patch.object(private, "_insert_private_message", side_effect=insert))
            stack.enter_context(patch.object(private, "_insert_private_message_audit"))
            stack.enter_context(patch.object(private, "_serialize_private_message", side_effect=lambda row, **kw: dict(row)))
            stack.enter_context(patch.object(private, "_load_blocked_identity_map", return_value={}))
            params = dict(class_offering_id=10, conversation_key="thread", request_message_id=2, job_id=7, attempt_count=1)
            first = asyncio.run(private.generate_ai_private_reply(self.user, **params))
            second = asyncio.run(private.generate_ai_private_reply(self.user, **params))
        self.assertEqual(first["id"], 9)
        self.assertIsNone(second)
        self.assertEqual(generate.await_count, 1)
        job = self.conn.execute("SELECT * FROM private_message_ai_jobs WHERE id=7").fetchone()
        self.assertEqual((job["status"], job["reply_message_id"]), ("completed", 9))

    def test_restart_does_not_automatically_repeat_an_unknown_paid_call(self):
        with patch.object(private, "get_db_connection", self.db), patch.object(private, "get_configured_db_engine", return_value="sqlite"):
            self.assertEqual(private.schedule_pending_private_ai_reply_jobs(), 0)
        job = self.conn.execute("SELECT * FROM private_message_ai_jobs WHERE id=7").fetchone()
        self.assertEqual(job["status"], "failed")
        self.assertIn("重复计费", job["error_message"])

    def test_failed_reply_insert_rolls_back_the_completion_claim(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(private, "get_db_connection", self.db))
            stack.enter_context(patch.object(private, "_generate_ai_private_reply_text", AsyncMock(return_value="回答")))
            stack.enter_context(patch.object(private, "_lookup_identity_display_name", return_value={"display_name": "AI"}))
            stack.enter_context(patch.object(private, "_insert_private_message", side_effect=RuntimeError("insert failed")))
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                asyncio.run(private.generate_ai_private_reply(self.user, class_offering_id=10, conversation_key="thread",
                    request_message_id=2, job_id=7, attempt_count=1))
        row = self.conn.execute("SELECT status,reply_message_id FROM private_message_ai_jobs WHERE id=7").fetchone()
        self.assertEqual(tuple(row), ("running", None))


class BlogImageReplyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
          CREATE TABLE blog_media_assets(id INTEGER PRIMARY KEY,file_hash TEXT,uploader_identity TEXT,
            mime_type TEXT,original_filename TEXT,updated_at TEXT);
          CREATE TABLE blog_ai_reply_jobs(id INTEGER PRIMARY KEY,trigger_type TEXT,trigger_id INTEGER,
            post_id INTEGER,trigger_author_identity TEXT,status TEXT,assistant_comment_id INTEGER,
            error_message TEXT,created_at TEXT,updated_at TEXT,UNIQUE(trigger_type,trigger_id));
          CREATE TABLE blog_posts(id INTEGER PRIMARY KEY,title TEXT,content_md TEXT,status TEXT);
          CREATE TABLE blog_comments(id INTEGER PRIMARY KEY,post_id INTEGER,content_md TEXT,status TEXT);
          INSERT INTO blog_posts VALUES(10,'帖子','@管家 题目','published');
          INSERT INTO blog_comments VALUES(20,10,'@管家 本图','active');
        """)
        for pk, value, identity in ((1, "a", "student:1"), (2, "b", "student:2"), (3, "c", "student:1")):
            self.conn.execute("INSERT INTO blog_media_assets VALUES(?,?,?,'image/png','题目','now')", (pk, value * 64, identity))

    def test_only_current_author_images_never_history_post_or_emoji(self):
        item = {"author_identity": "student:1", "content_md": "@管家", "post_content_md": f"![](/api/blog/image/{'c'*64})",
                "attachments_json": json.dumps([{"type": "image", "file_hash": "a" * 64}]),
                "emoji_payload_json": json.dumps([{"file_hash": "c" * 64}])}
        assets = blog._load_current_blog_ai_assets(self.conn, item, "comment")
        self.assertEqual([row["file_hash"] for row in assets], ["a" * 64])
        item["attachments_json"] = json.dumps([{"type": "image", "file_hash": "b" * 64}])
        with self.assertRaisesRegex(ValueError, "不属于"):
            blog._load_current_blog_ai_assets(self.conn, item, "comment")

    def test_post_only_embedded_images_and_over_limit_rejected(self):
        item = {"author_identity": "student:1", "content_md": f"@管家 ![](/api/blog/image/{'a'*64})"}
        self.assertEqual(len(blog._load_current_blog_ai_assets(self.conn, item, "post")), 1)
        item["content_md"] = " ".join(f"![](/api/blog/image/{value*64})" for value in "abcde")
        with self.assertRaisesRegex(ValueError, "最多"):
            blog._load_current_blog_ai_assets(self.conn, item, "post")
        item["content_md"] = "@管家 ![](https://example.invalid/image.png)"
        with self.assertRaisesRegex(ValueError, "平台"):
            blog._load_current_blog_ai_assets(self.conn, item, "post")

    def test_uploading_image_without_mention_does_not_trigger_ai(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {
            "id": 10, "status": "published", "author_identity": "student:1",
            "title": "图片", "content_md": f"![](/api/blog/image/{'a'*64})",
        }
        @contextmanager
        def db():
            yield conn
        with patch.object(blog, "get_db_connection", db), patch.object(blog, "_prepare_reply_job") as claim, patch.object(
            blog.ai_client, "post", AsyncMock()
        ) as client:
            asyncio.run(blog.maybe_reply_to_post_mention(10, {"id": 1, "role": "student"}))
            claim.assert_not_called()
            client.assert_not_called()

    def test_job_claim_and_result_are_idempotent_and_deleted_source_suppressed(self):
        self.assertTrue(blog._prepare_reply_job(self.conn, "comment", 20, 10, "student:1"))
        self.assertFalse(blog._prepare_reply_job(self.conn, "comment", 20, 10, "student:1"))
        with patch.object(blog, "add_comment", return_value={"id": 30}) as add:
            params = dict(trigger_type="comment", trigger_id=20, post_id=10, source_text="@管家 本图", reply_text="回答")
            self.assertTrue(blog._publish_blog_ai_reply(self.conn, **params))
            self.assertFalse(blog._publish_blog_ai_reply(self.conn, **params))
            add.assert_called_once()
        self.assertFalse(blog._prepare_reply_job(self.conn, "comment", 20, 10, "student:1"))
        self.assertTrue(blog._prepare_reply_job(self.conn, "post", 10, 10, "student:1"))
        self.conn.execute("UPDATE blog_posts SET status='deleted' WHERE id=10")
        with patch.object(blog, "add_comment") as add:
            self.assertFalse(blog._publish_blog_ai_reply(self.conn, trigger_type="post", trigger_id=10,
                post_id=10, source_text="帖子\n@管家 题目", reply_text="回答"))
            add.assert_not_called()

    def test_blog_payload_routes_current_images_and_text_without_paid_calls(self):
        response = Mock()
        response.json.return_value = {"status": "success", "response_text": "回答"}
        for visual in (True, False):
            with patch.object(blog, "prepare_edge_image_inputs", AsyncMock(return_value=[{"url": "data:image/jpeg;base64,YQ=="}] if visual else [])), patch.object(
                blog.ai_client, "post", AsyncMock(return_value=response)
            ) as client:
                asyncio.run(blog._generate_housekeeper_reply(caller_display_name="同学", caller_profile_prompt="",
                    semester_context="", post_context="", recent_comments_context="", blog_overview_context="",
                    request_text="题目", trigger_label="评论", trigger_type="comment", trigger_id=20, current_assets=[]))
            payload = client.call_args.kwargs["json"]
            self.assertEqual(payload["model_capability"], "vision" if visual else "standard")
            self.assertEqual(payload["business_context"], {"operation": "chat", "source_feature": "blog", "logical_call_id": "blog:comment:20"})


if __name__ == "__main__":
    unittest.main()
