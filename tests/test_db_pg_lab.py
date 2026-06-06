import shutil
import sqlite3
import unittest
import json
from pathlib import Path

from tools import db_pg_lab


class DatabasePostgresLabTests(unittest.TestCase):
    def setUp(self):
        self.lab_root = db_pg_lab.TEMP_ROOT / "unit-pg-migration-lab"
        self.source_root = db_pg_lab.TEMP_ROOT / "unit-pg-migration-lab-source"
        for path in (self.lab_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.source_db = self.source_root / "source.db"
        conn = sqlite3.connect(self.source_db)
        try:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO sample (name) VALUES ('alpha')")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        for path in (self.lab_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def test_prepare_lab_copies_sqlite_db_without_using_source_as_target(self):
        report = db_pg_lab.prepare_lab(self.lab_root, source_db=self.source_db, clean=True)

        copied_db = Path(report["copied_db"])
        self.assertTrue(copied_db.exists())
        self.assertNotEqual(self.source_db.resolve(), copied_db.resolve())
        self.assertTrue(report["safety"]["lab_root_under_codex_temp"])
        self.assertFalse(report["safety"]["production_data_modified"])
        self.assertEqual("ok", report["sqlite_copy"]["quick_check"])
        conn = sqlite3.connect(copied_db)
        try:
            row = conn.execute("SELECT name FROM sample WHERE id = 1").fetchone()
        finally:
            conn.close()
        self.assertEqual(("alpha",), row)

    def test_lab_root_must_stay_under_codex_temp(self):
        unsafe = db_pg_lab.REPO_ROOT / "data" / "pg-migration-lab"

        with self.assertRaises(ValueError):
            db_pg_lab.resolve_lab_root(unsafe)

    def test_cleanup_plan_is_limited_to_safe_lab_root(self):
        report = db_pg_lab.cleanup_plan(self.lab_root)

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["safe_to_delete"])
        self.assertFalse(report["safety"]["production_data_modified"])

    def test_environment_report_redacts_database_url(self):
        report = db_pg_lab.collect_environment(
            self.lab_root,
            database_url="postgresql://lanshare@127.0.0.1:55432/lanshare_lab",
        )

        self.assertTrue(report["database_url_configured"])
        self.assertNotIn("lanshare@", report["database_url_redacted"])
        self.assertEqual("ok", report["status"])

    def test_summary_marks_actual_postgres_load_when_drill_report_passes(self):
        reports_dir = self.lab_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "remote-postgres-load-drill.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "schema_loaded": True,
                    "data_loaded": True,
                    "constraints_loaded": True,
                    "postgres_dump_executed": True,
                    "postgres_restore_executed": True,
                }
            ),
            encoding="utf-8",
        )

        report = db_pg_lab.summarize_lab(self.lab_root)

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["postgres_target"]["actual_postgres_data_load_executed"])
        self.assertTrue(report["postgres_target"]["postgres_restore_executed"])


if __name__ == "__main__":
    unittest.main()
