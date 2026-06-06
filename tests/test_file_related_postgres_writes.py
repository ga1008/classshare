import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from classroom_app.services import discussion_attachment_service, signature_service


class FakeRow(dict):
    def keys(self):
        return super().keys()


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self):
        self.execute_calls = []

    def cursor(self):
        raise AssertionError("file-related write paths must not use raw cursor()")

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.execute_calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT * FROM discussion_attachments WHERE id"):
            return FakeCursor(FakeRow({"id": params[0], "file_hash": "hash"}))
        return FakeCursor()


class FakeUpload:
    def __init__(self, *, filename="image.png", content_type="image/png", data=None):
        self.filename = filename
        self.content_type = content_type
        self._data = data or b"\x89PNG\r\n\x1a\nfake-png"
        self._read = False

    async def read(self, _size=-1):
        if self._read:
            return b""
        self._read = True
        return self._data

    async def seek(self, _offset):
        self._read = False


def run_async(coro):
    return asyncio.run(coro)


class FileRelatedPostgresWriteTests(unittest.TestCase):
    def test_discussion_attachment_uses_insert_returning_helper(self):
        conn = FakeConnection()
        derivative_payload = {
            "width": 100,
            "height": 80,
            "thumbnail": {
                "file_hash": "thumb",
                "mime_type": "image/webp",
                "file_size": 10,
                "width": 50,
                "height": 40,
            },
            "preview": {
                "file_hash": "preview",
                "mime_type": "image/webp",
                "file_size": 20,
                "width": 100,
                "height": 80,
            },
        }

        with patch.object(
            discussion_attachment_service,
            "ensure_discussion_attachment_schema",
            return_value=None,
        ), patch.object(
            discussion_attachment_service,
            "save_file_globally",
            new=AsyncMock(return_value={"hash": "hash", "size": 30, "path": str(Path("fake.png"))}),
        ), patch.object(
            discussion_attachment_service,
            "prepare_chat_image_derivatives",
            new=AsyncMock(return_value=derivative_payload),
        ), patch.object(
            discussion_attachment_service,
            "execute_insert_returning_id",
            return_value=606,
        ) as insert_helper, patch.object(
            discussion_attachment_service,
            "build_discussion_attachment_payload",
            return_value={"id": 606},
        ):
            result = run_async(
                discussion_attachment_service.create_discussion_attachment(
                    conn,
                    20,
                    {"id": 3, "role": "student"},
                    FakeUpload(),
                )
            )

        self.assertEqual({"id": 606}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO discussion_attachments", insert_helper.call_args.args[1])

    def test_discussion_attachment_schema_validates_postgres_without_sqlite_ddl(self):
        conn = FakeConnection()
        rows = [FakeRow({"column_name": column}) for column in discussion_attachment_service.DISCUSSION_ATTACHMENT_REQUIRED_COLUMNS]

        def execute(sql, params=()):
            normalized = " ".join(str(sql).split())
            conn.execute_calls.append((normalized, tuple(params)))
            if "information_schema.columns" in normalized:
                return FakeCursor(rows=rows)
            raise AssertionError(f"Unexpected SQL: {normalized}")

        conn.execute = execute
        with patch.object(discussion_attachment_service, "get_configured_db_engine", return_value="postgres"):
            discussion_attachment_service.ensure_discussion_attachment_schema(conn)

        sql_text = "\n".join(sql for sql, _ in conn.execute_calls)
        self.assertIn("information_schema.columns", sql_text)
        self.assertNotIn("CREATE TABLE", sql_text)
        self.assertNotIn("PRAGMA", sql_text)

    def test_signature_upload_uses_insert_returning_helper(self):
        conn = FakeConnection()
        actor = {
            "role": "teacher",
            "id": 3,
            "name": "Teacher",
            "is_super_admin": False,
            "scope": {
                "school_code": "gxufl",
                "school_name": "School",
                "college": "College",
                "department": "Department",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            signature_path = Path(tmpdir) / "fake.png"
            signature_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
            with patch.object(signature_service, "build_signature_actor", return_value=actor), patch.object(
                signature_service,
                "_store_signature_bytes",
                new=AsyncMock(return_value=signature_path),
            ), patch.object(
                signature_service,
                "execute_insert_returning_id",
                return_value=707,
            ) as insert_helper, patch.object(
                signature_service,
                "get_signature_row_for_actor",
                return_value=(FakeRow({"id": 707}), actor),
            ) as get_signature, patch.object(
                signature_service,
                "serialize_signature",
                return_value={"id": 707},
            ):
                result = run_async(
                    signature_service.create_signature_from_upload(
                        conn,
                        {"id": 3, "role": "teacher", "name": "Teacher"},
                        FakeUpload(filename="signature.png"),
                        name="Signature",
                    )
                )

        self.assertEqual({"id": 707}, result)
        self.assertEqual(1, insert_helper.call_count)
        self.assertIn("INSERT INTO electronic_signatures", insert_helper.call_args.args[1])
        get_signature.assert_called_once()
        self.assertEqual(707, get_signature.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
