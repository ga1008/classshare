import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_remediation_plan


class DatabaseRemediationPlanTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_remediation_plan.TEMP_ROOT / "unit-db-remediation-plan"
        self.source_root = db_remediation_plan.TEMP_ROOT / "unit-db-remediation-source"
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.data_root = self.source_root / "data"
        self.source_db = self.source_root / "source.db"
        conn = sqlite3.connect(self.source_db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute(
                """
                CREATE TABLE teacher_onboarding_state (
                    teacher_id INTEGER PRIMARY KEY REFERENCES teachers(id),
                    dismissed_at TEXT,
                    completed_at TEXT,
                    dismiss_reason TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER)")
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
            conn.execute("INSERT INTO teachers (id, name) VALUES (1, 'Teacher')")
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("INSERT INTO teacher_onboarding_state (teacher_id, updated_at) VALUES (99, '2026-01-01')")
            conn.execute("INSERT INTO submissions (id, assignment_id, student_pk_id) VALUES (7, 'A1', 3)")
            conn.execute(
                """
                INSERT INTO submission_files
                    (id, submission_id, original_filename, relative_path, stored_path, file_hash, file_size)
                VALUES
                    (5, 7, 'missing.txt', 'missing.txt', 'F:\\missing\\missing.txt', 'abc', 10)
                """
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def test_apply_to_copy_repairs_orphan_onboarding_without_touching_source(self):
        report = db_remediation_plan.build_remediation_plan(
            self.runtime_root,
            source_db=self.source_db,
            data_root=self.data_root,
            repo_root=self.source_root,
            apply_to_copy=True,
        )

        self.assertEqual("ok", report["status"])
        self.assertEqual(1, len(report["foreign_key"]["violations_before"]))
        self.assertEqual(0, len(report["foreign_key"]["violations_after"]))
        self.assertTrue(report["cutover_effect"]["foreign_key_blocker_cleared_on_copy"])
        self.assertEqual(1, report["files"]["manual_restore_required_count"])
        self.assertFalse(report["safety"]["production_data_modified"])

        conn = sqlite3.connect(self.source_db)
        try:
            remaining = conn.execute("SELECT COUNT(*) FROM teacher_onboarding_state WHERE teacher_id = 99").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(1, remaining)

    def test_runtime_root_must_stay_under_codex_temp(self):
        unsafe = db_remediation_plan.REPO_ROOT / "data" / "remediation"

        with self.assertRaises(ValueError):
            db_remediation_plan.resolve_runtime_root(unsafe)


if __name__ == "__main__":
    unittest.main()
