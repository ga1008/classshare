import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

from classroom_app.services import blog_opportunity_service as opportunities


class BlogOpportunityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE blog_posts (id INTEGER PRIMARY KEY, title TEXT DEFAULT '就业机会');
            CREATE TABLE blog_opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL UNIQUE,
                employer_name TEXT NOT NULL DEFAULT '',
                opportunity_type TEXT NOT NULL DEFAULT 'campus_recruitment',
                positions_text TEXT NOT NULL DEFAULT '',
                regions_json TEXT NOT NULL DEFAULT '[]',
                city TEXT NOT NULL DEFAULT '',
                target_groups_json TEXT NOT NULL DEFAULT '[]',
                education_text TEXT NOT NULL DEFAULT '',
                majors_json TEXT NOT NULL DEFAULT '[]',
                headcount_text TEXT NOT NULL DEFAULT '',
                compensation_text TEXT NOT NULL DEFAULT '',
                application_method TEXT NOT NULL DEFAULT '',
                application_url TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source_domain TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                source_level TEXT NOT NULL DEFAULT 'C',
                published_at TEXT,
                deadline_at TEXT,
                last_verified_at TEXT,
                expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                extraction_confidence REAL NOT NULL DEFAULT 0,
                verification_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE blog_opportunity_user_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                user_identity TEXT NOT NULL,
                user_role TEXT NOT NULL,
                user_pk INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'saved',
                reminder_at TEXT,
                deadline_reminder_sent_at TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE (opportunity_id, user_identity)
            );
            INSERT INTO blog_posts (id) VALUES (11);
            """
        )
        self.engine_patch = patch.object(opportunities.blog_service, "get_configured_db_engine", return_value="sqlite")
        self.engine_patch.start()

    def tearDown(self):
        self.engine_patch.stop()
        self.conn.close()

    def test_upsert_normalizes_official_source_and_deadline(self):
        item = opportunities.upsert_opportunity_for_post(
            self.conn,
            11,
            {
                "employer_name": "南宁市人力资源服务中心",
                "regions": ["广西", "南宁", "南宁"],
                "deadline_at": "2026年8月5日",
                "application_url": "javascript:alert(1)",
                "extraction_confidence": 1.5,
            },
            source_url="https://rsj.nanning.gov.cn/job/11",
            source_name="南宁人社",
        )

        self.assertEqual("A", item["source_level"])
        self.assertTrue(item["is_official"])
        self.assertEqual("https://rsj.nanning.gov.cn/job/11", item["application_url"])
        self.assertEqual(["广西", "南宁"], item["regions"])
        self.assertEqual("2026-08-05T23:59:59", item["deadline_at"])
        self.assertEqual(1.0, item["extraction_confidence"])

    def test_status_expiry_and_user_workflow_are_closed_loop(self):
        item = opportunities.upsert_opportunity_for_post(
            self.conn,
            11,
            {"deadline_at": "2026-07-01", "positions_text": "毕业生岗位"},
            source_url="https://example.com/jobs/11",
        )
        expired = opportunities.refresh_opportunity_statuses(
            self.conn,
            now=datetime(2026, 7, 15, 12, 0, 0),
        )
        self.assertEqual(1, expired)
        self.assertEqual("expired", self.conn.execute("SELECT status FROM blog_opportunities").fetchone()[0])

        saved = opportunities.set_opportunity_user_state(
            self.conn,
            {"id": 9, "role": "student"},
            item["id"],
            state="applied",
            notes="已提交网申",
        )
        self.assertEqual("applied", saved["state"])
        removed = opportunities.set_opportunity_user_state(
            self.conn,
            {"id": 9, "role": "student"},
            item["id"],
            state="none",
        )
        self.assertIsNone(removed["state"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM blog_opportunity_user_states").fetchone()[0])

    def test_due_deadline_reminder_is_sent_once(self):
        item = opportunities.upsert_opportunity_for_post(
            self.conn,
            11,
            {"deadline_at": "2026-07-17", "employer_name": "测试单位"},
            source_url="https://example.com/jobs/11",
        )
        opportunities.set_opportunity_user_state(
            self.conn,
            {"id": 9, "role": "student"},
            item["id"],
            state="saved",
        )
        with patch.object(opportunities, "notify_opportunity_deadline") as notify:
            first = opportunities.notify_due_opportunity_deadlines(
                self.conn, now=datetime(2026, 7, 15, 9, 0, 0)
            )
            second = opportunities.notify_due_opportunity_deadlines(
                self.conn, now=datetime(2026, 7, 15, 10, 0, 0)
            )
        self.assertEqual(1, first)
        self.assertEqual(0, second)
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
