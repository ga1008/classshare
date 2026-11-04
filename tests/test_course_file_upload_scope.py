from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers import files as routes
from classroom_app.services import file_service


class ChunkedUploadScopeTests(unittest.TestCase):
    """分块上传完成时的作用域判定。

    ``chunked_uploads.is_public`` / ``is_teacher_resource`` 在 PostgreSQL 里是
    TEXT 列，读回 ``'true'`` / ``'false'``。字符串 ``'false'`` 为真，曾让每次
    课堂资源上传都被写成 ``scope_level='private'``，学生在资源区看不到文件。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-upload-scope-")
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite"
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE teachers(id INTEGER PRIMARY KEY, is_super_admin INTEGER, is_active INTEGER);
                INSERT INTO teachers VALUES(7,0,1);
                CREATE TABLE courses(
                    id INTEGER PRIMARY KEY, created_by_teacher_id INTEGER,
                    school_code TEXT, school_name TEXT, college TEXT, department TEXT
                );
                INSERT INTO courses VALUES(19,7,'gxufl','合成大学','合成学院','合成系');
                CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT, created_by_teacher_id INTEGER);
                INSERT INTO classes VALUES(27,'合成班',7);
                CREATE TABLE class_offerings(id INTEGER PRIMARY KEY, teacher_id INTEGER, course_id INTEGER, class_id INTEGER);
                INSERT INTO class_offerings VALUES(13,7,19,27);
                CREATE TABLE chunked_uploads(
                    upload_id TEXT PRIMARY KEY, course_id INTEGER, teacher_id INTEGER,
                    file_name TEXT, file_size INTEGER, chunk_size INTEGER, total_chunks INTEGER,
                    temp_dir TEXT, description TEXT, is_public TEXT, is_teacher_resource TEXT,
                    class_offering_id INTEGER, received_chunks TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'uploading'
                );
                CREATE TABLE course_files(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id INTEGER NOT NULL, file_name TEXT NOT NULL, file_hash TEXT NOT NULL,
                    file_size INTEGER NOT NULL, is_public TEXT, is_teacher_resource TEXT,
                    description TEXT, original_link TEXT, uploaded_by_teacher_id INTEGER,
                    owner_role TEXT, owner_user_pk INTEGER, scope_level TEXT,
                    class_offering_id INTEGER, class_id INTEGER,
                    school_code TEXT, school_name TEXT, college TEXT, department TEXT,
                    published_at TEXT, updated_at TEXT
                );
                """
            )
        self.patches = [
            patch.object(routes, "get_db_connection", self.connect),
            patch.object(file_service, "GLOBAL_FILES_DIR", self.root / "files"),
            patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()),
            patch.object(routes, "bind_global_file_references", lambda conn, hashes: tuple(hashes)),
            patch.object(routes, "broadcast_file_update", AsyncMock()),
            patch("classroom_app.core.ai_client.post", AsyncMock(side_effect=RuntimeError("offline"))),
        ]
        for p in self.patches:
            p.start()
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_teacher] = lambda: {"id": 7, "role": "teacher"}
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def seed_upload(self, *, is_public: str, is_teacher_resource: str, class_offering_id=13):
        temp_dir = self.root / "chunks"
        temp_dir.mkdir(exist_ok=True)
        (temp_dir / "chunk_000000").write_bytes(b"classroom notes")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO chunked_uploads("
                " upload_id, course_id, teacher_id, file_name, file_size, chunk_size,"
                " total_chunks, temp_dir, description, is_public, is_teacher_resource,"
                " class_offering_id, received_chunks, status"
                ") VALUES ('u1',19,7,'notes.html',15,15,1,?,'',?,?,?,'[0]','uploading')",
                (str(temp_dir), is_public, is_teacher_resource, class_offering_id),
            )
        return temp_dir

    def complete(self):
        response = self.client.post("/api/files/upload/complete", json={"upload_id": "u1"})
        self.assertEqual(200, response.status_code, response.text)
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM course_files ORDER BY id DESC LIMIT 1").fetchone())

    def test_text_false_flag_does_not_force_private_scope(self):
        self.seed_upload(is_public="true", is_teacher_resource="false")

        row = self.complete()

        self.assertEqual("classroom", row["scope_level"])
        self.assertEqual(13, row["class_offering_id"])
        self.assertEqual(27, row["class_id"])
        self.assertIsNotNone(row["published_at"])

    def test_text_false_public_flag_keeps_file_unpublished(self):
        self.seed_upload(is_public="false", is_teacher_resource="false")

        row = self.complete()

        self.assertEqual("private", row["scope_level"])
        self.assertIsNone(row["published_at"])

    def test_numeric_flags_also_scope_to_the_classroom(self):
        self.seed_upload(is_public="1", is_teacher_resource="0")

        row = self.complete()

        self.assertEqual("classroom", row["scope_level"])
        self.assertIsNotNone(row["published_at"])


class DbFlagCoercionTests(unittest.TestCase):
    def test_database_flags_are_real_booleans(self):
        for value in ("false", "0", "no", "", 0, False, None):
            self.assertFalse(routes._coerce_db_flag(value), value)
        for value in ("true", "TRUE", "1", "yes", "on", 1, True):
            self.assertTrue(routes._coerce_db_flag(value), value)


if __name__ == "__main__":
    unittest.main()
