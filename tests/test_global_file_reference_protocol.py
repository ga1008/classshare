from __future__ import annotations

import asyncio
import errno
import hashlib
import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile
from classroom_app.db import connection
from classroom_app.services import file_service as files


class GlobalFileReferenceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-file-protocol-")
        self.root = Path(self.temp.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.patches = [patch.object(files, "GLOBAL_FILES_DIR", self.root / "files"),
                        patch.object(files, "GLOBAL_FILES_LEGACY_DIRS", ()),
                        patch.object(connection, "get_configured_db_engine", return_value="sqlite")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.conn.close()
        self.temp.cleanup()

    def save(self, data=b"shared file"):
        return files.store_file_object_globally(io.BytesIO(data))

    @contextmanager
    def connect(self):
        with self.conn:
            yield self.conn

    def test_all_declared_hash_columns_protect_shared_file(self):
        digest = self.save()["hash"]
        references = [("students", "avatar_file_hash"), ("teachers", "avatar_file_hash"),
                      ("discussion_attachments", "file_hash"), ("private_message_attachments", "preview_file_hash"),
                      ("custom_emojis", "file_hash"), ("future_feature", "source_file_hash")]
        for table, column in references:
            self.conn.execute(f"CREATE TABLE {table} ({column} TEXT)")
            self.conn.execute(f"INSERT INTO {table} VALUES (?)", (digest,))
        self.assertEqual(len(references), files.count_global_file_references(self.conn, digest))
        self.assertFalse(asyncio.run(files.delete_global_file(digest, conn=self.conn)))
        self.assertTrue(Path(self.save()["path"]).exists())

    def test_uncommitted_detach_and_rollback_never_loses_file(self):
        saved = self.save()
        self.conn.execute("CREATE TABLE course_files(file_hash TEXT)")
        self.conn.execute("INSERT INTO course_files VALUES(?)", (saved["hash"],))
        self.conn.commit()
        self.conn.execute("DELETE FROM course_files")
        self.assertEqual(0, files.count_global_file_references(self.conn, saved["hash"]))
        self.assertFalse(asyncio.run(files.delete_global_file(saved["hash"], conn=self.conn)))
        self.conn.rollback()
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM course_files").fetchone()[0])
        self.assertEqual(b"shared file", Path(saved["path"]).read_bytes())

    def test_hash_cannot_escape_the_store(self):
        outside = self.root / "private.txt"
        outside.write_text("keep", encoding="utf-8")
        for invalid in ("../private.txt", "../" + "a" * 64, "/tmp/secret", "g" * 64, ""):
            self.assertEqual((), files.global_file_candidates(invalid))
            with self.assertRaises(ValueError):
                files.global_file_write_path(invalid)
        self.assertEqual("keep", outside.read_text())

    def test_sorted_unique_batch_locks_and_missing_file_conflict(self):
        saved = [self.save(b"a"), self.save(b"b")]
        calls = []
        class PG:
            def execute(self, query, params=()):
                calls.append(params[0])
        with patch.object(connection, "get_configured_db_engine", return_value="postgres"):
            hashes = files.bind_global_file_references(PG(), [saved[1]["hash"], saved[0]["hash"], saved[1]["hash"]])
        expected = tuple(sorted(item["hash"] for item in saved))
        self.assertEqual(expected, hashes)
        self.assertEqual([int.from_bytes(hashlib.sha256(("lanshare:blob:" + value).encode()).digest()[:8], "big", signed=True)
                          for value in expected], calls)
        Path(saved[0]["path"]).unlink()
        with self.assertRaises(HTTPException) as caught:
            files.bind_global_file_references(self.conn, [saved[0]["hash"]])
        self.assertEqual(409, caught.exception.status_code)
        self.assertIn("重新上传", caught.exception.detail)

    def test_concurrent_identical_uploads_publish_complete_bytes(self):
        data = b"same-upload" * 100000
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.save(data), range(16)))
        self.assertEqual(1, len({item["hash"] for item in results}))
        self.assertTrue(all(Path(item["path"]).read_bytes() == data for item in results))
        self.assertFalse(list((self.root / "files").rglob(".upload-*")))

    def test_failed_upload_never_publishes_partial_file(self):
        def partial_then_fail(source, output, length):
            output.write(b"partial")
            raise OSError("disk write failed")
        with patch.object(files.shutil, "copyfileobj", side_effect=partial_then_fail):
            with self.assertRaises(OSError):
                self.save(b"not-published")
        digest = hashlib.sha256(b"not-published").hexdigest()
        self.assertIsNone(files.resolve_global_file_path(digest))
        self.assertFalse(list((self.root / "files").rglob(".upload-*")))

    def test_atomic_upload_supports_filesystem_without_hardlinks(self):
        with patch.object(files.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            saved = self.save(b"fallback upload")
        self.assertEqual(b"fallback upload", Path(saved["path"]).read_bytes())
        self.assertFalse(list((self.root / "files").rglob(".upload-*")))

    def test_platform_avatar_rejects_missing_blob_before_binding(self):
        from classroom_app.services import profile_service
        self.conn.execute("CREATE TABLE students(id INTEGER PRIMARY KEY, avatar_file_hash TEXT, avatar_mime_type TEXT, avatar_updated_at TEXT)")
        self.conn.execute("INSERT INTO students VALUES(1,'old','image/png','before')")
        with patch.object(profile_service, "get_user_profile", return_value={}):
            with self.assertRaises(HTTPException) as caught:
                profile_service.update_profile_avatar(self.conn, {"role": "student", "id": 1}, file_hash="a" * 64, mime_type="image/png")
            self.assertEqual(409, caught.exception.status_code)
            self.assertEqual("old", self.conn.execute("SELECT avatar_file_hash FROM students").fetchone()[0])
            saved = self.save()
            profile_service.update_profile_avatar(self.conn, {"role": "student", "id": 1}, file_hash=saved["hash"], mime_type="image/png")
            self.assertEqual(saved["hash"], self.conn.execute("SELECT avatar_file_hash FROM students").fetchone()[0])

    def test_course_upload_rejects_lost_blob_and_preserves_transaction(self):
        from classroom_app.routers.manage_parts import classes_courses_courses as routes
        self.conn.execute("CREATE TABLE courses(id INTEGER,created_by_teacher_id INTEGER)")
        self.conn.execute("INSERT INTO courses VALUES(10,1)")
        self.conn.execute("CREATE TABLE course_files(course_id INTEGER,file_name TEXT,file_hash TEXT,file_size INTEGER,is_public INTEGER,is_teacher_resource INTEGER,uploaded_by_teacher_id INTEGER)")
        self.conn.commit()
        upload = UploadFile(filename="notes.txt", file=io.BytesIO(b"notes"))
        with patch.object(routes, "get_db_connection", self.connect), patch.object(routes, "save_file_globally", new=AsyncMock(return_value={"hash": "a" * 64, "size": 5})):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(routes.api_upload_course_file(10, upload, True, False, {"id": 1, "role": "teacher"}))
        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM course_files").fetchone()[0])

    def test_discussion_attachment_checks_all_derivatives_before_insert(self):
        from classroom_app.services import discussion_attachment_service as discussion
        original, thumb = self.save(b"original"), self.save(b"thumb")
        payload = {"width": 100, "height": 100, "thumbnail": {"file_hash": thumb["hash"]}, "preview": {"file_hash": "a" * 64}}
        upload = UploadFile(filename="image.png", file=io.BytesIO(b"image"), headers={"content-type": "image/png"})
        with patch.object(discussion, "ensure_discussion_attachment_schema"), \
             patch.object(discussion, "save_file_globally", new=AsyncMock(return_value=original)), \
             patch.object(discussion, "prepare_chat_image_derivatives", new=AsyncMock(return_value=payload)), \
             patch.object(discussion, "execute_insert_returning_id") as insert:
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(discussion.create_discussion_attachment(self.conn, 1, {"id": 1, "role": "student"}, upload))
        self.assertEqual(409, caught.exception.status_code)
        insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
