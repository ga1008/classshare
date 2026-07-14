import sqlite3
import unittest
from unittest.mock import patch

from classroom_app.services import blog_news_crawler_service as crawler
from classroom_app.services import blog_section_service, blog_service


class BlogSectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE blog_sections (
                section_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                short_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT '•',
                accent_color TEXT NOT NULL DEFAULT '#2563eb',
                sort_order INTEGER NOT NULL DEFAULT 100,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                is_career INTEGER NOT NULL DEFAULT 0,
                allow_user_posts INTEGER NOT NULL DEFAULT 1,
                source_keywords_json TEXT NOT NULL DEFAULT '[]',
                source_templates_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT,
                sect_name TEXT,
                description TEXT
            );
            CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER);
            CREATE TABLE blog_posts (
                id INTEGER PRIMARY KEY,
                author_identity TEXT NOT NULL,
                author_role TEXT NOT NULL,
                author_user_pk INTEGER NOT NULL,
                author_display_name TEXT NOT NULL,
                author_display_mode TEXT NOT NULL DEFAULT 'real_name',
                author_avatar_hash TEXT DEFAULT '',
                author_avatar_mime TEXT DEFAULT '',
                section_key TEXT NOT NULL DEFAULT 'general',
                title TEXT NOT NULL,
                content_md TEXT NOT NULL DEFAULT '',
                summary TEXT DEFAULT '',
                cover_image_hash TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'published',
                visibility TEXT NOT NULL DEFAULT 'public',
                visible_class_id INTEGER,
                visible_user_identities_json TEXT DEFAULT '[]',
                allow_comments INTEGER NOT NULL DEFAULT 1,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                is_featured INTEGER NOT NULL DEFAULT 0,
                pinned_at TEXT,
                featured_at TEXT,
                hot_notified_at TEXT,
                view_count INTEGER NOT NULL DEFAULT 0,
                like_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                bookmark_count INTEGER NOT NULL DEFAULT 0,
                system_tags_json TEXT DEFAULT '[]',
                tags_json TEXT DEFAULT '[]',
                edited_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE blog_likes (
                id INTEGER PRIMARY KEY,
                target_type TEXT,
                target_id INTEGER,
                user_identity TEXT
            );
            CREATE TABLE blog_bookmarks (
                id INTEGER PRIMARY KEY,
                post_id INTEGER,
                user_identity TEXT
            );
            """
        )
        self.engine_patch = patch.object(blog_section_service, "get_configured_db_engine", return_value="sqlite")
        self.engine_patch.start()
        blog_section_service.ensure_default_blog_sections(self.conn)

    def tearDown(self):
        self.engine_patch.stop()
        self.conn.close()

    def test_default_sections_are_data_driven_and_career_has_regional_sources(self):
        sections = blog_section_service.list_blog_sections(self.conn, include_source_config=True)
        keys = [section["section_key"] for section in sections]

        self.assertEqual(["general", "technology", "humanities", "computer", "ai", "career"], keys)
        career = next(section for section in sections if section["section_key"] == "career")
        self.assertTrue(career["is_career"])
        self.assertGreaterEqual(len(career["source_keywords"]), 6)
        self.assertEqual(3, len(career["source_templates"]))
        self.assertIn("广西", " ".join(career["source_keywords"]))
        self.assertIn("珠三角", career["description"])

    def test_repeated_section_catalog_reads_do_not_start_noop_writes(self):
        before = self.conn.total_changes

        blog_section_service.list_blog_sections(self.conn)
        blog_section_service.list_blog_sections(self.conn)

        self.assertEqual(before, self.conn.total_changes)

    def test_keyword_plan_reserves_all_sections_and_extra_career_coverage(self):
        self.conn.execute(
            "INSERT INTO courses (id, name, sect_name, description) VALUES (1, 'Python 程序设计', '计算机', '')"
        )
        config = {
            "max_keywords": 8,
            "extra_keywords": [],
        }

        planned = crawler.load_course_news_keywords(self.conn, config)
        section_keys = [item["section_key"] for item in planned]

        for section_key in ("technology", "humanities", "computer", "ai", "career", "general"):
            self.assertIn(section_key, section_keys)
        self.assertGreaterEqual(section_keys.count("career"), 2)
        career_entry = next(item for item in planned if item["section_key"] == "career")
        sources = crawler._build_search_feed_urls(
            career_entry["keyword"],
            2,
            {},
            section_key="career",
            section_templates=career_entry["source_templates"],
        )
        source_names = {source.name for source in sources}
        self.assertIn("国家大学生就业服务平台", source_names)
        self.assertIn("广西公共就业与人才服务", source_names)
        self.assertIn("珠三角公共就业服务", source_names)

    def test_list_posts_filters_by_section_without_cross_section_leakage(self):
        for post_id, section_key, title in (
            (1, "ai", "AI 帖子"),
            (2, "humanities", "人文帖子"),
        ):
            self.conn.execute(
                """
                INSERT INTO blog_posts (
                    id, author_identity, author_role, author_user_pk, author_display_name,
                    section_key, title, content_md, summary, status, visibility, created_at, updated_at
                ) VALUES (?, 'assistant:0', 'assistant', 0, 'AI管家', ?, ?, '正文', '摘要', 'published', 'public',
                          '2026-07-15T08:00:00', '2026-07-15T08:00:00')
                """,
                (post_id, section_key, title),
            )

        result = blog_service.list_posts(
            self.conn,
            {"id": 0, "role": "assistant", "name": "AI管家"},
            section_key="ai",
        )

        self.assertEqual(1, result["total"])
        self.assertEqual("AI 帖子", result["posts"][0]["title"])
        self.assertEqual("ai", result["posts"][0]["section_key"])

    def test_selection_always_keeps_a_career_candidate_when_available(self):
        candidates = [
            {"id": 1, "section_key": "ai", "score": 100},
            {"id": 2, "section_key": "technology", "score": 90},
            {"id": 3, "section_key": "career", "score": 80},
        ]

        selected = crawler._balance_section_selection(candidates, candidates[:2], max_posts=2)

        self.assertEqual(2, len(selected))
        self.assertEqual("career", selected[0]["section_key"])
        self.assertEqual(2, len({item["section_key"] for item in selected}))


if __name__ == "__main__":
    unittest.main()
