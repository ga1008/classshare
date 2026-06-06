import hashlib
import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_file_integrity, db_inventory


class DatabaseFileIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_inventory.TEMP_ROOT / "unit-db-file-integrity"
        self.source_root = db_inventory.TEMP_ROOT / "unit-db-file-integrity-source"
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def tearDown(self):
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def _write_global_file(self, data_root: Path, payload: bytes) -> str:
        file_hash = hashlib.sha256(payload).hexdigest()
        target = data_root / "media" / "blobs" / "sha256" / file_hash[:2] / file_hash[2:4] / file_hash
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return file_hash

    def _make_source_db(self) -> tuple[Path, Path, Path]:
        self.source_root.mkdir(parents=True, exist_ok=True)
        data_root = self.source_root / "data"
        repo_root = self.source_root
        submission_path = data_root / "files" / "submissions" / "1" / "2" / "3" / "answer.txt"
        submission_path.parent.mkdir(parents=True, exist_ok=True)
        submission_path.write_text("answer", encoding="utf-8")
        percent_encoded_path = data_root / "files" / "submissions" / "1" / "2" / "3" / "ipconfig %25USERPROFILE%25.txt"
        percent_encoded_path.write_text("ipconfig", encoding="utf-8")
        global_hash = self._write_global_file(data_root, b"global")
        signature_hash = hashlib.sha256(b"signature").hexdigest()
        signature_path = data_root / "media" / "signatures" / "sha256" / signature_hash[:2] / signature_hash[2:4] / f"{signature_hash}.png"
        signature_path.parent.mkdir(parents=True, exist_ok=True)
        signature_path.write_bytes(b"signature")
        orphan_path = data_root / "files" / "submissions" / "orphan.txt"
        orphan_path.write_text("orphan", encoding="utf-8")

        source_db = self.source_root / "source.db"
        conn = sqlite3.connect(source_db)
        try:
            conn.execute(
                """
                CREATE TABLE submission_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stored_path TEXT,
                    relative_path TEXT,
                    original_filename TEXT,
                    file_hash TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE app_feedback_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT,
                    original_filename TEXT,
                    file_size INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE electronic_signatures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT,
                    file_ext TEXT,
                    stored_path TEXT,
                    file_size INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE textbooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attachment_path TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO submission_files (stored_path, relative_path, original_filename, file_hash) VALUES (?, ?, ?, ?)",
                (str(submission_path), "answer.txt", "answer.txt", hashlib.sha256(b"answer").hexdigest()),
            )
            conn.execute(
                "INSERT INTO submission_files (stored_path, relative_path, original_filename) VALUES (?, ?, ?)",
                (str(data_root / "files" / "submissions" / "missing.txt"), "missing.txt", "missing.txt"),
            )
            conn.execute(
                "INSERT INTO submission_files (stored_path, relative_path, original_filename) VALUES (?, ?, ?)",
                (
                    str(data_root / "files" / "submissions" / "1" / "2" / "3" / "ipconfig %USERPROFILE%.txt"),
                    "ipconfig %USERPROFILE%.txt",
                    "ipconfig %USERPROFILE%.txt",
                ),
            )
            conn.execute(
                "INSERT INTO app_feedback_attachments (file_hash, original_filename, file_size) VALUES (?, ?, ?)",
                (global_hash, "global.bin", 6),
            )
            conn.execute(
                "INSERT INTO electronic_signatures (file_hash, file_ext, stored_path, file_size) VALUES (?, ?, ?, ?)",
                (signature_hash, ".png", str(signature_path.relative_to(data_root / "media" / "signatures" / "sha256")), 9),
            )
            conn.commit()
        finally:
            conn.close()
        return source_db, data_root, repo_root

    def test_file_integrity_report_is_read_only_and_detects_missing_and_orphans(self):
        source_db, data_root, repo_root = self._make_source_db()

        report = db_file_integrity.build_file_integrity_report(
            self.runtime_root,
            source_db,
            data_root=data_root,
            repo_root=repo_root,
        )

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["safety"]["production_data_modified"])
        self.assertFalse(report["safety"]["filesystem_modified"])
        self.assertGreaterEqual(report["references_checked"], 4)
        self.assertEqual(1, report["missing_references"])
        self.assertGreaterEqual(report["orphan_files"]["submissions"]["orphan_files"], 1)

    def test_file_integrity_main_writes_reports(self):
        source_db, data_root, repo_root = self._make_source_db()
        json_output = self.runtime_root / "file-integrity.json"
        markdown_output = self.runtime_root / "file-integrity.md"

        exit_code = db_file_integrity.main(
            [
                "--runtime-root",
                str(self.runtime_root),
                "--source-db",
                str(source_db),
                "--data-root",
                str(data_root),
                "--repo-root",
                str(repo_root),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(json_output.exists())
        self.assertTrue(markdown_output.exists())
        self.assertIn("Filesystem modified: `False`", markdown_output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
