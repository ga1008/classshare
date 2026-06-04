import sqlite3
import unittest
from datetime import datetime, timedelta

from classroom_app.services.background_task_ledger_service import build_background_task_ledger_snapshot


def _iso(minutes_ago: int = 0) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class BackgroundTaskLedgerTests(unittest.TestCase):
    def _build_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                status TEXT,
                feedback_md TEXT,
                grading_started_at TEXT,
                submitted_at TEXT
            );
            CREATE TABLE material_ai_import_records (
                id INTEGER PRIMARY KEY,
                parse_status TEXT,
                error_message TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE session_material_generation_tasks (
                id INTEGER PRIMARY KEY,
                status TEXT,
                error_message TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE private_message_ai_jobs (
                id INTEGER PRIMARY KEY,
                status TEXT,
                error_message TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE email_outbox (
                id INTEGER PRIMARY KEY,
                status TEXT,
                last_error TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE email_worker_heartbeats (
                worker_id TEXT PRIMARY KEY,
                status TEXT,
                queue_depth INTEGER,
                last_error TEXT,
                updated_at TEXT
            );
            CREATE TABLE blog_news_crawler_runs (
                id INTEGER PRIMARY KEY,
                status TEXT,
                error_message TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE blog_news_crawler_config (
                id INTEGER PRIMARY KEY,
                worker_id TEXT,
                worker_status TEXT,
                last_heartbeat_at TEXT
            );
            CREATE TABLE agent_tasks (
                id INTEGER PRIMARY KEY,
                status TEXT,
                error_message TEXT,
                worker_id TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO submissions (status, feedback_md, grading_started_at, submitted_at) VALUES (?, ?, ?, ?)",
            ("grading", "", _iso(300), _iso(300)),
        )
        conn.execute(
            "INSERT INTO submissions (status, feedback_md, grading_started_at, submitted_at) VALUES (?, ?, ?, ?)",
            ("grading_failed", "token=abc123 password=secret bad callback", _iso(10), _iso(10)),
        )
        conn.execute(
            "INSERT INTO material_ai_import_records VALUES (?, ?, ?, ?, ?, ?)",
            (1, "queued", "", _iso(20), "", _iso(20)),
        )
        conn.execute(
            "INSERT INTO material_ai_import_records VALUES (?, ?, ?, ?, ?, ?)",
            (2, "running", "", _iso(60), _iso(60), _iso(60)),
        )
        conn.execute(
            "INSERT INTO material_ai_import_records VALUES (?, ?, ?, ?, ?, ?)",
            (3, "ai_failed", "api_key=sk-demo import failed", _iso(1), _iso(1), _iso(1)),
        )
        conn.execute(
            "INSERT INTO session_material_generation_tasks VALUES (?, ?, ?, ?, ?, ?)",
            (1, "queued", "", _iso(5), "", _iso(5)),
        )
        conn.execute(
            "INSERT INTO private_message_ai_jobs VALUES (?, ?, ?, ?, ?, ?)",
            (1, "failed", "cookie=session leaked", _iso(3), _iso(3), _iso(3)),
        )
        conn.execute(
            "INSERT INTO email_outbox VALUES (?, ?, ?, ?, ?)",
            (1, "queued", "", _iso(2), _iso(2)),
        )
        conn.execute(
            "INSERT INTO email_worker_heartbeats VALUES (?, ?, ?, ?, ?)",
            ("mailer-1", "running", 1, "", _iso(0)),
        )
        conn.execute(
            "INSERT INTO blog_news_crawler_runs VALUES (?, ?, ?, ?, ?, ?)",
            (1, "running", "", _iso(10), _iso(10), _iso(10)),
        )
        conn.execute(
            "INSERT INTO blog_news_crawler_config VALUES (?, ?, ?, ?)",
            (1, "crawler-1", "polling", _iso(0)),
        )
        conn.execute(
            "INSERT INTO agent_tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "running", "", "agent-worker-1", _iso(40), _iso(40), _iso(40)),
        )
        conn.commit()
        return conn

    def test_ledger_collects_all_task_types_and_redacts_errors(self):
        conn = self._build_conn()
        snapshot = build_background_task_ledger_snapshot(
            conn,
            behavior_stats_provider=lambda: {
                "alive": True,
                "queue_depth": 7,
                "queue_capacity": 512,
                "dropped_count": 0,
            },
        )
        items = {item["task_type"]: item for item in snapshot["items"]}

        self.assertEqual(set(items), {
            "ai_grading",
            "material_ai_import",
            "session_material_generation",
            "private_message_ai_reply",
            "email_outbox",
            "blog_news_crawler",
            "agent_task",
            "behavior_write_pipeline",
        })
        self.assertEqual(items["material_ai_import"]["queue_depth"], 1)
        self.assertGreaterEqual(items["material_ai_import"]["stale_count"], 1)
        self.assertEqual(items["behavior_write_pipeline"]["queue_depth"], 7)
        combined_errors = " ".join(str(item.get("last_error") or "") for item in items.values())
        self.assertNotIn("abc123", combined_errors)
        self.assertNotIn("secret", combined_errors)
        self.assertNotIn("sk-demo", combined_errors)
        self.assertIn("[REDACTED]", combined_errors)

    def test_missing_tables_return_stable_items_instead_of_crashing(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        snapshot = build_background_task_ledger_snapshot(
            conn,
            behavior_stats_provider=lambda: {"alive": False, "queue_depth": 0, "queue_capacity": 512},
        )

        self.assertEqual(len(snapshot["items"]), 8)
        self.assertTrue(
            any(item["status"] == "missing_source" for item in snapshot["items"]),
            snapshot,
        )


if __name__ == "__main__":
    unittest.main()
