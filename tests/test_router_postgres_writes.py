import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from classroom_app.routers import emoji, feedback, files


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
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def cursor(self):
        raise AssertionError("router write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.execute_calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT id, user_id FROM app_feedback"):
            return FakeCursor(FakeRow({"id": params[0], "user_id": "3"}))
        if normalized.startswith("SELECT COUNT(*) AS cnt FROM app_feedback_attachments"):
            return FakeCursor(FakeRow({"cnt": 0}))
        if "FROM custom_emojis" in normalized and "file_hash" in normalized:
            return FakeCursor(None)
        if normalized.startswith("SELECT * FROM custom_emojis WHERE id"):
            return FakeCursor(FakeRow({"id": params[0], "display_name": "emoji"}))
        if normalized.startswith("SELECT id, file_name, file_size, file_hash"):
            return FakeCursor(
                FakeRow(
                    {
                        "id": 1,
                        "file_name": "existing.pdf",
                        "file_size": 123,
                        "file_hash": "abc",
                        "description": "desc",
                        "original_link": "",
                        "uploaded_at": "2026-01-01T00:00:00",
                    }
                )
            )
        if normalized.startswith("SELECT id FROM course_files WHERE course_id"):
            return FakeCursor(None)
        if normalized.startswith("SELECT * FROM chunked_uploads"):
            return FakeCursor(
                FakeRow(
                    {
                        "upload_id": params[0],
                        "course_id": 10,
                        "teacher_id": 3,
                        "file_name": "chunked.pdf",
                        "file_size": 123,
                        "description": "",
                        "is_public": 1,
                        "is_teacher_resource": 0,
                        "class_offering_id": 20,
                        "temp_dir": str(Path(tempfile.gettempdir()) / "lanshare-test-chunks"),
                        "total_chunks": 1,
                        "received_chunks": json.dumps([0]),
                        "status": "uploading",
                    }
                )
            )
        if normalized.startswith("SELECT cf.description"):
            return FakeCursor(FakeRow({"description": "existing desc", "original_link": ""}))
        return FakeCursor()

    def commit(self):
        self.commits += 1


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class FakeUpload:
    def __init__(self, data=b"image-bytes", filename="shot.png", content_type="image/png"):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self, _size=-1):
        return self._data


def run_async(coro):
    return asyncio.run(coro)


class RouterPostgresWriteTests(unittest.TestCase):
    def test_feedback_submit_uses_insert_returning_helper(self):
        conn = FakeConnection()
        with patch.object(feedback, "get_db_connection", return_value=conn), patch.object(
            feedback,
            "execute_insert_returning_id",
            return_value=101,
        ) as insert_helper, patch.object(
            feedback,
            "create_app_feedback_notifications",
            return_value=2,
        ):
            response = run_async(
                feedback.submit_feedback(
                    FakeRequest(
                        {
                            "feedback_type": "bug",
                            "title": "Upload problem",
                            "description": "Something failed",
                        }
                    ),
                    user={"id": 3, "role": "teacher", "name": "Teacher"},
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(101, payload["feedback_id"])
        self.assertEqual(2, payload["notification_count"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO app_feedback", insert_helper.call_args.args[1])

    def test_feedback_attachment_uses_insert_returning_helper(self):
        conn = FakeConnection()
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            feedback,
            "get_db_connection",
            return_value=conn,
        ), patch.object(
            feedback,
            "global_file_write_path",
            return_value=Path(tmpdir) / "img",
        ), patch.object(
            feedback,
            "execute_insert_returning_id",
            return_value=202,
        ) as insert_helper:
            response = run_async(
                feedback.upload_feedback_attachment(
                    101,
                    file=FakeUpload(),
                    user={"id": 3, "role": "teacher", "name": "Teacher"},
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(202, payload["attachment_id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO app_feedback_attachments", insert_helper.call_args.args[1])

    def test_custom_emoji_upload_uses_insert_returning_helper(self):
        conn = FakeConnection()
        with patch.object(emoji, "get_db_connection", return_value=conn), patch.object(
            emoji,
            "ensure_classroom_access",
            return_value=None,
        ), patch.object(
            emoji,
            "validate_and_store_custom_emoji",
            new=AsyncMock(
                return_value={
                    "hash": "emoji-hash",
                    "mime_type": "image/png",
                    "size": 10,
                    "width": 16,
                    "height": 16,
                }
            ),
        ), patch.object(
            emoji,
            "make_unique_custom_emoji_name",
            return_value="emoji",
        ), patch.object(
            emoji,
            "execute_insert_returning_id",
            return_value=303,
        ) as insert_helper, patch.object(
            emoji,
            "serialize_custom_emoji_row",
            side_effect=lambda _classroom_id, row: {"id": row["id"]},
        ):
            result = run_async(
                emoji.upload_custom_emoji(
                    20,
                    file=FakeUpload(filename="emoji.png"),
                    user={"id": 3, "role": "student"},
                )
            )

        self.assertTrue(result["created"])
        self.assertEqual({"id": 303}, result["emoji"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO custom_emojis", insert_helper.call_args.args[1])

    def test_file_check_link_uses_insert_returning_helper(self):
        conn = FakeConnection()
        with patch.object(files, "get_db_connection", return_value=conn), patch.object(
            files,
            "resolve_teacher_course_context",
            return_value={"course_id": 10, "class_offering_id": 20},
        ), patch.object(
            files,
            "build_course_file_scope",
            return_value={
                "owner_role": "teacher",
                "owner_user_pk": 3,
                "scope_level": "class_offering",
                "class_offering_id": 20,
                "class_id": 30,
                "school_code": "gxufl",
                "school_name": "School",
                "college": "College",
                "department": "Department",
            },
        ), patch.object(
            files,
            "execute_insert_returning_id",
            return_value=404,
        ) as insert_helper:
            result = run_async(
                files.check_file_exists(
                    files.FileCheckRequest(file_name="existing.pdf", file_size=123, course_id=10),
                    user={"id": 3, "role": "teacher"},
                )
            )

        self.assertTrue(result["linked"])
        self.assertEqual(404, result["file"]["id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO course_files", insert_helper.call_args.args[1])

    def test_complete_chunked_upload_uses_insert_returning_helper(self):
        conn = FakeConnection()
        with patch.object(files, "get_db_connection", return_value=conn), patch.object(
            files,
            "sync_assemble_file",
            return_value=("file-hash", 123),
        ), patch.object(
            files,
            "build_course_file_scope",
            return_value={
                "owner_role": "teacher",
                "owner_user_pk": 3,
                "scope_level": "class_offering",
                "class_offering_id": 20,
                "class_id": 30,
                "school_code": "gxufl",
                "school_name": "School",
                "college": "College",
                "department": "Department",
            },
        ), patch.object(
            files,
            "execute_insert_returning_id",
            return_value=505,
        ) as insert_helper, patch.object(
            files,
            "broadcast_file_update",
            new=AsyncMock(),
        ), patch.object(
            files.shutil,
            "rmtree",
            return_value=None,
        ):
            result = run_async(
                files.complete_chunked_upload(
                    files.UploadCompleteRequest(upload_id="upload-1"),
                    user={"id": 3, "role": "teacher"},
                )
            )

        self.assertEqual("success", result["status"])
        self.assertEqual(505, result["file_id"])
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO course_files", insert_helper.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
