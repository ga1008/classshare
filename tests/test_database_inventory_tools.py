import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_inventory


class DatabaseInventoryToolTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_inventory.TEMP_ROOT / "unit-db-inventory"
        self.source_root = db_inventory.TEMP_ROOT / "unit-db-inventory-source"
        if self.runtime_root.exists():
            shutil.rmtree(self.runtime_root)
        if self.source_root.exists():
            shutil.rmtree(self.source_root)

    def tearDown(self):
        if self.runtime_root.exists():
            shutil.rmtree(self.runtime_root)
        if self.source_root.exists():
            shutil.rmtree(self.source_root)

    def _make_source_db(self) -> Path:
        self.source_root.mkdir(parents=True, exist_ok=True)
        source_db = self.source_root / "source.db"
        if source_db.exists():
            source_db.unlink()
        conn = sqlite3.connect(source_db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
            conn.execute(
                "CREATE TABLE child ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "parent_id INTEGER NOT NULL REFERENCES parent(id), "
                "name TEXT)"
            )
            conn.execute("INSERT INTO parent (name) VALUES (?)", ("p1",))
            conn.execute("INSERT INTO child (parent_id, name) VALUES (?, ?)", (1, "c1"))
            conn.commit()
        finally:
            conn.close()
        return source_db

    def test_runtime_root_must_stay_under_codex_temp(self):
        unsafe = db_inventory.REPO_ROOT / "data" / "inventory"

        with self.assertRaises(ValueError):
            db_inventory.resolve_runtime_root(str(unsafe))

    def test_inventory_copies_source_and_reports_no_production_mutation(self):
        source_db = self._make_source_db()

        report = db_inventory.build_inventory(self.runtime_root, source_db)

        self.assertEqual("ok", report["status"])
        self.assertTrue(report["safety"]["source_db_was_copied_with_sqlite_backup_api"])
        self.assertFalse(report["safety"]["production_data_modified"])
        self.assertNotEqual(str(source_db), report["copied_db"])
        self.assertEqual("ok", report["sqlite_snapshot"]["quick_check"])
        self.assertEqual(2, report["sqlite_snapshot"]["table_count"])
        self.assertEqual(0, report["sqlite_snapshot"]["foreign_key_violations"])
        self.assertGreaterEqual(report["code_inventory"]["scanned_python_files"], 1)

    def test_markdown_report_contains_safety_and_summary(self):
        source_db = self._make_source_db()
        report = db_inventory.build_inventory(self.runtime_root, source_db)

        text = db_inventory.markdown_report(report)

        self.assertIn("Production data modified: `False`", text)
        self.assertIn("Files with database coupling hits", text)
        self.assertIn("Foreign key violations", text)

    def test_table_map_and_risk_register_reports_are_generated(self):
        source_db = self._make_source_db()
        report = db_inventory.build_inventory(self.runtime_root, source_db)

        table_map = db_inventory.table_map_report(report)
        risk_register = db_inventory.risk_register_report(report)

        self.assertIn("LanShare Database Table Map", table_map)
        self.assertIn("`child`", table_map)
        self.assertIn("LanShare Database Risk Register", risk_register)
        self.assertIn("DB-R001", risk_register)


if __name__ == "__main__":
    unittest.main()
