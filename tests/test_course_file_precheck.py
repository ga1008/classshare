from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from classroom_app.routers import files as routes
from classroom_app.services import file_service


ROOT = Path(__file__).resolve().parents[1]


class CourseFilePrecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-course-precheck-")
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite"
        self.queries = []
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE teachers(id INTEGER PRIMARY KEY,is_super_admin INTEGER,is_active INTEGER);
                INSERT INTO teachers VALUES(7,0,1),(8,0,1);
                CREATE TABLE courses(id INTEGER PRIMARY KEY,created_by_teacher_id INTEGER);
                INSERT INTO courses VALUES(1,7),(2,8),(3,7);
                CREATE TABLE class_offerings(id INTEGER,teacher_id INTEGER,course_id INTEGER);
                CREATE TABLE course_files(id INTEGER PRIMARY KEY,course_id INTEGER,file_name TEXT,file_size INTEGER,
                    file_hash TEXT,description TEXT,original_link TEXT,uploaded_at TEXT,
                    owner_role TEXT,owner_user_pk INTEGER,scope_level TEXT,uploaded_by_teacher_id INTEGER);
            """)
        self.patches = [patch.object(routes, "get_db_connection", self.connect),
                        patch.object(file_service, "GLOBAL_FILES_DIR", self.root / "files"),
                        patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ())]
        for p in self.patches:
            p.start()
        self.saved = file_service.store_file_object_globally(io.BytesIO(b"course notes"))
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
        conn.set_trace_callback(self.queries.append)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def seed(self, *, course=1, owner=7, scope="private", digest=None):
        with self.connect() as conn:
            conn.execute("INSERT INTO course_files VALUES(1,?,'notes.pdf',?,?,'private description','https://example.invalid/','before','teacher',?,?,?)",
                         (course, self.saved["size"], digest or self.saved["hash"], owner, scope, owner))

    def check(self, **changes):
        body = {"file_name": "notes.pdf", "file_size": self.saved["size"], "course_id": 1, "file_hash": self.saved["hash"]}
        body.update(changes)
        self.queries.clear()
        return self.client.post("/api/files/check", json=body)

    def test_legacy_filename_size_check_never_leaks_or_copies_other_course(self):
        self.seed(course=2, owner=8)
        response = self.check(file_hash=None)
        self.assertEqual(200, response.status_code)
        self.assertEqual({"exists": False}, response.json())
        self.assertFalse(any("FROM course_files" in query for query in self.queries))
        with self.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM course_files").fetchone()[0])

    def test_authorized_exact_existing_file_is_a_read_only_hit(self):
        self.seed()
        response = self.check(file_hash=self.saved["hash"].upper())
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["exists"])
        self.assertTrue(response.json()["in_current_course"])
        self.assertFalse(response.json()["linked"])
        self.assertFalse(any(query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in self.queries))

    def test_destination_course_ownership_does_not_bypass_source_privacy(self):
        self.seed(owner=8, scope="private")
        self.assertEqual({"exists": False}, self.check().json())

    def test_existing_public_access_semantics_are_preserved(self):
        self.seed(owner=8, scope="public")
        self.assertTrue(self.check().json()["exists"])

    def test_accessible_file_in_another_owned_course_is_not_automatically_republished(self):
        self.seed(course=3)
        self.assertEqual({"exists": False}, self.check().json())
        with self.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM course_files WHERE course_id=1").fetchone()[0])

    def test_same_name_and_size_do_not_replace_content_identity(self):
        self.seed(digest="a" * 64)
        self.assertEqual({"exists": False}, self.check().json())

    def test_missing_blob_continues_upload(self):
        self.seed()
        Path(self.saved["path"]).unlink()
        self.assertEqual({"exists": False}, self.check().json())

    def test_destination_permission_and_hash_validation(self):
        self.assertEqual(403, self.check(course_id=2, file_hash=None).status_code)
        self.assertEqual(422, self.check(file_hash="../../private").status_code)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the existing uploader contract")
    def test_legacy_client_runs_full_upload_after_negative_check(self):
        script = r"""
const fs = require('fs'), vm = require('vm');
const calls = [], chunks = [], completed = [], errors = [];
const sandbox = {console, Date, setTimeout, apiFetch: async (url) => {
  calls.push(url);
  if (url.endsWith('/check')) return {exists:false};
  if (url.endsWith('/init')) return {upload_id:'probe',chunk_size:4,total_chunks:1};
  if (url.endsWith('/complete')) return {status:'success'};
  throw new Error('Unexpected URL');
}};
let source = fs.readFileSync('static/js/upload.js','utf8').replace(/^import .*;\s*$/gm,'').replace('export class ChunkedUploader','class ChunkedUploader');
vm.runInNewContext(source+'\nglobalThis.Uploader=ChunkedUploader;',sandbox);
const uploader = new sandbox.Uploader({name:'notes.pdf',size:4,slice:()=>({})},1,{onComplete:x=>completed.push(x),onError:x=>errors.push(String(x))});
uploader._uploadChunk = async index => { chunks.push(index); };
uploader.start().then(()=>console.log(JSON.stringify({calls,chunks,completed,errors}))).catch(err=>{console.error(err);process.exitCode=1;});
"""
        output = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15, check=True)
        result = json.loads(output.stdout)
        self.assertEqual(["/api/files/check", "/api/files/upload/init", "/api/files/upload/complete"], result["calls"])
        self.assertEqual([0], result["chunks"])
        self.assertEqual([], result["errors"])
        self.assertEqual(False, result["completed"][0]["skipped"])


if __name__ == "__main__":
    unittest.main()
