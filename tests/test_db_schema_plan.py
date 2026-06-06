import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_inventory, db_schema_plan


class DatabaseSchemaPlanTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_inventory.TEMP_ROOT / "unit-db-schema-plan"
        self.source_root = db_inventory.TEMP_ROOT / "unit-db-schema-plan-source"
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
            conn.execute(
                """
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT,
                    created_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        return source_db

    def test_schema_plan_copies_source_and_builds_baseline(self):
        report = db_schema_plan.build_schema_plan(self.runtime_root, self._make_source_db())

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["safety"]["production_data_modified"])
        self.assertEqual("0001_sqlite_v4_baseline", report["baseline_migration"]["version"])
        self.assertEqual(1, report["table_count"])
        self.assertEqual(["jobs"], report["autoincrement_tables"])
        self.assertGreaterEqual(len(report["postgres_conversion_risks"]), 2)
        self.assertIn("schema_migrations", report["schema_migrations_sql"]["postgres"])

    def test_schema_plan_markdown_contains_cutover_relevant_summary(self):
        report = db_schema_plan.build_schema_plan(self.runtime_root, self._make_source_db())
        text = db_schema_plan.markdown_report(report)

        self.assertIn("LanShare Schema Baseline Plan", text)
        self.assertIn("Production data modified: `False`", text)
        self.assertIn("AUTOINCREMENT Tables", text)

    def test_schema_plan_main_writes_reports(self):
        json_output = self.runtime_root / "schema-plan.json"
        markdown_output = self.runtime_root / "schema-plan.md"

        exit_code = db_schema_plan.main(
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
        self.assertIn("production_data_modified", json_output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
