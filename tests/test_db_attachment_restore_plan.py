import hashlib
import json
import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_attachment_restore_plan


class DatabaseAttachmentRestorePlanTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_attachment_restore_plan.TEMP_ROOT / "unit-db-attachment-restore-plan"
        self.source_root = db_attachment_restore_plan.TEMP_ROOT / "unit-db-attachment-restore-source"
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.data_root = self.source_root / "data"
        self.source_db = self.source_root / "source.db"
        self.payload = b"student answer"
        self.file_hash = hashlib.sha256(self.payload).hexdigest()
        self.file_size = len(self.payload)
        self._make_source_db()
        self.remediation_report = self.source_root / "remediation.json"
        self._write_remediation_report()

    def tearDown(self):
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def _make_source_db(self):
        conn = sqlite3.connect(self.source_db)
        try:
            conn.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("CREATE TABLE assignments (id INTEGER PRIMARY KEY, course_id INTEGER, title TEXT)")
            conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, assignment_id INTEGER, student_pk_id INTEGER)")
            conn.execute(
                """
                CREATE TABLE submission_files (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER,
                    original_filename TEXT,
                    relative_path TEXT,
                    stored_path TEXT,
                    file_hash TEXT,
                    file_size INTEGER
                )
                """
            )
            conn.execute("INSERT INTO courses (id, name) VALUES (3, 'Network')")
            conn.execute("INSERT INTO assignments (id, course_id, title) VALUES (5, 3, 'Midterm')")
            conn.execute("INSERT INTO submissions (id, assignment_id, student_pk_id) VALUES (79, 5, 272)")
            conn.execute(
                """
                INSERT INTO submission_files
                    (id, submission_id, original_filename, relative_path, stored_path, file_hash, file_size)
                VALUES
                    (182, 79, 'answer.doc', 'answer.doc', 'F:\\lanshare\\homework_submissions\\1\\12\\272\\answer.doc', ?, ?)
                """,
                (self.file_hash, self.file_size),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_remediation_report(self):
        self.remediation_report.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "source_db": str(self.source_db),
                    "files": {
                        "missing_submission_files": [
                            {
                                "id": 182,
                                "submission_id": 79,
                                "original_filename": "answer.doc",
                                "relative_path": "answer.doc",
                                "stored_path": "F:\\lanshare\\homework_submissions\\1\\12\\272\\answer.doc",
                                "file_hash": self.file_hash,
                                "file_size": self.file_size,
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_plan_finds_trusted_candidate_but_stays_blocked_until_restored_or_accepted(self):
        search_root = self.source_root / "backup"
        search_root.mkdir(parents=True)
        (search_root / "answer.doc").write_bytes(self.payload)

        report = db_attachment_restore_plan.build_attachment_restore_plan(
            self.runtime_root,
            remediation_report=self.remediation_report,
            source_db=self.source_db,
            data_root=self.data_root,
            search_roots=[search_root],
        )

        self.assertEqual("blocked", report["status"])
        self.assertEqual(1, report["missing_count"])
        self.assertEqual(1, report["items"][0]["trusted_candidate_count"])
        self.assertFalse(report["cutover_effect"]["file_blocker_cleared"])
        self.assertFalse(report["safety"]["production_data_modified"])
        template = json.loads(Path(report["exception_template"]).read_text(encoding="utf-8"))
        self.assertEqual("sqlite-to-postgresql-cutover-missing-submission-files", template["scope"])
        self.assertEqual(1, template["manifest_version"])
        self.assertEqual([182], template["accepted_missing_submission_file_ids"])
        self.assertEqual(1, len(template["missing_submission_files"]))
        self.assertEqual("Midterm", template["missing_submission_files"][0]["assignment_title"])
        self.assertEqual("Network", template["missing_submission_files"][0]["course_name"])
        self.assertEqual(self.file_hash, template["missing_submission_files"][0]["expected_sha256"])
        self.assertFalse(template["required_acknowledgements"]["database_records_will_not_be_deleted_to_hide_missing_files"])

    def test_valid_exception_manifest_clears_file_blocker_without_modifying_data(self):
        manifest = self.source_root / "exception.json"
        manifest.write_text(
            json.dumps(
                {
                    "scope": "sqlite-to-postgresql-cutover-missing-submission-files",
                    "manifest_version": 1,
                    "approved_by": "owner",
                    "approved_at": "2026-06-05",
                    "reason": "Original historical attachment is unavailable after backup search.",
                    "business_acknowledgement": "Teacher accepts that this old attachment cannot be opened after cutover.",
                    "required_acknowledgements": {
                        "original_files_unavailable_after_search": True,
                        "database_records_will_not_be_deleted_to_hide_missing_files": True,
                        "historical_attachments_may_remain_unopenable_after_cutover": True,
                        "cutover_can_continue_without_restoring_these_specific_files": True,
                    },
                    "accepted_missing_submission_file_ids": [182],
                }
            ),
            encoding="utf-8",
        )

        report = db_attachment_restore_plan.build_attachment_restore_plan(
            self.runtime_root,
            remediation_report=self.remediation_report,
            source_db=self.source_db,
            data_root=self.data_root,
            exception_manifest=manifest,
        )

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["exception_manifest"]["valid"])
        self.assertTrue(report["cutover_effect"]["accepted_exception_manifest_valid"])
        self.assertTrue(report["cutover_effect"]["file_blocker_cleared"])
        self.assertFalse(report["safety"]["filesystem_modified"])

    def test_exception_manifest_requires_risk_acknowledgements(self):
        manifest = self.source_root / "exception.json"
        manifest.write_text(
            json.dumps(
                {
                    "scope": "sqlite-to-postgresql-cutover-missing-submission-files",
                    "manifest_version": 1,
                    "approved_by": "owner",
                    "approved_at": "2026-06-05",
                    "reason": "Original historical attachment is unavailable after backup search.",
                    "business_acknowledgement": "Teacher accepts that this old attachment cannot be opened after cutover.",
                    "required_acknowledgements": {
                        "original_files_unavailable_after_search": True,
                    },
                    "accepted_missing_submission_file_ids": [182],
                }
            ),
            encoding="utf-8",
        )

        report = db_attachment_restore_plan.build_attachment_restore_plan(
            self.runtime_root,
            remediation_report=self.remediation_report,
            source_db=self.source_db,
            data_root=self.data_root,
            exception_manifest=manifest,
        )

        self.assertEqual("blocked", report["status"])
        self.assertFalse(report["exception_manifest"]["valid"])
        self.assertIn(
            "database_records_will_not_be_deleted_to_hide_missing_files",
            report["exception_manifest"]["missing_acknowledgements"],
        )
        self.assertFalse(report["cutover_effect"]["file_blocker_cleared"])

    def test_already_restored_canonical_file_clears_file_blocker(self):
        target = self.data_root / "files" / "submissions" / "3" / "5" / "272" / "answer.doc"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.payload)

        report = db_attachment_restore_plan.build_attachment_restore_plan(
            self.runtime_root,
            remediation_report=self.remediation_report,
            source_db=self.source_db,
            data_root=self.data_root,
        )

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["items"][0]["already_restored"])
        self.assertEqual(str(target), report["items"][0]["canonical_target_path"])
        self.assertTrue(report["cutover_effect"]["all_missing_files_restored"])


if __name__ == "__main__":
    unittest.main()
