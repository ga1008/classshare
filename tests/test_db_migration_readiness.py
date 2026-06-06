import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_inventory, db_migration_readiness


class DatabaseMigrationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_inventory.TEMP_ROOT / "unit-db-migration-readiness"
        self.source_root = db_inventory.TEMP_ROOT / "unit-db-migration-readiness-source"
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def tearDown(self):
        for path in (self.runtime_root, self.source_root):
            if path.exists():
                shutil.rmtree(path)

    def _make_source_db(self) -> Path:
        self.source_root.mkdir(parents=True, exist_ok=True)
        source_db = self.source_root / "source.db"
        conn = sqlite3.connect(source_db)
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT)")
            conn.execute(
                "CREATE TABLE child ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "parent_id INTEGER REFERENCES parent(id), "
                "metadata_json TEXT, "
                "status TEXT)"
            )
            conn.execute("INSERT INTO parent (status) VALUES ('active')")
            conn.execute("INSERT INTO child (parent_id, metadata_json, status) VALUES (99, '{bad json', 'queued')")
            conn.commit()
        finally:
            conn.close()
        return source_db

    def test_readiness_report_detects_fk_and_json_risks(self):
        report = db_migration_readiness.build_readiness_report(self.runtime_root, self._make_source_db())

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["safety"]["production_data_modified"])
        self.assertEqual("ok", report["quick_check"])
        self.assertEqual(1, report["foreign_key_violations"])
        issue_ids = {item["id"] for item in report["blocking_issues"]}
        self.assertIn("MR-R002", issue_ids)
        self.assertIn("MR-R003", issue_ids)
        self.assertTrue(any(item["table"] == "child" for item in report["primary_key_maxima"]))

    def test_readiness_main_writes_reports(self):
        json_output = self.runtime_root / "readiness.json"
        markdown_output = self.runtime_root / "readiness.md"

        exit_code = db_migration_readiness.main(
            [
                "--runtime-root",
                str(self.runtime_root),
                "--source-db",
                str(self._make_source_db()),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(json_output.exists())
        self.assertTrue(markdown_output.exists())
        self.assertIn("Foreign key violations", markdown_output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
