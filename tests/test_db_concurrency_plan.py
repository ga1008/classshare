import shutil
import sqlite3
import unittest
from pathlib import Path

from tools import db_concurrency_plan, db_inventory


class DatabaseConcurrencyPlanTests(unittest.TestCase):
    def setUp(self):
        self.runtime_root = db_inventory.TEMP_ROOT / "unit-db-concurrency-plan"
        self.source_root = db_inventory.TEMP_ROOT / "unit-db-concurrency-plan-source"
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
                CREATE TABLE email_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    next_attempt_at TEXT,
                    locked_at TEXT,
                    updated_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE agent_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    started_at TEXT,
                    updated_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("INSERT INTO email_outbox (status, created_at) VALUES ('queued', '2026-01-01')")
            conn.execute("INSERT INTO agent_tasks (status, created_at) VALUES ('queued', '2026-01-01')")
            conn.commit()
        finally:
            conn.close()
        return source_db

    def test_concurrency_plan_reports_queue_candidates_and_sql(self):
        report = db_concurrency_plan.build_concurrency_plan(self.runtime_root, self._make_source_db())

        self.assertEqual("ok", report["status"])
        self.assertFalse(report["safety"]["production_data_modified"])
        by_table = {item["table"]: item for item in report["queue_candidates"]}
        self.assertIn("email_outbox", by_table)
        self.assertIn("agent_tasks", by_table)
        self.assertIn("FOR UPDATE SKIP LOCKED", by_table["email_outbox"]["postgres_claim_sql"])
        self.assertIn("CREATE UNIQUE INDEX", by_table["agent_tasks"]["postgres_singleton_guard_sql"])

    def test_concurrency_plan_main_writes_reports(self):
        json_output = self.runtime_root / "concurrency-plan.json"
        markdown_output = self.runtime_root / "concurrency-plan.md"

        exit_code = db_concurrency_plan.main(
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
        self.assertIn("SKIP LOCKED", markdown_output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
