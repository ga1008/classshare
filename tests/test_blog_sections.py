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

        self.assertEqual(["career", "general", "technology", "computer", "ai", "humanities"], keys)
        general = next(section for section in sections if section["section_key"] == "general")
        self.assertEqual("杂谈", general["short_name"])
        self.assertIn("小说与叙事", general["source_keywords"])
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

    def test_legacy_default_metadata_upgrades_once_without_overwriting_custom_sections(self):
        self.conn.execute(
            """
            UPDATE blog_sections
            SET name = '校园与成长', short_name = '综合', description = '旧描述',
                sort_order = 10, source_keywords_json = '[]'
            WHERE section_key = 'general'
            """
        )
        self.conn.execute("UPDATE blog_sections SET sort_order = 60 WHERE section_key = 'career'")
        self.conn.execute("UPDATE blog_sections SET sort_order = 20 WHERE section_key = 'technology'")
        self.conn.execute("UPDATE blog_sections SET sort_order = 30 WHERE section_key = 'humanities'")
        self.conn.execute(
            "UPDATE blog_sections SET name = '我的 AI 频道', sort_order = 50 WHERE section_key = 'ai'"
        )

        blog_section_service.ensure_default_blog_sections(self.conn)
        sections = blog_section_service.list_blog_sections(self.conn, include_source_config=True)
        by_key = {item["section_key"]: item for item in sections}

        self.assertEqual("杂谈与故事", by_key["general"]["name"])
        self.assertEqual("旧描述", by_key["general"]["description"])
        self.assertIn("小说与叙事", by_key["general"]["source_keywords"])
        self.assertEqual(10, by_key["career"]["sort_order"])
        self.assertEqual(30, by_key["technology"]["sort_order"])
        self.assertEqual(60, by_key["humanities"]["sort_order"])
        self.assertEqual("我的 AI 频道", by_key["ai"]["name"])

        after_upgrade = self.conn.total_changes
        blog_section_service.ensure_default_blog_sections(self.conn)
        self.assertEqual(after_upgrade, self.conn.total_changes)

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

    def test_career_sources_are_prioritized_before_general_news(self):
        career_section = next(
            item
            for item in blog_section_service.DEFAULT_BLOG_SECTIONS
            if item["section_key"] == "career"
        )

        sources = crawler._effective_source_templates(
            {},
            section_key="career",
            section_templates=career_section["source_templates"],
        )

        self.assertEqual(
            [item["name"] for item in career_section["source_templates"]],
            [item["name"] for item in sources[:3]],
        )

    def test_career_relevance_rejects_news_without_job_intent_or_region(self):
        self.assertFalse(
            crawler._career_candidate_is_relevant(
                keyword="\u7ca4\u6e2f\u6fb3\u5927\u6e7e\u533a\u6821\u56ed\u62db\u8058",
                title="\u67d0\u79d1\u6280\u516c\u53f8 IPO \u5373\u5c06\u4e0a\u4f1a",
                summary="\u8d44\u672c\u5e02\u573a\u65b0\u95fb",
                url="https://example.com/news/1",
            )
        )
        self.assertFalse(
            crawler._career_candidate_is_relevant(
                keyword="\u7ca4\u6e2f\u6fb3\u5927\u6e7e\u533a\u6821\u56ed\u62db\u8058",
                title="\u5317\u4eac\u67d0\u516c\u53f8 2027 \u5c4a\u6821\u62db\u542f\u52a8",
                summary="\u9762\u5411\u5e94\u5c4a\u6bd5\u4e1a\u751f",
                url="https://example.com/jobs/2",
            )
        )
        self.assertTrue(
            crawler._career_candidate_is_relevant(
                keyword="\u7ca4\u6e2f\u6fb3\u5927\u6e7e\u533a\u6821\u56ed\u62db\u8058",
                title="\u6df1\u5733\u5e02\u5c5e\u56fd\u4f01 2027 \u5c4a\u6821\u56ed\u62db\u8058",
                summary="\u5e94\u5c4a\u6bd5\u4e1a\u751f\u53ef\u7f51\u4e0a\u6295\u9012",
                url="https://gzw.sz.gov.cn/jobs/3",
            )
        )

    def test_editorial_career_guard_requires_an_actionable_opportunity(self):
        self.assertFalse(
            crawler._has_actionable_career_signal(
                {
                    "title": "机器人公司准备 IPO，未来也许会创造就业机会",
                    "summary": "文章主要讨论融资、产业前景和潜在用人需求。",
                }
            )
        )
        self.assertTrue(
            crawler._has_actionable_career_signal(
                {
                    "title": "大湾区大学生实习计划开放报名",
                    "summary": "公告给出了实习岗位、报名入口和截止时间。",
                }
            )
        )

    def test_selection_prefers_regional_official_career_candidate(self):
        candidates = [
            {"id": 1, "section_key": "career", "score": 99, "title": "\u5168\u56fd 2027 \u5c4a\u6821\u62db", "url": "https://example.com/1"},
            {"id": 2, "section_key": "ai", "score": 95},
            {
                "id": 3,
                "section_key": "career",
                "score": 75,
                "keyword": "\u7ca4\u6e2f\u6fb3\u5927\u6e7e\u533a\u6821\u56ed\u62db\u8058",
                "title": "\u6df1\u5733\u5e02\u5c5e\u56fd\u4f01\u6821\u56ed\u62db\u8058",
                "summary": "\u5c97\u4f4d\u53ef\u5728\u5b98\u7f51\u6295\u9012",
                "url": "https://gzw.sz.gov.cn/jobs/3",
            },
        ]

        selected = crawler._balance_section_selection(candidates, candidates[:2], max_posts=2)

        self.assertEqual(3, selected[0]["id"])
        self.assertEqual("career", selected[0]["section_key"])

    def test_reused_unpublished_candidate_refreshes_stale_section(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE blog_news_crawler_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                section_key TEXT NOT NULL DEFAULT 'general',
                keyword TEXT NOT NULL,
                course_names_json TEXT NOT NULL DEFAULT '[]',
                source_name TEXT DEFAULT '',
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                canonical_url TEXT DEFAULT '',
                url_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                summary TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                fetched_at TEXT DEFAULT '',
                media_json TEXT NOT NULL DEFAULT '[]',
                score REAL NOT NULL DEFAULT 0,
                selected INTEGER NOT NULL DEFAULT 0,
                duplicate_of_item_id INTEGER,
                duplicate_of_post_id INTEGER,
                post_id INTEGER,
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        candidate = crawler.NewsCandidate(
            keyword="\u79d1\u6280\u521b\u65b0",
            course_names=["\u79d1\u6280\u524d\u6cbf"],
            source_name="Tech News",
            title="\u673a\u5668\u4eba\u4f01\u4e1a IPO \u8fdb\u5c55",
            url="https://example.com/ipo",
            canonical_url="https://example.com/ipo",
            summary="\u4e0a\u5e02\u5ba1\u6838\u59d4\u5458\u4f1a\u5c06\u5ba1\u8bae\u9996\u53d1\u4e8b\u9879",
            published_at="2026-07-15T10:00:00",
            fetched_at="2026-07-15T11:00:00",
            section_key="technology",
            score=80,
        )
        conn.execute(
            """
            INSERT INTO blog_news_crawler_items (
                run_id, section_key, keyword, source_name, title, url, canonical_url,
                url_hash, content_hash, summary, published_at, fetched_at, score, raw_json
            ) VALUES (1, 'career', 'old keyword', 'Old Source', 'Old title', ?, ?, ?, ?,
                      'Old summary', '', '', 1, '{}')
            """,
            (candidate.url, candidate.canonical_url, candidate.url_hash, candidate.content_hash),
        )

        with patch.object(crawler, "get_configured_db_engine", return_value="sqlite"):
            stored, duplicate_count = crawler._store_candidates(conn, 2, [candidate])

        self.assertEqual(0, duplicate_count)
        self.assertEqual(1, len(stored))
        self.assertEqual("technology", stored[0]["section_key"])
        row = conn.execute("SELECT section_key, keyword, title FROM blog_news_crawler_items").fetchone()
        self.assertEqual("technology", row["section_key"])
        self.assertEqual(candidate.keyword, row["keyword"])
        self.assertEqual(candidate.title, row["title"])
        conn.close()

    def test_section_management_is_extensible_and_preserves_disabled_sections(self):
        created = blog_section_service.save_blog_section(
            self.conn,
            {
                "section_key": "design",
                "name": "设计与创意",
                "short_name": "设计",
                "description": "视觉、产品与服务设计。",
                "icon": "D",
                "accent_color": "#db2777",
                "sort_order": 70,
                "source_keywords": ["产品设计", "用户体验"],
                "source_templates": [{"name": "Design RSS", "url": "https://example.com/design.xml", "kind": "fixed_rss"}],
            },
        )
        self.assertEqual("design", created["section_key"])
        self.assertEqual(["产品设计", "用户体验"], created["source_keywords"])

        disabled = blog_section_service.save_blog_section(
            self.conn,
            {**created, "is_enabled": False},
            section_key="design",
        )
        self.assertFalse(disabled["is_enabled"])
        self.assertNotIn("design", [item["section_key"] for item in blog_section_service.list_blog_sections(self.conn)])
        self.assertIn(
            "design",
            [item["section_key"] for item in blog_section_service.list_blog_sections(self.conn, include_disabled=True)],
        )

    def test_default_section_cannot_be_disabled(self):
        general = next(
            item
            for item in blog_section_service.list_blog_sections(self.conn, include_source_config=True)
            if item["section_key"] == "general"
        )
        with self.assertRaisesRegex(ValueError, "默认杂谈板块不能停用"):
            blog_section_service.save_blog_section(
                self.conn,
                {**general, "is_enabled": False},
                section_key="general",
            )


if __name__ == "__main__":
    unittest.main()
