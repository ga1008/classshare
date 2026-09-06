import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.services import file_service as files


class ResumeFileReferenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE course_files (file_hash TEXT)")
        self.conn.execute("CREATE TABLE resumes (source_file_hash TEXT)")
        self.conn.execute("CREATE TABLE resume_attachments (file_hash TEXT)")
        self.conn.execute("CREATE TABLE resume_personal_info (avatar_file_hash TEXT)")
        self.conn.execute("CREATE TABLE resume_versions (snapshot_json TEXT)")
        self.conn.execute("CREATE TABLE resume_candidates (payload_json TEXT)")
        self.conn.execute("CREATE TABLE resume_applications (resume_snapshot_json TEXT)")
        self.temp = tempfile.TemporaryDirectory(prefix="lanshare-career-blobs-")
        self.hash = hashlib.sha256(b"shared resume file").hexdigest()
        self.path = Path(self.temp.name) / self.hash
        self.path.write_bytes(b"shared resume file")
        self.paths = patch.object(files, "global_file_candidates", return_value=(self.path,))
        self.paths.start()

    def tearDown(self):
        self.paths.stop()
        self.conn.close()
        self.temp.cleanup()

    def test_each_resume_reference_protects_blob_after_course_delete(self):
        for table, column in (("resumes", "source_file_hash"), ("resume_attachments", "file_hash"), ("resume_personal_info", "avatar_file_hash")):
            with self.subTest(table=table):
                self.conn.execute(f"INSERT INTO {table} VALUES (?)", (self.hash,))
                self.conn.execute("INSERT INTO course_files VALUES (?)", (self.hash,))
                self.conn.execute("DELETE FROM course_files")
                self.assertEqual(1, files.count_global_file_references(self.conn, self.hash))
                self.assertFalse(asyncio.run(files.delete_global_file(self.hash, conn=self.conn)))
                self.assertTrue(self.path.is_file())
                self.conn.execute(f"DELETE FROM {table}")

    def test_immutable_snapshots_keep_files_after_profile_deletion(self):
        snapshot = json.dumps({"bundle": {"personal": {"avatar_file_hash": self.hash}}})
        for table in ("resume_versions", "resume_candidates", "resume_applications"):
            with self.subTest(table=table):
                self.conn.execute(f"INSERT INTO {table} VALUES (?)", (snapshot,))
                self.assertFalse(asyncio.run(files.delete_global_file(self.hash, conn=self.conn)))
                self.conn.execute(f"DELETE FROM {table}")
        self.assertFalse(asyncio.run(files.delete_global_file(self.hash, conn=self.conn)))
        self.assertTrue(self.path.exists())

    def test_deletion_rechecks_reference_added_after_old_count(self):
        self.assertEqual(0, files.count_global_file_references(self.conn, self.hash))
        self.conn.execute("INSERT INTO resume_attachments VALUES (?)", (self.hash,))
        self.assertFalse(asyncio.run(files.delete_global_file(self.hash, conn=self.conn)))
        self.assertTrue(self.path.is_file())

    def test_new_binding_rejects_file_already_collected(self):
        # Simulate an external/historical collector; request-time collection
        # now retains shared bytes until all legacy writers are audited.
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "重新上传"):
            files.lock_global_file_references(self.conn, (self.hash,))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM resume_attachments").fetchone()[0])

    def test_reference_lock_rejects_invalid_hash(self):
        with self.assertRaises(ValueError):
            files.lock_global_file_references(self.conn, ("../another-path",))


if __name__ == "__main__":
    unittest.main()
