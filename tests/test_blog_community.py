import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import blog_community_service as community
from classroom_app.services import blog_section_service


class BlogCommunityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE blog_sections (
                section_key TEXT PRIMARY KEY, name TEXT NOT NULL, short_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT '•',
                accent_color TEXT NOT NULL DEFAULT '#2563eb', sort_order INTEGER NOT NULL DEFAULT 100,
                is_enabled INTEGER NOT NULL DEFAULT 1, is_career INTEGER NOT NULL DEFAULT 0,
                allow_user_posts INTEGER NOT NULL DEFAULT 1, source_keywords_json TEXT NOT NULL DEFAULT '[]',
                source_templates_json TEXT NOT NULL DEFAULT '[]', created_at TEXT, updated_at TEXT
            );
            CREATE TABLE blog_follows (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_identity TEXT NOT NULL, user_role TEXT NOT NULL,
                user_pk INTEGER NOT NULL, target_type TEXT NOT NULL, target_key TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_identity, target_type, target_key)
            );
            CREATE TABLE blog_posts (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE blog_comments (id INTEGER PRIMARY KEY, content_md TEXT);
            CREATE TABLE blog_opportunities (id INTEGER PRIMARY KEY, post_id INTEGER);
            CREATE TABLE blog_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT, target_type TEXT NOT NULL, target_id INTEGER NOT NULL,
                reporter_identity TEXT NOT NULL, reporter_role TEXT NOT NULL, reporter_user_pk INTEGER NOT NULL,
                reason_code TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                resolved_by_identity TEXT NOT NULL DEFAULT '', resolution_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, target_id, reporter_identity, status)
            );
            INSERT INTO blog_posts (id, title) VALUES (5, '一篇需要核验的文章');
            """
        )
        self.engine_patch = patch.object(blog_section_service, "get_configured_db_engine", return_value="sqlite")
        self.engine_patch.start()
        blog_section_service.ensure_default_blog_sections(self.conn)

    def tearDown(self):
        self.engine_patch.stop()
        self.conn.close()

    def test_follow_toggle_is_idempotent(self):
        user = {"id": 3, "role": "student"}
        followed = community.set_follow(
            self.conn, user, target_type="section", target_key="ai", following=True
        )
        repeated = community.set_follow(
            self.conn, user, target_type="section", target_key="ai", following=True
        )
        self.assertTrue(followed["following"])
        self.assertTrue(repeated["following"])
        self.assertEqual(1, len(community.list_follows(self.conn, user)))
        unfollowed = community.set_follow(
            self.conn, user, target_type="section", target_key="ai", following=False
        )
        self.assertFalse(unfollowed["following"])
        self.assertEqual([], community.list_follows(self.conn, user))

    def test_report_can_be_updated_and_resolved(self):
        reporter = {"id": 9, "role": "student"}
        first = community.create_report(
            self.conn,
            reporter,
            target_type="post",
            target_id=5,
            reason_code="false_information",
            details="来源已经撤回",
        )
        updated = community.create_report(
            self.conn,
            reporter,
            target_type="post",
            target_id=5,
            reason_code="job_scam",
            details="官方页面提示该招聘为虚假信息",
        )
        self.assertEqual(first["id"], updated["id"])
        pending = community.list_pending_reports(self.conn)
        self.assertEqual(1, len(pending))
        self.assertEqual("一篇需要核验的文章", pending[0]["target_title"])
        self.assertEqual("/blog?post=5", pending[0]["target_url"])
        result = community.resolve_report(
            self.conn,
            {"id": 1, "role": "teacher"},
            first["id"],
            status="resolved",
            notes="已隐藏并联系作者",
        )
        self.assertEqual("resolved", result["status"])
        self.assertEqual([], community.list_pending_reports(self.conn))


if __name__ == "__main__":
    unittest.main()
