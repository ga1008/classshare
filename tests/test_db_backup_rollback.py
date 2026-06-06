import shutil
import sqlite3
import unittest
import json

from tools import db_backup_rollback


class DatabaseBackupRollbackTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_backup_rollback.TEMP_ROOT / "unit-db-backup-rollback"
        self.source_root = db_backup_rollback.TEMP_ROOT / "unit-db-backup-rollback-source"
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.source_db = self.source_root / "source.db"
        conn = sqlite3.connect(self.source_db)
        try:
            conn.execute("CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO teachers (name) VALUES ('Teacher')")
            conn.execute("INSERT INTO students (name) VALUES ('Student')")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def test_backup_rollback_drill_copies_and_restores_sqlite_db(self):
        report = db_backup_rollback.run_backup_rollback_drill(
            self.runtime_root,
            source_db=self.source_db,
        )

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["sqlite_restore_drill_executed"])
        self.assertFalse(report["postgres_dump_drill_executed"])
        self.assertFalse(report["postgres_restore_drill_executed"])
        self.assertTrue(report["key_counts_match"])
        self.assertFalse(report["safety"]["production_data_modified"])
        self.assertEqual("ok", report["backup_snapshot"]["quick_check"])
        self.assertEqual("ok", report["restore_snapshot"]["quick_check"])
        self.assertEqual(1, report["restore_snapshot"]["key_counts"]["teachers"])

    def test_runtime_root_must_stay_under_codex_temp(self):
        unsafe = db_backup_rollback.REPO_ROOT / "data" / "backup-drill"

        with self.assertRaises(ValueError):
            db_backup_rollback.resolve_runtime_root(unsafe)

    def test_postgres_drill_report_marks_dump_and_restore_executed(self):
        postgres_report = self.source_root / "postgres-drill.json"
        postgres_report.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "postgres_dump_executed": True,
                    "postgres_restore_executed": True,
                    "restore_table_count": 3,
                }
            ),
            encoding="utf-8",
        )

        report = db_backup_rollback.run_backup_rollback_drill(
            self.runtime_root,
            source_db=self.source_db,
            postgres_drill_report=postgres_report,
        )

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["postgres_dump_drill_executed"])
        self.assertTrue(report["postgres_restore_drill_executed"])
        self.assertEqual(3, report["postgres_drill_report"]["restore_table_count"])


if __name__ == "__main__":
    unittest.main()
